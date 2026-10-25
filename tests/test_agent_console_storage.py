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
        "as_of": "2026-08-07T16:10:00Z",
        "bars": {"AAPL": [
            {"time": "2026-08-07T15:55:00Z", "close": 100},
            {"time": "2026-08-07T16:00:00Z", "close": 101},
        ]},
    })

    assert response.status_code == 200
    assert response.json["event_count"] == 0
    assert response.json["evaluations"][0]["matched"] is False
    state = storage.get_chart_alert_rule(rule["id"])["last_state"]
    assert state["matched"] is False
    assert state["trace"][0]["status"] == "false"


def _server_rule(monkeypatch, tmp_path, **extra):
    from agent_console import chart_alert_worker

    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "console.sqlite3"))
    monkeypatch.setattr(chart_alert_worker, "_LOCK_TARGET", str(tmp_path / "chart_alert_worker"))
    return storage.save_chart_alert_rule({
        "workspace_id": "w1",
        "store_key": "cw:w1:AAPL:5m:lin",
        "symbol": "AAPL",
        "timeframe": "5m",
        "frequency": "once",
        "message": "AAPL crossed 100",
        "condition": {"type": "price", "operator": "crossing_up", "value": 100},
        **extra,
    })


_CROSS_BARS = {"AAPL": [
    {"time": "2026-08-07T15:55:00Z", "close": 99},
    {"time": "2026-08-07T16:00:00Z", "close": 101},
]}


def test_C_fix_alerts_batch_api_retries_failed_send_and_never_resends_delivered(monkeypatch, tmp_path):
    from agent_console import chart_alert_dispatcher
    from agent_console.server import create_app

    rule = _server_rule(monkeypatch, tmp_path)
    outcomes = [False, True, True]
    sent: list[str] = []
    monkeypatch.setattr(chart_alert_dispatcher, "send_alert_message", lambda text: sent.append(text) or outcomes[len(sent) - 1])
    client = create_app().test_client()
    body = {"workspace_id": "w1", "bars": _CROSS_BARS, "notify": True, "as_of": "2026-08-07T16:10:00Z"}

    failed = client.post("/api/chart-alerts/evaluate-batch", json=body).json
    assert failed["notification"]["failed"] == 1
    assert storage.get_chart_alert_rule(rule["id"])["last_state"]["triggered"] is False

    retried = client.post("/api/chart-alerts/evaluate-batch", json=body).json
    assert retried["notification"]["delivered"] == 1
    assert storage.get_chart_alert_rule(rule["id"])["last_state"]["triggered"] is True

    again = client.post("/api/chart-alerts/evaluate-batch", json=body).json
    assert again["notification"]["attempted"] == 0
    assert len(sent) == 2


def test_C_fix_alerts_single_rule_evaluate_api_does_not_consume_once(monkeypatch, tmp_path):
    from agent_console.server import create_app

    rule = _server_rule(monkeypatch, tmp_path)
    client = create_app().test_client()

    response = client.post(f"/api/chart-alerts/rules/{rule['id']}/evaluate", json={
        "previous_price": 99, "current_price": 101, "as_of": "2026-08-07T16:00:00Z",
    })

    assert response.json["triggered"] is True
    state = storage.get_chart_alert_rule(rule["id"])["last_state"]
    assert not state.get("triggered")
    assert state["manual_evaluation"]["triggered"] is True


def test_C_fix2_batch_api_does_not_alert_on_stale_client_bars(monkeypatch, tmp_path):
    from agent_console import chart_alert_dispatcher
    from agent_console.server import create_app

    rule = _server_rule(monkeypatch, tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(chart_alert_dispatcher, "send_alert_message", lambda text: sent.append(text) or True)
    client = create_app().test_client()

    # 마지막 5분봉 마감(16:05Z) 뒤 정규장 3시간 가까이 새 봉이 없는 스냅샷.
    stale = client.post("/api/chart-alerts/evaluate-batch", json={
        "workspace_id": "w1", "bars": _CROSS_BARS, "notify": True, "as_of": "2026-08-07T19:00:00Z",
    }).json

    assert stale["notification"]["attempted"] == 0 and sent == []
    assert stale["evaluations"][0]["status"] == "unknown"
    assert stale["evaluations"][0]["reason"] == "stale data: AAPL 5m"
    assert stale["evaluation_summary"]["unknown"] == 1
    assert storage.list_chart_alert_events(rule_ids=[rule["id"]]) == []
