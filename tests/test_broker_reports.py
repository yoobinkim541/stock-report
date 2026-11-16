"""증권사 채널 리포트 PDF 수집: 허용 호스트, 형식 확인, 중복 방지. 네트워크는 쓰지 않는다."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from reports import broker_reports, source_publishers
from reports import source_collector as sc


class _Response:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code, self.headers, self._body = status, headers or {}, body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=65536):
        yield self._body


def _web(pages: dict[str, _Response]):
    calls: list[str] = []

    def get(url, **kwargs):
        assert kwargs["allow_redirects"] is False
        calls.append(url)
        return pages[url]
    return get, calls


PDF = b"%PDF-1.7 fake"
SHORT, REPORT = "https://buly.kr/abc", "https://bbn.kiwoom.com/rfSD1.pdf"


def test_same_house_is_one_publisher_across_channel_and_report():
    channel, report = source_publishers.publisher("telegram:kwusa"), source_publishers.publisher("broker_report:KWUSA")
    assert channel == report == {"id": "키움증권", "name": "키움증권", "role": "sell_side"}
    assert source_publishers.publisher("telegram:insidertracking")["role"] == "community"
    assert source_publishers.publisher("telegram:new_channel") == {
        "id": "telegram:new_channel", "name": "new_channel", "role": "community"}
    assert source_publishers.publisher(None)["role"] == "other"
    assert "kwusa" in source_publishers.sell_side_channels()
    assert "insidertracking" not in source_publishers.sell_side_channels()


def test_fetch_follows_a_shortener_only_into_an_allowed_report_host():
    get, calls = _web({SHORT: _Response(302, {"Location": REPORT}), REPORT: _Response(body=PDF)})
    assert broker_reports.fetch_pdf(SHORT, get=get) == (PDF, REPORT, "ok")

    get, calls = _web({SHORT: _Response(302, {"Location": "https://evil.example/x.pdf"})})
    assert broker_reports.fetch_pdf(SHORT, get=get) == (None, "https://evil.example/x.pdf", "host_not_allowed")
    assert calls == [SHORT]          # 허용 목록 밖 주소에는 요청을 보내지 않는다

    get, calls = _web({})
    assert broker_reports.fetch_pdf("https://evil.example/x.pdf", get=get)[2] == "host_not_allowed" and calls == []


@pytest.mark.parametrize(("response", "url", "reason"), [
    (_Response(body=b"<html>login</html>"), REPORT, "not_pdf"),
    (_Response(404), REPORT, "http_404"),
    (_Response(headers={"Content-Length": str(broker_reports.MAX_BYTES + 1)}, body=PDF), REPORT, "too_large"),
    (_Response(body=PDF), SHORT, "not_a_report_host"),     # 단축 주소가 파일을 직접 주면 받지 않는다
])
def test_fetch_rejects_what_is_not_a_report_pdf(response, url, reason):
    get, _calls = _web({url: response})
    assert broker_reports.fetch_pdf(url, get=get) == (None, url, reason)


def test_report_becomes_one_research_event_and_is_not_fetched_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    post = {"title": "[키움증권] 코어위브 (CRWV.US) 기업리포트", "url": "https://t.me/kwusa/10",
            "body_raw": "코어위브 (CRWV.US) 기업리포트", "links": [REPORT, "https://youtu.be/x"]}
    asked: list[str] = []
    monkeypatch.setattr(sc, "_telegram_direct_messages", lambda channel: asked.append(channel) or [post])
    monkeypatch.setattr(broker_reports, "pdf_text", lambda data: ("엔비디아 수요가 견조하다. " * 60, 12))
    get, calls = _web({REPORT: _Response(body=PDF)})
    now = datetime(2026, 11, 16, 9, 0, tzinfo=sc.KST)
    cache = tmp_path / "cache"

    events = broker_reports.fetch_broker_report_events(
        ["insidertracking", "kwusa"], cache_dir=cache, limit=4, now=now, get=get)

    assert asked == ["kwusa"] and calls == [REPORT]      # 커뮤니티 채널과 허용 목록 밖 링크는 건드리지 않는다
    (event,) = events
    assert (event["source"], event["url"], event["source_url"]) == ("broker_report:kwusa", REPORT, "https://t.me/kwusa/10")
    assert (event["source_family"], event["classification"]["kind"], event["trust"]) == ("research", "report", "B")
    assert event["classification"]["publisher"] == "키움증권"
    assert event["tickers"][:2] == ["CRWV", "NVDA"]      # 채널 글의 표기와 본문 언급을 함께 읽는다
    assert (event["pages"], event["text_complete"], event["transport"]) == (12, True, "public_pdf")
    assert str(tmp_path) in event["raw_path"] and str(tmp_path) in event["text_path"]
    seen = json.loads((cache / broker_reports.SEEN_FILE).read_text(encoding="utf-8"))
    assert seen[REPORT]["status"] == "ok"

    again = broker_reports.fetch_broker_report_events(["kwusa"], cache_dir=cache, limit=4, now=now, get=get)
    assert again == [] and calls == [REPORT]


def test_scanned_pdf_without_text_is_recorded_and_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(sc, "_telegram_direct_messages", lambda channel: [{"title": "리포트", "links": [REPORT]}])
    monkeypatch.setattr(broker_reports, "pdf_text", lambda data: ("", 3))
    get, _calls = _web({REPORT: _Response(body=PDF)})

    events = broker_reports.fetch_broker_report_events(["kwusa"], cache_dir=tmp_path, limit=4, get=get)

    assert events == []
    assert json.loads((tmp_path / broker_reports.SEEN_FILE).read_text(encoding="utf-8"))[REPORT]["status"] == "no_text"


def test_report_source_is_on_demand_so_quiet_days_are_not_outages():
    assert sc.source_collection_mode("broker_report:kwusa") == "on_demand"
    assert sc.source_collection_mode("telegram:kwusa") == "scheduled"


def test_pdf_text_drops_private_use_bullets_and_collapses_spaces(monkeypatch):
    import pypdf

    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class _Reader:
        def __init__(self, stream):
            self.pages = [_Page("\uf0a0 데이터센터   규제\n\n\uf06c"), _Page("둘째 쪽"), _Page("셋째 쪽")]

    monkeypatch.setattr(pypdf, "PdfReader", _Reader)

    assert broker_reports.pdf_text(b"%PDF-", max_pages=2) == ("데이터센터 규제\n\n둘째 쪽", 3)
