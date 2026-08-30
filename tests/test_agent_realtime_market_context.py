from __future__ import annotations

import json
import time


def test_realtime_snapshot_reads_fresh_cache_and_formats_lines(monkeypatch, tmp_path):
    from agent_console import realtime_market
    from providers import realtime_quotes

    now = time.time()
    cache = {
        "__heartbeat__": {"ts": now, "n": 2},
        "005930": {"price": 80000, "volume": 123, "ts": now - 3, "src": "toss"},
        "QQQ": {"price": 550.5, "volume": 456, "ts": now - 5, "src": "toss"},
    }
    path = tmp_path / "rest_quotes.json"
    path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_URL", raising=False)
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    monkeypatch.setenv("QUOTES_POLL_ENABLED", "true")
    monkeypatch.setattr(realtime_quotes, "REST_CACHE_PATH", str(path))

    snapshot = realtime_market.build_market_snapshot(symbols=["005930", "QQQ"], now=now)

    assert snapshot["ok"] is True
    assert snapshot["status"] == "partial"
    assert snapshot["quotes"][0]["symbol"] == "005930"
    assert snapshot["quotes"][0]["price"] == 80000.0
    assert snapshot["quotes"][0]["source"] == "rest_cache:toss"
    assert 2 <= snapshot["quotes"][0]["age_s"] <= 4
    assert "005930" in "\n".join(realtime_market.compact_snapshot_lines(snapshot))


def test_context_pack_includes_market_snapshot(monkeypatch, tmp_path):
    from agent_console import context

    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setattr(context, "recent_source_events", lambda **kwargs: [])
    monkeypatch.setattr(context, "world_memory_rows", lambda **kwargs: [])
    monkeypatch.setattr(context, "latest_reports", lambda *args, **kwargs: [])
    monkeypatch.setattr(context, "ml_activity", lambda *args, **kwargs: [])
    monkeypatch.setattr(context, "portfolio_state", lambda: {"holdings": []})
    monkeypatch.setattr(context, "paper_state", lambda: {})
    monkeypatch.setattr(context, "model_state", lambda: {})
    monkeypatch.setattr(
        context,
        "shared_memory",
        type(
            "SharedMemoryStub",
            (),
            {
                "sync_external_layer_from_pack": staticmethod(lambda pack: None),
                "status": staticmethod(lambda limit=8: {"ok": True, "records": []}),
            },
        ),
    )
    monkeypatch.setattr(
        context.realtime_market,
        "build_market_snapshot",
        lambda: {
            "ok": True,
            "status": "partial",
            "quotes": [{"symbol": "QQQ", "price": 550.5}],
        },
    )

    pack = context.context_pack("market")

    assert pack["market_snapshot"]["quotes"][0]["symbol"] == "QQQ"

def test_realtime_snapshot_merges_market_microstructure_store(monkeypatch):
    from agent_console import realtime_market

    payload = {
        "as_of": "2026-07-27T05:00:00+00:00",
        "source": "kiwoom_collector",
        "indices": {
            "kospi": {"price": 3210.5, "change_pct": 0.7},
            "kosdaq": {"price": 820.1, "change_pct": -0.2},
            "kospi200": {"price": 452.2, "change_pct": 0.31},
        },
        "investor_flow": {
            "kospi": {"foreign_net": 120000000000, "institution_net": -50000000000, "individual_net": -70000000000},
        },
        "k200_futures": {"price": 425.5, "change_pct": 0.4, "foreign_net": 3500},
        "breadth": {"advancers": 512, "decliners": 318, "unchanged": 75},
    }
    monkeypatch.setattr(realtime_market.market_snapshot_store, "load_market_microstructure", lambda: payload)

    snapshot = realtime_market.build_market_snapshot(symbols=[], now=1000.0)

    assert snapshot["ok"] is True
    assert snapshot["status"] == "partial"
    assert snapshot["indices"]["kospi"]["price"] == 3210.5
    assert snapshot["investor_flow"]["kospi"]["foreign_net"] == 120000000000
    assert snapshot["k200_futures"]["foreign_net"] == 3500
    assert snapshot["breadth"]["advancers"] == 512
    unavailable = {row["field"] for row in snapshot["unavailable"]}
    assert "kospi_index" not in unavailable
    assert "kosdaq_index" not in unavailable
    assert "investor_flow" not in unavailable
    assert "k200_futures" not in unavailable
    assert "advancers_decliners" not in unavailable

def test_compact_snapshot_lines_include_microstructure_fields():
    from agent_console import realtime_market

    snapshot = {
        "status": "partial",
        "as_of": "2026-07-27T05:00:00+00:00",
        "indices": {
            "kospi": {"price": 3210.5, "change_pct": 0.7},
            "kosdaq": {"price": 820.1, "change_pct": -0.2},
            "kospi200": {"price": 452.2, "change_pct": 0.31},
        },
        "investor_flow": {
            "kospi": {"foreign_net": 120000000000, "institution_net": -50000000000, "scope": "market", "basis": "intraday"},
        },
        "k200_futures": {"price": 425.5, "change_pct": 0.4, "foreign_net": 3500},
        "breadth": {"advancers": 512, "decliners": 318},
    }

    text = "\n".join(realtime_market.compact_snapshot_lines(snapshot))

    assert "KOSPI" in text
    assert "KOSDAQ" in text
    assert "KOSPI200" in text
    assert "외국인" in text
    assert "기관" in text
    assert "K200 선물" in text
    assert "상승 512" in text
    assert "하락 318" in text
    assert "market" in text
    assert "intraday" in text


