import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reports import source_collector as sc
import pytest

KST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def isolate_provider_error_state(monkeypatch):
    monkeypatch.setattr(sc, "_LAST_ERRORS", {})
    monkeypatch.setattr(sc, "_SOURCE_AVAILABILITY", {})


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


def test_load_recent_events_limit_reads_newest_tail_without_full_file_scan(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    event_file = cache_dir / "events-2026-06-04.jsonl"
    cache_dir.mkdir()
    now = datetime(2026, 6, 4, 8, 0, tzinfo=KST)
    rows = [
        {
            "id": f"old-{idx}",
            "source": "saveticker",
            "title": f"old-{idx}",
            "collected_at": (now - timedelta(minutes=2010 - idx)).isoformat(),
        }
        for idx in range(2000)
    ]
    rows.extend(
        {
            "id": f"new-{idx}",
            "source": "saveticker",
            "title": f"new-{idx}",
            "collected_at": (now - timedelta(seconds=5 - idx)).isoformat(),
        }
        for idx in range(5)
    )
    event_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    original_read_text = Path.read_text

    def reject_full_read(path, *args, **kwargs):
        if path == event_file:
            raise AssertionError("recent event lookup must not read the whole event file")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_full_read)

    events = sc.load_recent_events(cache_dir=cache_dir, now=now, hours=1, limit=3)

    assert [event["title"] for event in events] == ["new-2", "new-3", "new-4"]


def test_append_events_preserves_mutable_observations_across_time_buckets(tmp_path):
    cache_dir = tmp_path / "cache"
    first_at = datetime(2026, 8, 21, 10, 5, tzinfo=KST)
    event = {
        "source": "yahoo_finance",
        "type": "market_snapshot",
        "title": "QQQ snapshot",
        "url": "https://finance.yahoo.com/quote/QQQ",
        "metrics": {"ticker": "QQQ", "current": 600.0},
    }

    assert sc.append_events([event], cache_dir=cache_dir, now=first_at) == 1
    assert sc.append_events([event], cache_dir=cache_dir, now=first_at + timedelta(minutes=20)) == 0
    assert sc.append_events([event], cache_dir=cache_dir, now=first_at + timedelta(minutes=30)) == 1

    rows = sc.load_recent_events(cache_dir=cache_dir, now=first_at + timedelta(minutes=35), hours=2)
    assert len(rows) == 2
    assert rows[0]["entity_id"] == rows[1]["entity_id"] == "yahoo_finance:QQQ"
    assert rows[0]["id"] != rows[1]["id"]


def test_append_events_accounts_for_duplicates_and_identity_collisions(tmp_path):
    from reports import source_collector as sc
    from reports.source_identity import normalize_event_identity

    now = datetime(2026, 8, 21, 10, 5, tzinfo=KST)
    event = {
        "source": "kalshi",
        "type": "prediction_market",
        "entity_id": "kalshi:market-1",
        "observed_at": now.isoformat(),
    }
    cache = tmp_path / "duplicates"

    assert sc.append_events([event], cache_dir=cache, now=now) == 1
    assert sc.append_events([event], cache_dir=cache, now=now) == 0
    duplicate_stats = sc._LAST_APPEND_STATS["kalshi"]
    assert duplicate_stats["fetched"] == 1
    assert duplicate_stats["persisted"] == 0
    assert duplicate_stats["deduped"] == 1
    assert duplicate_stats["collisions"] == 0

    collision_cache = tmp_path / "collisions"
    normalized = normalize_event_identity(event, now)
    saved = dict(normalized)
    saved["entity_id"] = "kalshi:legacy-market"
    event_file = collision_cache / "events-2026-08-21.jsonl"
    collision_cache.mkdir(parents=True)
    event_file.write_text(json.dumps(saved) + "\n", encoding="utf-8")

    assert sc.append_events([event], cache_dir=collision_cache, now=now) == 0
    collision_stats = sc._LAST_APPEND_STATS["kalshi"]
    assert collision_stats["deduped"] == 0
    assert collision_stats["collisions"] == 1


