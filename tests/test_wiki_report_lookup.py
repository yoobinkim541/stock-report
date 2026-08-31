from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def _page(wiki, *, title, body, tags, kind="risk", status="reviewed"):
    return wiki.upsert_page({
        "title": title,
        "summary": body,
        "body": body,
        "surface": "ticker",
        "kind": kind,
        "status": status,
        "tags": tags,
        "source_refs": ["https://example.com/source"],
        "report_citation": "리포트에서 바로 인용할 한 줄",
    })


def test_report_lookup_matches_exact_ticker_and_excludes_unrelated_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    msft = _page(wiki, title="MSFT 클라우드 마진 리스크", body="MSFT 마진을 확인한다.", tags=["ticker:MSFT"])
    _page(wiki, title="MSFTX 유사 문자열", body="MSFTX만 언급한다.", tags=["ticker:MSFTX"])
    _page(wiki, title="시장 금리 리스크", body="금리 위험", tags=["ticker:NVDA"])
    _page(wiki, title="보관된 MSFT 리스크", body="MSFT 과거 위험", tags=["ticker:MSFT"], status="archived")

    matches = wiki.for_report_targets(["MSFT"])

    assert [page["id"] for page in matches["MSFT"]] == [msft["id"]]
    assert matches["MSFT"][0]["report_citation"] == "리포트에서 바로 인용할 한 줄"


def test_report_lookup_supports_scope_topics_without_broad_top_n(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    target = _page(wiki, title="반도체 공급망 리스크", body="공급망 병목이 핵심이다.", tags=["topic:공급망"])
    _page(wiki, title="금리 리스크", body="할인율 위험", tags=["topic:금리"])

    matches = wiki.for_report_targets([], topics=["공급망"], surface="ticker")

    assert [page["id"] for page in matches["공급망"]] == [target["id"]]
