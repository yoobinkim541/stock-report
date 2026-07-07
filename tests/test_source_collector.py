import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import source_collector as sc

KST = timezone(timedelta(hours=9))


def test_event_id_prefers_url_and_dedupes_when_appending(tmp_path):
    cache_dir = tmp_path / "cache"
    now = datetime(2026, 6, 4, 10, 30, tzinfo=KST)
    events = [
        {"source": "saveticker", "title": "NVDA rallies", "url": "https://example.com/nvda"},
        {"source": "saveticker", "title": "NVDA rallies", "url": "https://example.com/nvda"},
    ]

    written = sc.append_events(events, cache_dir=cache_dir, now=now)

    assert written == 1
    rows = [json.loads(line) for line in (cache_dir / "events-2026-06-04.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["id"] == sc.event_id(events[0])
    assert rows[0]["collected_at"] == "2026-06-04T10:30:00+09:00"


def test_load_recent_events_reads_multiple_days_and_dedupes(tmp_path):
    cache_dir = tmp_path / "cache"
    now = datetime(2026, 6, 4, 8, 0, tzinfo=KST)
    sc.append_events([{"source": "arca", "title": "old", "url": "https://e/old"}], cache_dir=cache_dir, now=now - timedelta(days=2))
    sc.append_events([{"source": "arca", "title": "fresh", "url": "https://e/fresh"}], cache_dir=cache_dir, now=now - timedelta(hours=23))
    sc.append_events([{"source": "arca", "title": "fresh", "url": "https://e/fresh"}], cache_dir=cache_dir, now=now)

    events = sc.load_recent_events(cache_dir=cache_dir, now=now, hours=24)

    assert [e["title"] for e in events] == ["fresh"]


def test_build_digest_groups_by_source_and_limits_items():
    events = [
        {"source": "saveticker", "source_url": "https://saveticker.com/api", "title": "AI chip demand", "url": "https://e/1", "tickers": ["NVDA"]},
        {"source": "arca", "source_url": "https://arca.live/b/stock", "title": "환율 경계", "url": "https://e/2", "category": "📰뉴스"},
    ]

    digest = sc.build_digest(events, limit=5)

    assert "누적 수집 자료" in digest
    assert "saveticker 1건" in digest
    assert "arca 1건" in digest
    assert "신뢰 소스" in digest
    assert "https://saveticker.com/api" in digest
    assert "AI chip demand" in digest
    assert "NVDA" in digest


def test_build_digest_normalizes_dict_tickers_and_tags():
    events = [
        {
            "source": "saveticker",
            "source_url": "https://saveticker.com/api",
            "title": "Microsoft AI demand",
            "url": "https://e/msft",
            "tickers": [{"symbol": "MSFT", "name": "Microsoft Corporation"}],
            "tags": [{"name": "AI"}],
        }
    ]

    digest = sc.build_digest(events, limit=5)

    assert "반복 등장 종목: MSFT 1건" in digest
    assert "반복 테마: AI 1건" in digest
    assert "Microsoft AI demand · MSFT" in digest


def test_fetch_market_snapshot_events_includes_common_market_and_portfolio_data():
    import pandas as pd

    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, period, auto_adjust=True):
            assert period == "1y"
            return pd.DataFrame({"Close": list(range(100, 130))})

    class FakeYF:
        Ticker = FakeTicker

    events = sc.fetch_market_snapshot_events(yf_module=FakeYF)
    titles = "\n".join(e["title"] for e in events)

    assert len(events) >= 40
    assert "QQQ Nasdaq 100 ETF" in titles
    assert "XLK Technology ETF" in titles
    assert "HYG High-yield bond ETF" in titles
    assert "MSFT Portfolio holding MSFT" in titles
    assert all(e["source"] == "yahoo_finance" for e in events)


def test_fetch_fred_macro_events_parses_public_csv(monkeypatch):
    class FakeResponse:
        text = "observation_date,DGS10\n2026-06-01,4.10\n2026-06-02,.\n2026-06-03,4.15\n"

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(sc.requests, "get", fake_get)

    events = sc.fetch_fred_macro_events({"DGS10": "미국 10년 국채금리"})

    assert len(events) == 1
    assert calls[0][0] == "https://fred.stlouisfed.org/graph/fredgraph.csv"
    assert calls[0][1]["params"] == {"id": "DGS10"}
    assert events[0]["source"] == "fred"
    assert events[0]["source_url"] == "https://fred.stlouisfed.org"
    assert "DGS10 미국 10년 국채금리: 2026-06-03 4.15" in events[0]["title"]
    assert events[0]["metrics"] == {"series_id": "DGS10", "current": 4.15, "delta": 0.05}


def test_fred_series_includes_common_treasury_maturities():
    assert "DGS5" in sc.FRED_SERIES
    assert "DGS10" in sc.FRED_SERIES
    assert "DGS20" in sc.FRED_SERIES
    assert "DGS30" in sc.FRED_SERIES


def test_parse_world_gov_bonds_common_maturities():
    markdown = """
|  | [5 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/5-years/) | 4.163% | +7.8 bp |
|  | [10 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/10-years/) | 4.457% | +1.4 bp |
|  | [20 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/20-years/) | 4.969% | -5.0 bp |
|  | [30 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/30-years/) | 4.966% | -5.3 bp |
"""

    yields = sc._parse_yields_from_world_gov_bonds(markdown, maturities=(5, 10, 20, 30))

    assert yields == {
        "5Y": 4.163,
        "10Y": 4.457,
        "20Y": 4.969,
        "30Y": 4.966,
    }


def test_fetch_world_gov_bond_events_emits_common_maturities(monkeypatch):
    # _bounded_get(stream=True) 인터페이스에 맞춘 스트리밍 컨텍스트매니저 fake
    _MD = """
|  | [5 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/5-years/) | 4.163% | +7.8 bp |
|  | [10 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/10-years/) | 4.457% | +1.4 bp |
|  | [20 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/20-years/) | 4.969% | -5.0 bp |
|  | [30 years](https://www.worldgovernmentbonds.com/bond-historical-data/united-states/30-years/) | 4.966% | -5.3 bp |
"""

    class FakeResponse:
        headers: dict = {}
        encoding = "utf-8"

        def raise_for_status(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, chunk_size=65536):
            yield _MD.encode("utf-8")

    monkeypatch.setattr(sc.requests, "get", lambda *args, **kwargs: FakeResponse())

    events = sc.fetch_world_gov_bond_events({"united-states": "미국 국채금리"})

    assert [event["metrics"]["maturity"] for event in events] == ["5Y", "10Y", "20Y", "30Y"]
    assert "미국 국채금리 5Y: 4.163%" in events[0]["title"]
    assert "미국 국채금리 30Y: 4.966%" in events[-1]["title"]
    # 각 만기가 고유 URL fragment를 가져 append_events 중복 제거에 걸리지 않아야 함
    urls = [event["url"] for event in events]
    assert len(set(urls)) == 4
    assert "#30Y" in urls[-1]


def test_normalize_tickers_extracts_symbols_from_dicts():
    # SaveTicker 가 tickers 를 dict 리스트로 줄 때 symbol 만 추출
    assert sc._normalize_tickers([{"id": 17135, "name": None, "symbol": "SPCX"}]) == ["SPCX"]
    assert sc._normalize_tickers(["NVDA", {"symbol": "MSFT"}, {"name": "심볼없음"}]) == ["NVDA", "MSFT"]
    assert sc._normalize_tickers(None) == []
    assert sc._normalize_tickers([]) == []
    assert sc._normalize_tickers([" AMD ", ""]) == ["AMD"]


def test_build_digest_survives_dict_tickers_in_corrupt_cache():
    # 손상 캐시(tickers/tags 가 dict 리스트)여도 build_digest 가 크래시하지 않아야 함.
    # 회귀: 예전엔 Counter / ", ".join 에서 TypeError: unhashable type: 'dict'
    events = [
        {"source": "saveticker", "source_url": "https://saveticker.com/api",
         "title": "스페이스X 급등", "url": "https://e/1",
         "tickers": [{"id": 17135, "name": None, "symbol": "SPCX"}],
         "tags": [{"id": 9, "name": "우주"}]},
        {"source": "saveticker", "source_url": "https://saveticker.com/api",
         "title": "정상 이벤트", "url": "https://e/2", "tickers": ["NVDA"]},
    ]

    digest = sc.build_digest(events, limit=5)

    assert "누적 수집 자료" in digest
    assert "NVDA" in digest            # 정상 str 티커는 집계됨
    assert "스페이스X 급등" in digest   # dict 티커 이벤트도 크래시 없이 항목으로 표시


def test_fetch_saveticker_events_normalizes_dict_tickers(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    payload = {"news_list": [{
        "title": "스페이스X 상장 급등",
        "url": "https://e/spcx",
        "created_at": "2026-06-16",
        "tickers": [{"id": 17135, "name": None, "symbol": "SPCX"}],
        "tag_names": ["우주"],
    }]}

    monkeypatch.setattr(sc.requests, "get", lambda *args, **kwargs: FakeResponse(payload))

    events = sc.fetch_saveticker_events()

    assert events, "이벤트가 비어있으면 안 됨"
    assert events[0]["tickers"] == ["SPCX"]   # dict → symbol 문자열로 정규화
    assert all(isinstance(t, str) for t in events[0]["tickers"])


# ── 소스별 수집 헬스 (수집 공백 가시화) ──────────────────────────────────────

def test_update_and_load_source_health_roundtrip(tmp_path):
    cache = tmp_path / "cache"
    now = datetime(2026, 7, 7, 10, 0, tzinfo=KST)
    events = [{"source": "saveticker", "title": "a"},
              {"source": "saveticker", "title": "b"},
              {"source": "telegram:yuzukinaok1", "title": "c"}]
    h = sc.update_source_health(events, cache_dir=cache, now=now)
    assert h["saveticker"]["last_count"] == 2
    assert h["saveticker"]["last_success"].startswith("2026-07-07")
    # 수집 0건 소스: last_run 은 찍히고 last_success 는 없음 (공백 측정 기준점)
    assert h["telegram:insidertracking"]["last_count"] == 0
    assert "last_success" not in h["telegram:insidertracking"]
    assert sc.load_source_health(cache) == h


def test_source_health_preserves_last_success_across_gap(tmp_path):
    cache = tmp_path / "cache"
    t1 = datetime(2026, 7, 6, 10, 0, tzinfo=KST)
    t2 = datetime(2026, 7, 7, 10, 0, tzinfo=KST)
    sc.update_source_health([{"source": "fred", "title": "x"}], cache_dir=cache, now=t1)
    h = sc.update_source_health([], cache_dir=cache, now=t2)     # 다음 수집은 전부 0건
    assert h["fred"]["last_count"] == 0
    assert h["fred"]["last_success"].startswith("2026-07-06")    # 성공 시각 보존


def test_stale_sources_flags_gap_and_never_succeeded():
    now = datetime(2026, 7, 7, 12, 0, tzinfo=KST)
    health = {
        "saveticker": {"last_success": "2026-07-07T11:40:00+09:00"},        # 20분 전 — 정상
        "fred": {"last_success": "2026-07-03T10:00:00+09:00"},              # 98h — 임계 72h 초과
        "telegram:insidertracking": {"last_run": "2026-07-07T11:40:00+09:00"},  # 성공 이력 없음
        "telegram:yuzukinaok1": {"last_success": "2026-07-07T05:00:00+09:00"},  # 7h — 임계 12h 이내
    }
    bad = {s["source"]: s for s in sc.stale_sources(health, now=now)}
    assert "saveticker" not in bad and "telegram:yuzukinaok1" not in bad
    assert bad["fred"]["hours"] > 72
    assert bad["telegram:insidertracking"]["hours"] is None      # 이력 없음 = 최우선 점검


def test_stale_sources_empty_health_silent():
    assert sc.stale_sources({}, now=datetime.now(KST)) == []


def test_telegram_titles_from_html_parses_widget_text():
    html = '''
    <div class="tgme_widget_message_text js-message_text" dir="auto">
      삼성전자, <b>HBM4</b> 공급 계약 체결&amp;확대<br/>관련 종목 주목
    </div>
    <a class="tgme_widget_message_date" href="https://t.me/insidertracking/123"><time></time></a>
    <div class="tgme_widget_message_text js-message_text" dir="auto">두번째 메시지</div>
    <a class="tgme_widget_message_date" href="https://t.me/insidertracking/124"><time></time></a>
    '''
    titles, urls = sc._telegram_titles_from_html(html, "insidertracking")
    assert titles[0] == "삼성전자, HBM4 공급 계약 체결&확대 관련 종목 주목"   # 태그 제거·unescape·공백 정리
    assert titles[1] == "두번째 메시지"
    assert urls == ["https://t.me/insidertracking/123", "https://t.me/insidertracking/124"]


def test_fetch_telegram_falls_back_to_direct_html(monkeypatch):
    """jina 가 bold 없는 마크다운(제목 0건)을 줘도 직접 HTML 폴백으로 수집."""
    calls = []

    class _Resp:
        def __init__(self, text):
            self.text = text

    def fake_get(url, timeout=0):
        calls.append(url)
        if url.startswith("https://r.jina.ai/"):
            return _Resp("plain markdown without bold titles")
        return _Resp('<div class="tgme_widget_message_text">연준 금리 동결 시사</div>'
                     '<a href="https://t.me/chanx/9"></a>')

    monkeypatch.setattr(sc, "_bounded_get", fake_get)
    events = sc.fetch_telegram_channel_events(["chanx"])
    assert len(events) == 1
    assert events[0]["title"] == "연준 금리 동결 시사"
    assert events[0]["url"] == "https://t.me/chanx/9"
    assert any(u.startswith("https://t.me/s/chanx") for u in calls)   # 폴백 경로 사용됨


def test_collect_once_isolates_source_crash(tmp_path, monkeypatch):
    """한 소스 fetcher 가 크래시해도 나머지 수집 + 헬스 기록은 계속."""
    monkeypatch.setattr(sc, "fetch_saveticker_events",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sc, "fetch_arca_events", lambda max_pages=2: [])
    monkeypatch.setattr(sc, "fetch_telegram_channel_events",
                        lambda: [{"source": "telegram:yuzukinaok1", "title": "t", "url": "https://t.me/y/1"}])
    monkeypatch.setattr(sc, "fetch_market_snapshot_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_fred_macro_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_world_gov_bond_events", lambda: [])
    fetched, written = sc.collect_once(cache_dir=tmp_path / "cache")
    assert fetched == 1 and written == 1
    h = sc.load_source_health(tmp_path / "cache")
    assert h["saveticker"]["last_count"] == 0                     # 크래시 소스 = 0건 기록
    assert h["telegram:yuzukinaok1"]["last_count"] == 1


# ── 죽은 출처 복구 — 직접 폴백·FRED API·오류 원인 기록 ───────────────────────

def test_parse_arca_html():
    html = '''
    <a class="title" href="/b/stock/172799906?p=1"><span>🧠분석</span> 로마 시대의 지중해 무역</a>
    <a class="title" href="/b/stock/172799907?p=1">잡담 글은 라벨 없음</a>
    <a class="title" href="/b/stock/172799906?p=1">중복 id</a>
    '''
    rows = sc._parse_arca_html(html)
    assert rows[0][0] == "172799906" and "지중해 무역" in rows[0][1]
    assert len(rows) == 2                                     # 중복 id 제거


def test_fetch_arca_falls_back_to_direct(monkeypatch):
    def fake_get(url, timeout=0):
        if url.startswith("https://r.jina.ai/"):
            raise RuntimeError("429 rate limited")
        class R:
            text = '<a href="/b/stock/111?p=1">📰뉴스 반도체 수출 규제</a>'
        return R()
    monkeypatch.setattr(sc, "_bounded_get", fake_get)
    events = sc.fetch_arca_events(max_pages=1)
    assert len(events) == 1 and events[0]["url"].endswith("/111")
    assert events[0]["category"] == "📰뉴스"


def test_parse_wgb_html():
    html = '''
    <tr><td><a href="/x">5 years</a></td><td>3.456%</td></tr>
    <tr><td><a href="/y">10 years</a></td><td class="w3">4.395 %</td></tr>
    <tr><td>비관련 99.9%</td></tr>
    '''
    y = sc._parse_yields_from_wgb_html(html)
    assert y == {"5Y": 3.456, "10Y": 4.395}


def test_fetch_wgb_falls_back_to_direct(monkeypatch):
    def fake_get(url, timeout=0):
        if url.startswith("https://r.jina.ai/"):
            raise RuntimeError("boom")
        class R:
            text = '<tr><td><a>10 years</a></td><td>4.100%</td></tr>'
        return R()
    monkeypatch.setattr(sc, "_bounded_get", fake_get)
    events = sc.fetch_world_gov_bond_events({"united-states": "미국 국채금리"})
    assert len(events) == 1 and events[0]["metrics"]["yield_pct"] == 4.1


def test_fred_api_fallback(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "testkey")
    class R:
        def raise_for_status(self): pass
        def json(self):
            return {"observations": [
                {"date": "2026-07-04", "value": "4.35"},
                {"date": "2026-07-03", "value": "."},
                {"date": "2026-07-02", "value": "4.30"}]}
    monkeypatch.setattr(sc.requests, "get", lambda *a, **k: R())
    latest, prev = sc._fred_api_latest("DGS10")
    assert latest == ("2026-07-04", "4.35")
    assert prev == ("2026-07-02", "4.30")                     # '.' 결측 건너뜀


def test_fred_api_fallback_no_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    assert sc._fred_api_latest("DGS10") == (None, None)


def test_health_records_error_and_grace(tmp_path):
    cache = tmp_path / "cache"
    t0 = datetime(2026, 7, 7, 10, 0, tzinfo=KST)
    sc._LAST_ERRORS.clear()
    sc._note_error("fred", "fredgraph.csv: 403 Forbidden")
    h = sc.update_source_health([], cache_dir=cache, now=t0)
    assert h["fred"]["last_error"].startswith("fredgraph.csv")
    assert h["fred"]["first_run"].startswith("2026-07-07")

    # grace: 첫 기록 2h 후 — 무성공이어도 아직 경보 아님 (fred grace = min(72,6)=6h)
    bad_2h = {s["source"]: s for s in sc.stale_sources(h, now=t0 + timedelta(hours=2))}
    assert "fred" not in bad_2h
    # 7h 후 — grace 초과 → 경보 + 원인 포함
    bad_7h = {s["source"]: s for s in sc.stale_sources(h, now=t0 + timedelta(hours=7))}
    assert "fred" in bad_7h and bad_7h["fred"]["error"].startswith("fredgraph.csv")

    # 성공하면 오류 제거
    sc._LAST_ERRORS.pop("fred", None)
    h2 = sc.update_source_health([{"source": "fred", "title": "x"}], cache_dir=cache,
                                 now=t0 + timedelta(hours=8))
    assert "last_error" not in h2["fred"] and h2["fred"]["first_run"] == h["fred"]["first_run"]