def test_append_events_keeps_legacy_same_id_as_duplicate(tmp_path):
    from reports import source_collector as sc
    from reports.source_identity import normalize_event_identity

    now = datetime(2026, 8, 21, 10, 5, tzinfo=KST)
    event = {
        "source": "kalshi",
        "type": "prediction_market",
        "entity_id": "kalshi:market-legacy",
        "observed_at": now.isoformat(),
    }
    normalized = normalize_event_identity(event, now)
    legacy = dict(normalized)
    for key in ("record_kind", "entity_id", "observation_bucket", "content_id"):
        legacy.pop(key, None)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "events-2026-08-21.jsonl").write_text(
        json.dumps(legacy) + "\n", encoding="utf-8"
    )

    assert sc.append_events([event], cache_dir=cache, now=now) == 0
    stats = sc._LAST_APPEND_STATS["kalshi"]
    assert stats["deduped"] == 1
    assert stats["collisions"] == 0


def test_build_digest_groups_by_source_and_limits_items():
    events = [
        {"source": "saveticker", "source_url": "https://saveticker.com/api", "title": "AI chip demand", "url": "https://e/1", "tickers": ["NVDA"], "classification": {"kind": "article", "topic": "기술/AI", "trust": "B"}},
        {"source": "arca", "source_url": "https://arca.live/b/stock", "title": "환율 경계", "url": "https://e/2", "category": "📰뉴스", "classification": {"kind": "news", "topic": "정책/재정", "trust": "C"}},
    ]

    digest = sc.build_digest(events, limit=5)

    assert "누적 수집 자료" in digest
    assert "saveticker 1건" in digest
    assert "arca 1건" in digest
    assert "반복 분류" in digest
    assert "반복 주제" in digest
    assert "신뢰 등급" in digest
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
    assert events[0]["classification"]["source_family"] == "market_data"
    assert "QQQ Nasdaq 100 ETF" in titles
    assert "XLK Technology ETF" in titles
    assert "HYG High-yield bond ETF" in titles
    assert "MSFT Portfolio holding MSFT" in titles
    assert all(e["source"] == "yahoo_finance" for e in events)


def test_fetch_market_snapshot_events_uses_one_batch_download(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(sc, "MARKET_TICKERS", {"QQQ": "Nasdaq 100 ETF", "SPY": "S&P 500 ETF"})
    dates = pd.date_range("2026-07-01", periods=30, freq="D")
    frame = pd.DataFrame(
        {
            ("Close", "QQQ"): [100 + i for i in range(30)],
            ("Close", "SPY"): [200 + i for i in range(30)],
        },
        index=dates,
    )
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)

    class FakeYF:
        calls = []

        @classmethod
        def download(cls, tickers, **kwargs):
            cls.calls.append((tickers, kwargs))
            return frame

        class Ticker:
            def __init__(self, _ticker):
                raise AssertionError("complete batch response must not use per-ticker fallback")

    events = sc.fetch_market_snapshot_events(yf_module=FakeYF)

    assert len(events) == 2
    assert len(FakeYF.calls) == 1
    assert FakeYF.calls[0][0] == ["QQQ", "SPY"]
    assert FakeYF.calls[0][1]["period"] == "1y"
    assert {event["metrics"]["ticker"] for event in events} == {"QQQ", "SPY"}
    assert all(event["record_kind"] == "observation" for event in events)


