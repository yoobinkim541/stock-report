from __future__ import annotations

from agent_console import storage


def test_storage_accepts_nested_shared_condition_and_round_trips_audit_state(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "console.sqlite3"))
    rule = storage.save_chart_alert_rule({
        "workspace_id": "w1",
        "store_key": "cw:w1:AAPL:1d:lin",
        "symbol": "AAPL",
        "timeframe": "1d",
        "condition": {"op": "any", "children": [
            {"type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close", "operator": "greater_than", "value": 200},
            {"op": "none", "children": [
                {"type": "indicator", "symbol": "QQQ", "timeframe": "1h", "field": "rsi_14", "operator": "greater_than", "value": 80},
            ]},
        ]},
    })
    state = {
        "last_checked_at": "2026-08-08T12:00:00Z",
        "matched": False,
        "reason": "missing context",
        "trace": [{"node": "leaf", "status": "unknown"}],
        "missing_contexts": [{"symbol": "QQQ", "timeframe": "1h"}],
        "source_timestamps": {"AAPL:1d": "2026-08-08T00:00:00Z"},
        "triggered": False,
    }

    saved = storage.update_chart_alert_state(rule["id"], state)

    assert saved["condition"]["op"] == "any"
    assert saved["last_state"] == state


def test_batch_api_persists_nonmatching_evaluation_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "console.sqlite3"))
    from agent_console.server import create_app

    rule = storage.save_chart_alert_rule({
        "workspace_id": "w1",
        "store_key": "cw:w1:AAPL:5m:lin",
        "symbol": "AAPL",
        "timeframe": "5m",
        "frequency": "repeating",
        "condition": {"type": "price", "operator": "greater_than", "value": 200},
    })
    client = create_app().test_client()

    response = client.post("/api/chart-alerts/evaluate-batch", json={
        "workspace_id": "w1",
        "bars": {"AAPL": [
            {"time": "2026-08-08T11:55:00Z", "close": 100},
            {"time": "2026-08-08T12:00:00Z", "close": 101},
        ]},
    })

    assert response.status_code == 200
    assert response.json["event_count"] == 0
    assert response.json["evaluations"][0]["matched"] is False
    state = storage.get_chart_alert_rule(rule["id"])["last_state"]
    assert state["matched"] is False
    assert state["trace"][0]["status"] == "false"