def test_realtime_snapshot_uses_microstructure_fx_when_toss_disabled(monkeypatch):
    from agent_console import realtime_market

    monkeypatch.setenv("AGENT_CONSOLE_TOSS_FX_ENABLED", "0")
    monkeypatch.setattr(
        realtime_market.market_snapshot_store,
        "load_market_microstructure",
        lambda: {"as_of": "2026-07-27T10:15:00+09:00", "fx": {"pair": "USD/KRW", "rate": 1387.2, "source": "kr_microstructure"}},
    )

    snapshot = realtime_market.build_market_snapshot(symbols=[], now=1000.0)

    assert snapshot["fx"]["rate"] == 1387.2
    assert "USD/KRW" in "\n".join(realtime_market.compact_snapshot_lines(snapshot))


def test_realtime_snapshot_exposes_us_quote_session(monkeypatch, tmp_path):
    from agent_console import realtime_market
    from providers import realtime_quotes

    now = time.time()
    cache = {
        "__heartbeat__": {"ts": now, "us_session": "premarket"},
        "QQQ": {"price": 550.5, "ts": now - 2, "src": "toss", "session": "premarket"},
    }
    path = tmp_path / "rest_quotes.json"
    path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("UPSTASH_REDIS_URL", raising=False)
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    monkeypatch.setenv("QUOTES_POLL_ENABLED", "true")
    monkeypatch.setattr(realtime_quotes, "REST_CACHE_PATH", str(path))

    snapshot = realtime_market.build_market_snapshot(symbols=["QQQ"], now=now)

    assert snapshot["quotes"][0]["session"] == "premarket"
    assert "premarket" in "\n".join(realtime_market.compact_snapshot_lines(snapshot))


def test_realtime_snapshot_reads_quote_layers_once_for_many_symbols(monkeypatch):
    from agent_console import realtime_market
    from providers import realtime_quotes

    now = 1_000.0
    calls = []
    cache = {
        "__heartbeat__": {"ts": now},
        "AAPL": {"price": 100.0, "volume": 10, "ts": now - 1, "src": "ws"},
        "MSFT": {"price": 200.0, "volume": 20, "ts": now - 1, "src": "ws"},
    }

    def snapshot_reader():
        calls.append(True)
        return {"ws": cache, "rest": {}}

    monkeypatch.setattr(realtime_quotes, "read_realtime_snapshot", snapshot_reader)
    monkeypatch.setattr(realtime_market.market_snapshot_store, "load_market_microstructure", lambda: {})
    monkeypatch.setattr(realtime_market, "_quote_from_kis", lambda *args, **kwargs: None)
    monkeypatch.setenv("REALTIME_ENABLED", "true")

    snapshot = realtime_market.build_market_snapshot(symbols=["AAPL", "MSFT"], now=now)

    assert [quote["symbol"] for quote in snapshot["quotes"]] == ["AAPL", "MSFT"]
    assert len(calls) == 1


def test_context_pack_includes_strategy_experiment_summary(monkeypatch, tmp_path):
    from agent_console import context

    monkeypatch.setenv("AGENT_CONSOLE_ML_DATA_DIR", str(tmp_path / "ml-data"))
    ml_dir = tmp_path / "ml-data"
    ml_dir.mkdir()
    (ml_dir / "intraday_shadow_decisions.jsonl").write_text(
        '{"id":"d1","symbol":"005930"}\n{"id":"d2","symbol":"000660"}\n',
        encoding="utf-8",
    )
    (ml_dir / "intraday_outcome_labels.jsonl").write_text(
        '{"decision_id":"d1","quality_label":"bad"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(context, "recent_source_events", lambda **kwargs: [])
    monkeypatch.setattr(context, "world_memory_rows", lambda **kwargs: [])
    monkeypatch.setattr(context, "latest_reports", lambda *args, **kwargs: [])
    monkeypatch.setattr(context, "ml_activity", lambda *args, **kwargs: [])
    monkeypatch.setattr(context, "portfolio_state", lambda: {"holdings": []})
    monkeypatch.setattr(context, "paper_state", lambda: {})
    monkeypatch.setattr(context, "model_state", lambda: {})
    monkeypatch.setattr(context.realtime_market, "build_market_snapshot", lambda: {"quotes": [], "status": "empty"})

    pack = context.context_pack("market")

    assert pack["strategy_experiments"] == {
        "ok": True,
        "recent_decisions": 2,
        "recent_labels": 1,
        "risk_action": "observe",
        "reasons": [],
    }