def test_fetch_market_snapshot_events_falls_back_only_for_missing_batch_symbol(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(sc, "MARKET_TICKERS", {"QQQ": "Nasdaq 100 ETF", "SPY": "S&P 500 ETF"})
    frame = pd.DataFrame({("Close", "QQQ"): [100 + i for i in range(30)]})
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    fallbacks = []

    class FakeYF:
        @staticmethod
        def download(_tickers, **_kwargs):
            return frame

        class Ticker:
            def __init__(self, ticker):
                fallbacks.append(ticker)

            def history(self, period, auto_adjust=True):
                assert period == "1y"
                return pd.DataFrame({"Close": [200 + i for i in range(30)]})

    events = sc.fetch_market_snapshot_events(yf_module=FakeYF)

    assert len(events) == 2
    assert fallbacks == ["SPY"]


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


def test_fetch_world_gov_bond_events_emits_country_maturity_entity_ids(monkeypatch):
    markdown = "|  | [10 years](https://example.test/10-years/) | 4.163% | +7.8 bp |"

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
            yield markdown.encode("utf-8")

    monkeypatch.setattr(sc.requests, "get", lambda *args, **kwargs: FakeResponse())

    events = sc.fetch_world_gov_bond_events({
        "united-states": "미국 국채금리",
        "japan": "일본 국채금리",
    })

    entity_ids = {event["entity_id"] for event in events}
    assert entity_ids == {
        "worldgovernmentbonds:united-states:10y",
        "worldgovernmentbonds:japan:10y",
    }


def test_fetch_polymarket_events_normalizes_gamma_markets(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "id": "100",
                    "slug": "fed-decision-september",
                    "title": "Fed decision in September?",
                    "category": "Economics",
                    "volume": "900000",
                    "liquidity": "25000",
                    "openInterest": "120000",
                    "endDate": "2026-09-16T00:00:00Z",
                    "active": True,
                    "closed": False,
                    "tags": [{"label": "Fed"}, {"label": "Rates"}],
                    "markets": [
                        {
                            "id": "200",
                            "slug": "fed-cut-in-september",
                            "question": "Will the Fed cut rates in September?",
                            "outcomes": '["Yes","No"]',
                            "outcomePrices": '["0.63","0.37"]',
                            "volume": "640000",
                            "liquidity": "18000",
                            "openInterest": "70000",
                            "endDate": "2026-09-16T00:00:00Z",
                            "active": True,
                            "closed": False,
                        }
                    ],
                }
            ]

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(sc.requests, "get", fake_get)

    events = sc.fetch_polymarket_events(limit=5, min_volume=1000, keywords=["Fed"])

    assert calls[0][0] == "https://gamma-api.polymarket.com/events"
    assert calls[0][1]["params"]["active"] is True
    assert len(events) == 1
    event = events[0]
    assert event["source"] == "polymarket"
    assert event["source_url"] == "https://gamma-api.polymarket.com"
    assert event["url"] == "https://polymarket.com/event/fed-decision-september"
    assert "63.0%" in event["title"]
    assert event["metrics"]["yes_probability"] == 0.63
    assert event["metrics"]["volume"] == 640000.0
    assert event["metrics"]["liquidity"] == 18000.0
    assert event["classification"]["source_family"] == "prediction_market"
    assert event["classification"]["kind"] == "prediction_market"
    assert event["classification"]["topic"] == "Fed"
    assert event["wiki_eligible"] is True
    assert event["raw_payload"]["event_id"] == "100"


def test_polymarket_451_is_recorded_as_region_blocked_not_stale(monkeypatch, tmp_path):
    class BlockedResponse:
        status_code = 451

        def raise_for_status(self):
            error = sc.requests.HTTPError("451 Client Error: Unavailable For Legal Reasons")
            error.response = self
            raise error

    monkeypatch.setattr(sc, "_SOURCE_AVAILABILITY", {})
    monkeypatch.setattr(sc, "_LAST_ERRORS", {})
    monkeypatch.setattr(sc.requests, "get", lambda *args, **kwargs: BlockedResponse())

    now = datetime(2026, 8, 21, 11, 0, tzinfo=KST)
    assert sc.fetch_polymarket_events() == []
    health = sc.update_source_health([], cache_dir=tmp_path / "cache", now=now)

    assert health["polymarket"]["availability"] == "blocked"
    assert "HTTP 451" in health["polymarket"]["availability_reason"]
    stale = sc.stale_sources(health, now=now + timedelta(hours=24))
    assert "polymarket" not in {row["source"] for row in stale}


def test_polymarket_payload_direct_success_does_not_call_relay(monkeypatch):
    class DirectResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": "direct-1"}]

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return DirectResponse()

    monkeypatch.setenv("POLYMARKET_RELAY_URL", "https://relay.example/api/events")
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-token")

    payload, meta = sc.fetch_polymarket_payload(request_limit=80, get=fake_get)

    assert payload == [{"id": "direct-1"}]
    assert meta["transport"] == "direct"
    assert meta["source_url"] == "https://gamma-api.polymarket.com"
    assert [call[0] for call in calls] == ["https://gamma-api.polymarket.com/events"]


