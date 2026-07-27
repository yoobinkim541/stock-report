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
