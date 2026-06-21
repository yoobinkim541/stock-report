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