def test_polymarket_payload_uses_authenticated_relay_only_after_direct_451(monkeypatch):
    class Response:
        def __init__(self, payload=None, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                error = sc.requests.HTTPError(f"{self.status_code} upstream")
                error.response = self
                raise error

        def json(self):
            return self._payload

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url == "https://gamma-api.polymarket.com/events":
            return Response(status_code=451)
        return Response({
            "ok": True,
            "transport": "polymarket-relay",
            "source_url": "https://gamma-api.polymarket.com/events",
            "retrieved_at": "2026-08-21T14:00:00+00:00",
            "events": [{"id": "relay-1"}],
        })

    monkeypatch.setenv("POLYMARKET_RELAY_URL", "https://relay.example/api/events")
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-token")

    payload, meta = sc.fetch_polymarket_payload(request_limit=80, get=fake_get)

    assert payload == [{"id": "relay-1"}]
    assert meta["transport"] == "relay"
    assert meta["retrieved_at"] == "2026-08-21T14:00:00+00:00"
    assert calls[1][0] == "https://relay.example/api/events"
    assert calls[1][1]["headers"]["Authorization"] == "Bearer relay-token"


def test_polymarket_payload_relay_451_remains_explicitly_blocked(monkeypatch):
    class BlockedResponse:
        status_code = 451

        def raise_for_status(self):
            error = sc.requests.HTTPError("451 upstream")
            error.response = self
            raise error

        def json(self):
            return {
                "ok": False,
                "availability": "blocked",
                "upstream_status": 451,
                "events": [],
            }

    monkeypatch.setenv("POLYMARKET_RELAY_URL", "https://relay.example/api/events")
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-token")
    monkeypatch.setattr(sc, "_SOURCE_AVAILABILITY", {})
    monkeypatch.setattr(sc, "_LAST_ERRORS", {})

    try:
        sc.fetch_polymarket_payload(request_limit=80, get=lambda *_args, **_kwargs: BlockedResponse())
    except sc.PolymarketUnavailable as exc:
        assert exc.availability == "blocked"
        assert exc.status_code == 451
    else:
        raise AssertionError("relay 451 must raise PolymarketUnavailable")


def test_polymarket_payload_does_not_relay_transient_direct_error(monkeypatch):
    class ErrorResponse:
        status_code = 503

        def raise_for_status(self):
            error = sc.requests.HTTPError("503 upstream")
            error.response = self
            raise error

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return ErrorResponse()

    monkeypatch.setenv("POLYMARKET_RELAY_URL", "https://relay.example/api/events")
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-token")

    try:
        sc.fetch_polymarket_payload(request_limit=80, get=fake_get)
    except sc.PolymarketUnavailable as exc:
        assert exc.availability == "error"
        assert exc.status_code == 503
    else:
        raise AssertionError("direct 503 must raise PolymarketUnavailable")
    assert calls == ["https://gamma-api.polymarket.com/events"]


def test_polymarket_success_clears_region_blocked_state(monkeypatch, tmp_path):
    class EmptyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    monkeypatch.setattr(sc, "_SOURCE_AVAILABILITY", {
        "polymarket": {
            "availability": "blocked",
            "availability_reason": "HTTP 451",
        }
    })
    monkeypatch.setattr(sc, "_LAST_ERRORS", {"polymarket": "HTTP 451"})
    monkeypatch.setattr(sc.requests, "get", lambda *args, **kwargs: EmptyResponse())
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / sc.HEALTH_FILE).write_text(json.dumps({
        "polymarket": {
            "first_run": "2026-08-20T11:00:00+09:00",
            "availability": "blocked",
            "availability_reason": "HTTP 451",
            "last_error": "HTTP 451",
        }
    }), encoding="utf-8")

    assert sc.fetch_polymarket_events() == []
    health = sc.update_source_health([], cache_dir=cache)
    assert "polymarket" not in sc._SOURCE_AVAILABILITY
    assert "availability" not in health["polymarket"]
    assert "availability_reason" not in health["polymarket"]
    assert "last_error" not in health["polymarket"]


def test_expected_sources_include_polymarket():
    assert "polymarket" in sc.expected_sources()


def test_expected_sources_include_kalshi():
    assert "kalshi" in sc.expected_sources()


def test_rss_items_from_xml_reads_rss_and_atom():
    rss = """<rss><channel><item>
      <title>Fed holds rates</title>
      <link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20261027a.htm</link>
      <description>&lt;p&gt;The Committee decided to maintain the target range.&lt;/p&gt;</description>
      <pubDate>Mon, 27 Oct 2026 18:00:00 GMT</pubDate>
    </item></channel></rss>"""
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>8-K - EXAMPLE INC</title>
      <link href="https://www.sec.gov/Archives/edgar/data/1/0001.htm"/>
      <summary>Item 2.02 results</summary>
      <updated>2026-10-27T12:00:00-04:00</updated>
    </entry></feed>"""
    fed = sc.rss_items_from_xml(rss)
    filing = sc.rss_items_from_xml(atom)
    assert fed[0]["title"] == "Fed holds rates"
    assert fed[0]["url"].startswith("https://www.federalreserve.gov/")
    assert "target range" in fed[0]["body"]
    assert "<p>" not in fed[0]["body"]
    assert filing[0]["title"].startswith("8-K")
    assert filing[0]["url"].startswith("https://www.sec.gov/")


def test_expected_sources_include_public_rss():
    sources = sc.expected_sources()
    assert "rss:fed" in sources and "rss:yonhap" in sources and "rss:sec" in sources


def test_polymarket_keyword_matching_uses_word_boundaries():
    event = {"title": "Will Gedion Timothewos be the next Prime Minister of Ethiopia?", "category": "Politics"}
    market = {"question": "Will Gedion Timothewos be the next Prime Minister of Ethiopia?"}

    assert sc._polymarket_matches_keywords(event, market, ["ai"]) is False
    assert sc._polymarket_matches_keywords({"title": "AI data center capex"}, {"question": ""}, ["ai"]) is True


def test_polymarket_multi_market_event_keeps_distinct_cache_rows(monkeypatch, tmp_path):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "id": "300",
                    "slug": "where-will-rates-end-year",
                    "title": "Where will rates end the year?",
                    "category": "Fed",
                    "active": True,
                    "closed": False,
                    "markets": [
                        {
                            "id": "301",
                            "slug": "rates-above-4",
                            "question": "Will rates end above 4%?",
                            "outcomes": '["Yes","No"]',
                            "outcomePrices": '["0.52","0.48"]',
                            "volume": "20000",
                            "active": True,
                            "closed": False,
                        },
                        {
                            "id": "302",
                            "slug": "rates-below-3",
                            "question": "Will rates end below 3%?",
                            "outcomes": '["Yes","No"]',
                            "outcomePrices": '["0.18","0.82"]',
                            "volume": "21000",
                            "active": True,
                            "closed": False,
                        },
                    ],
                }
            ]

    monkeypatch.setattr(sc.requests, "get", lambda *args, **kwargs: FakeResponse())

    events = sc.fetch_polymarket_events(limit=5, min_volume=1000, keywords=["rates"])
    written = sc.append_events(events, cache_dir=tmp_path / "cache")

    assert len(events) == 2
    assert written == 2
    assert len({event["url"] for event in events}) == 2


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


def test_source_health_separates_fetch_persist_dedupe_and_collision_counts(tmp_path):
    from reports import source_collector as sc

    now = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    health = sc.update_source_health(
        [],
        cache_dir=tmp_path / "cache",
        now=now,
        attempted_sources=["kalshi"],
        run_stats={"kalshi": {
            "fetch_success": True,
            "persist_success": True,
            "fetched": 10,
            "persisted": 2,
            "deduped": 7,
            "collisions": 1,
        }},
    )

    row = health["kalshi"]
    assert row["fetch_success"] is True
    assert row["persist_success"] is True
    assert row["last_fetch_success"] == "2026-08-21T10:00:00+09:00"
    assert row["last_persist_success"] == "2026-08-21T10:00:00+09:00"
    assert row["last_fetched_count"] == 10
    assert row["last_persisted_count"] == 2
    assert row["last_deduped_count"] == 7
    assert row["last_collision_count"] == 1


def test_duplicate_only_observation_run_is_not_zero_persist_failure(tmp_path):
    from reports import source_collector as sc
    from reports.wiki_pipeline_health import _summarize_source_health

    now = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    health = sc.update_source_health(
        [], cache_dir=tmp_path / "cache", now=now,
        attempted_sources=["kalshi"],
        run_stats={"kalshi": {
            "fetch_success": True, "persist_success": True,
            "fetched": 4, "persisted": 4, "deduped": 0, "collisions": 0,
        }},
    )
    health = sc.update_source_health(
        [], cache_dir=tmp_path / "cache", now=now + timedelta(minutes=30),
        attempted_sources=["kalshi"],
        run_stats={"kalshi": {
            "fetch_success": True, "persist_success": True,
            "fetched": 4, "persisted": 0, "deduped": 4, "collisions": 0,
        }},
    )

    section = _summarize_source_health(health, [], [])
    row = next(row for row in section["sources"] if row["source"] == "kalshi")
    assert row["duplicate_only"] is True
    assert row["zero_persist_streak"] == 0
    assert section["overall"]["zero_persist_sources"] == 0


def test_repeated_zero_persist_and_cardinality_drop_are_reported(tmp_path):
    from reports import source_collector as sc
    from reports.wiki_pipeline_health import _summarize_source_health

    cache = tmp_path / "cache"
    now = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    for offset, fetched, persisted in ((0, 100, 100), (30, 40, 0), (60, 10, 0)):
        health = sc.update_source_health(
            [], cache_dir=cache, now=now + timedelta(minutes=offset),
            attempted_sources=["kalshi"],
            run_stats={"kalshi": {
                "fetch_success": True,
                "persist_success": persisted > 0,
                "fetched": fetched,
                "persisted": persisted,
                "deduped": 0,
                "collisions": 0,
            }},
        )

    section = _summarize_source_health(health, [], [])
    row = next(row for row in section["sources"] if row["source"] == "kalshi")
    assert row["zero_persist_streak"] == 2
    assert row["cardinality_drop_streak"] == 2
    assert section["overall"]["zero_persist_sources"] == 1
    assert section["overall"]["cardinality_drop_sources"] == 1


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


def test_telegram_messages_from_html_keeps_card_text_and_url_together():
    html = '''
    <div class="tgme_widget_message">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        GE 버노바 2026년 2분기 실적 요약<br/>매출과 마진 개선
      </div>
      <a class="tgme_widget_message_date" href="https://t.me/insidertracking/58906"><time></time></a>
    </div>
    <div class="tgme_widget_message">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        요르단 공군기지 이란 미사일 공격
      </div>
      <a class="tgme_widget_message_date" href="https://t.me/insidertracking/58907"><time></time></a>
    </div>
    '''

    messages = sc._telegram_messages_from_html(html, "insidertracking")

    assert [m["url"] for m in messages] == [
        "https://t.me/insidertracking/58906",
        "https://t.me/insidertracking/58907",
    ]
    assert messages[0]["title"].startswith("GE 버노바")
    assert "매출과 마진 개선" in messages[0]["body_raw"]
    assert "요르단" not in messages[0]["body_raw"]
    assert "tgme_widget_message" in messages[0]["raw_html"]


def test_fetch_telegram_archives_raw_and_text(monkeypatch, tmp_path):
    class _Resp:
        def __init__(self, text):
            self.text = text

    html = '''
    <div class="tgme_widget_message">
      <div class="tgme_widget_message_text js-message_text" dir="auto">
        엔비디아 AI 서버 수요 확대<br/>원문: NVIDIA AI server demand expands
      </div>
      <a class="tgme_widget_message_date" href="https://t.me/insidertracking/60001"><time></time></a>
    </div>
    '''

    def fake_get(url, timeout=0):
        if url.startswith("https://r.jina.ai/"):
            return _Resp("plain markdown without bold titles")
        return _Resp(html)

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(sc, "_bounded_get", fake_get)

    events = sc.fetch_telegram_channel_events(["insidertracking"])

    assert len(events) == 1
    event = events[0]
    assert event["source"] == "telegram:insidertracking"
    assert event["url"] == "https://t.me/insidertracking/60001"
    assert "엔비디아" in event["body_raw"]
    assert Path(event["raw_path"]).exists()
    assert Path(event["text_path"]).read_text(encoding="utf-8") == event["body_raw"]
    manifest = json.loads(Path(event["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source"] == "telegram:insidertracking"
    assert manifest["ttl_days"] == 14


def test_fetch_telegram_falls_back_to_direct_html(monkeypatch, tmp_path):
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

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(sc, "_bounded_get", fake_get)
    events = sc.fetch_telegram_channel_events(["chanx"])
    assert len(events) == 1
    assert events[0]["title"] == "연준 금리 동결 시사"
    assert events[0]["url"] == "https://t.me/chanx/9"
    assert events[0]["classification"]["source_family"] == "community"
    assert any(u.startswith("https://t.me/s/chanx") for u in calls)   # 폴백 경로 사용됨


def test_collect_once_isolates_source_crash(tmp_path, monkeypatch):
    """한 소스 fetcher 가 크래시해도 나머지 수집 + 헬스 기록은 계속."""
    monkeypatch.setattr(sc, "fetch_yahoo_headline_events",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sc, "fetch_telegram_channel_events",
                        lambda: [{"source": "telegram:insidertracking", "title": "t", "url": "https://t.me/y/1"}])
    monkeypatch.setattr(sc, "fetch_market_snapshot_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_fred_macro_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_world_gov_bond_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_polymarket_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_kalshi_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_investing_news_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_news_rss_events", lambda: [])
    fetched, written = sc.collect_once(cache_dir=tmp_path / "cache")
    assert fetched == 1 and written == 1
    h = sc.load_source_health(tmp_path / "cache")
    assert h["yahoo_headline"]["last_count"] == 0
    assert h["telegram:insidertracking"]["last_count"] == 1


def test_collect_once_includes_polymarket(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "fetch_investing_news_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_telegram_channel_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_market_snapshot_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_fred_macro_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_world_gov_bond_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_polymarket_events",
                        lambda: [{"source": "polymarket", "title": "Fed cut odds", "url": "https://polymarket.com/event/fed"}])
    monkeypatch.setattr(sc, "fetch_kalshi_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_yahoo_headline_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_news_rss_events", lambda: [])

    fetched, written = sc.collect_once(cache_dir=tmp_path / "cache")

    assert fetched == 1 and written == 1
    h = sc.load_source_health(tmp_path / "cache")
    assert h["polymarket"]["last_count"] == 1


def test_collect_once_includes_kalshi(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "fetch_investing_news_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_telegram_channel_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_market_snapshot_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_fred_macro_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_world_gov_bond_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_polymarket_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_yahoo_headline_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_news_rss_events", lambda: [])
    monkeypatch.setattr(
        sc,
        "fetch_kalshi_events",
        lambda: [{"source": "kalshi", "title": "Fed cut odds", "url": "https://kalshi.com/markets/fed"}],
    )

    fetched, written = sc.collect_once(cache_dir=tmp_path / "cache")

    assert fetched == 1 and written == 1
    health = sc.load_source_health(tmp_path / "cache")
    assert health["kalshi"]["last_count"] == 1


def test_collect_once_uses_provider_runner_and_writes_manifests(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "fetch_investing_news_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_telegram_channel_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_market_snapshot_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_fred_macro_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_world_gov_bond_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_polymarket_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_kalshi_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_yahoo_headline_events", lambda: [])
    monkeypatch.setattr(sc, "fetch_news_rss_events", lambda: [])

    sc.collect_once(cache_dir=tmp_path / "cache")

    from reports.source_runs import load_source_runs

    runs = load_source_runs(tmp_path / "cache", hours=1)
    assert runs and all(not row.get("error") for row in runs)
    assert "saveticker" not in {row["provider"] for row in runs}



def test_sunset_alert_remains_until_source_is_explicitly_retired():
    now = datetime(2026, 11, 13, 11, 0, tzinfo=KST)
    health = {
        "saveticker": {
            "availability": "unavailable",
            "availability_reason": "SaveTicker news API ended: legacy-news-sunset-notice",
            "last_error": "SaveTicker news API ended: legacy-news-sunset-notice",
            "last_success": "2026-11-11T10:35:01+09:00",
        }
    }
    assert [r["source"] for r in sc.stale_sources(health, now=now)] == ["saveticker"]
    health["saveticker"]["availability"] = "disabled"
    assert sc.stale_sources(health, now=now) == []


def test_yahoo_headline_events_tag_symbol_and_skip_kr(monkeypatch):
    assert sc.yahoo_headline_symbols(["005930.KS", "NVDA", "brk.b"]) == ["NVDA", "BRK.B"]
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>Nvidia raises guidance</title>
      <link>https://finance.yahoo.com/news/nvda-guidance</link>
      <pubDate>Thu, 12 Nov 2026 14:04:03 +0000</pubDate>
      <description>Shares rose.</description>
    </item></channel></rss>"""
    events = sc.fetch_yahoo_headline_events(["NVDA"], get=lambda url, symbol: xml)
    assert events[0]["tickers"] == ["NVDA"]
    assert events[0]["source"] == "yahoo_headline"
    assert "속보" not in events[0]["tags"]
    assert events[0]["published_at"].endswith("+09:00")


def test_yahoo_headline_merges_tickers_for_shared_article(monkeypatch):
    shared = """<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>Chip demand lifts suppliers</title>
      <link>https://finance.yahoo.com/news/chip-demand</link>
      <pubDate>Thu, 12 Nov 2026 14:04:03 +0000</pubDate>
      <description>Shared story.</description>
    </item></channel></rss>"""
    unique = """<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>Microsoft cloud update</title>
      <link>https://finance.yahoo.com/news/msft-cloud</link>
      <pubDate>Thu, 12 Nov 2026 15:00:00 +0000</pubDate>
      <description>Only Microsoft.</description>
    </item></channel></rss>"""

    events = sc.fetch_yahoo_headline_events(["MSFT", "NVDA"], get=lambda url, symbol: shared)
    assert len(events) == 1
    assert events[0]["tickers"] == ["MSFT", "NVDA"]
    assert events[0]["source_url"].endswith("s=MSFT&region=US&lang=en-US")
    assert events[0]["classification"]["source_family"] == "news"

    reversed_events = sc.fetch_yahoo_headline_events(["NVDA", "MSFT"], get=lambda url, symbol: shared)
    assert len(reversed_events) == 1
    assert reversed_events[0]["tickers"] == ["NVDA", "MSFT"]

    classified = []
    real_classify = sc._classify_event

    def spy(event):
        classified.append(list(event.get("tickers") or []))
        return real_classify(event)

    monkeypatch.setattr(sc, "_classify_event", spy)

    def get(url, symbol):
        if symbol == "NVDA":
            raise RuntimeError("feed down")
        if symbol == "MSFT":
            return unique
        return shared

    mixed = sc.fetch_yahoo_headline_events(["MSFT", "NVDA", "AMD"], get=get)
    by_title = {event["title"]: event["tickers"] for event in mixed}
    assert by_title["Microsoft cloud update"] == ["MSFT"]
    assert by_title["Chip demand lifts suppliers"] == ["AMD"]
    assert all("NVDA" not in tickers for tickers in by_title.values())
    assert classified == [["MSFT"], ["AMD"]]

# ── 죽은 출처 복구 — 직접 폴백·FRED API·오류 원인 기록 ───────────────────────





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


def test_retired_news_sources_are_not_scheduled():
    from reports.source_pipeline import default_registry

    names = {spec.name for spec in default_registry()}
    sources = {source for spec in default_registry() for source in spec.sources}
    expected = set(sc.expected_sources())
    assert "arca" not in names and "saveticker" not in names
    assert "arca" not in sources and "saveticker" not in sources
    assert "arca" not in expected and "saveticker" not in expected
