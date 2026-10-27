"""agent_console — 차트 알림(룰 API·runner·worker) 테스트."""
from __future__ import annotations


import pandas as pd

from agent_console_support import _isolate

def test_chart_alert_rules_round_trip_and_filter(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage

    rule = storage.save_chart_alert_rule({
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:AAPL:1d:lin",
        "symbol": "AAPL",
        "timeframe": "1d",
        "name": "AAPL breakout",
        "condition": {"type": "price", "operator": "crossing_up", "value": 210.0},
        "message": "AAPL crossed 210",
        "frequency": "once",
        "enabled": True,
    })

    assert rule["id"]
    assert rule["workspace_id"] == "workspace-1"
    assert rule["condition"]["operator"] == "crossing_up"
    assert rule["enabled"] is True

    updated = storage.update_chart_alert_state(rule["id"], {"triggered": True, "last_price": 211.0})
    assert updated["last_state"]["triggered"] is True
    assert storage.get_chart_alert_rule(rule["id"])["last_state"]["last_price"] == 211.0

    listed = storage.list_chart_alert_rules(workspace_id="workspace-1")
    assert [row["id"] for row in listed] == [rule["id"]]
    assert storage.list_chart_alert_rules(workspace_id="other") == []


def test_chart_alert_rules_accept_multi_condition_indicator_alert(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage

    rule = storage.save_chart_alert_rule({
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:AAPL:1d:lin",
        "symbol": "AAPL",
        "timeframe": "1d",
        "name": "AAPL technical",
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_up", "value": 210.0},
                {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 70.0},
            ]
        },
    })

    assert rule["condition"]["all"][1]["field"] == "rsi_14"


def test_chart_alert_batch_api_evaluates_saved_rules_from_bars(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app
    from agent_console import storage

    app = create_app()
    client = app.test_client()
    rule = client.post("/api/chart-alerts/rules", json={
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:AAPL:5m:lin",
        "symbol": "AAPL",
        "timeframe": "5m",
        "name": "AAPL price + RSI",
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_up", "value": 100.0},
                {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 100.0},
            ]
        },
        "frequency": "once",
        "enabled": True,
    }).json["rule"]
    closes = [
        100, 99, 98, 97, 96,
        95, 96, 97, 98, 99,
        98, 97, 96, 95, 94,
        95, 96, 97, 99, 101,
    ]
    # 2026-08-03(월) 13:30Z = 미국 정규장 09:30 ET 부터의 5분봉.
    bars = [
        {"time": f"2026-08-03T{13 + (30 + i * 5) // 60:02d}:{(30 + i * 5) % 60:02d}:00Z", "close": close}
        for i, close in enumerate(closes)
    ]

    result = client.post("/api/chart-alerts/evaluate-batch", json={
        "workspace_id": "workspace-1",
        "bars": {"AAPL": bars},
        "as_of": "2026-08-03T15:15:00Z",  # 마지막 봉(15:05Z) 마감 직후 — 오래된 시세가 아님
    })

    assert result.status_code == 200
    payload = result.json
    assert payload["ok"] is True
    assert payload["event_count"] == 1
    assert payload["events"][0]["alert_id"] == rule["id"]
    assert payload["events"][0]["matched_conditions"] == ["price:crossing_up", "indicator:rsi_14:less_than"]
    saved = storage.get_chart_alert_rule(rule["id"])
    # notify 없는 평가는 발송이 아니므로 once 를 소진하지 않는다(C-04).
    assert saved["last_state"]["matched"] is True
    assert saved["last_state"]["triggered"] is False
    assert saved["last_state"]["event"]["alert_id"] == rule["id"]


def test_chart_alert_batch_api_returns_409_while_worker_cycle_holds_lock(monkeypatch, tmp_path):
    """배치 API 도 워커 사이클 락을 통과한다 — 종전엔 우회해 같은 once 룰을 이중 알림할 수 있었다."""
    _isolate(monkeypatch, tmp_path)
    from agent_console import chart_alert_worker
    from agent_console.server import create_app

    monkeypatch.setattr(chart_alert_worker, "_LOCK_TARGET", str(tmp_path / "chart_alert_worker"))
    client = create_app().test_client()
    with chart_alert_worker.cycle_lock(timeout=0):
        result = client.post("/api/chart-alerts/evaluate-batch", json={"workspace_id": "w", "bars": {}})
    assert result.status_code == 409 and result.json["reason"] == "already_running"
    assert client.post("/api/chart-alerts/evaluate-batch", json={"workspace_id": "w", "bars": {}}).status_code == 200


def test_chart_alert_runner_computes_extended_indicator_values():

    from agent_console import chart_alert_runner

    idx = pd.date_range("2026-08-03T13:30:00Z", periods=40, freq="5min")
    bars = pd.DataFrame({
        "open": [100 + i * 0.3 for i in range(40)],
        "high": [101 + i * 0.3 for i in range(40)],
        "low": [99 + i * 0.3 for i in range(40)],
        "close": [100 + i * 0.3 + (0.4 if i % 5 == 0 else 0.0) for i in range(40)],
        "volume": [1000 + i * 25 for i in range(40)],
    }, index=idx)
    rule = {
        "id": "r", "symbol": "AAPL", "timeframe": "5m",
        "condition": {"type": "price", "operator": "greater_than", "value": 0},
    }

    contexts = chart_alert_runner.build_condition_contexts(
        rule, {("AAPL", "5m"): bars}, as_of="2026-08-03T17:00:00Z",
    )
    values = contexts[("AAPL", "5m")]["current"]

    assert values["time"] == idx[-1].isoformat()
    for field in ("rsi_14", "macd", "macd_signal", "macd_hist", "vwap", "atr_14", "volume_zscore_20"):
        assert isinstance(values[field], float), field


def test_chart_alert_batch_api_evaluates_macd_hist_condition(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app

    app = create_app()
    client = app.test_client()
    rule = client.post("/api/chart-alerts/rules", json={
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:AAPL:5m:lin",
        "symbol": "AAPL",
        "timeframe": "5m",
        "name": "AAPL price + MACD",
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_up", "value": 111.5},
                {"type": "indicator", "field": "macd_hist", "operator": "greater_than", "value": -999.0},
            ]
        },
        "frequency": "once",
        "enabled": True,
    }).json["rule"]
    bars = [
        {
            "time": f"2026-08-03T{13 + (30 + i * 5) // 60:02d}:{(30 + i * 5) % 60:02d}:00Z",
            "open": 100 + i * 0.3,
            "high": 101 + i * 0.3,
            "low": 99 + i * 0.3,
            "close": 100 + i * 0.3,
            "volume": 1000 + i * 25,
        }
        for i in range(40)
    ]

    result = client.post("/api/chart-alerts/evaluate-batch", json={
        "workspace_id": "workspace-1",
        "bars": {"AAPL": bars},
        "as_of": "2026-08-03T17:00:00Z",
    })

    assert result.status_code == 200
    payload = result.json
    assert payload["event_count"] == 1
    assert payload["events"][0]["alert_id"] == rule["id"]
    assert payload["events"][0]["matched_conditions"] == ["price:crossing_up", "indicator:macd_hist:greater_than"]
    assert isinstance(payload["events"][0]["indicator_values"]["macd_hist"], float)


def test_chart_alert_batch_api_can_dispatch_notifications(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app
    from agent_console import chart_alert_dispatcher

    sent: list[str] = []
    monkeypatch.setattr(chart_alert_dispatcher, "send_alert_message", lambda text: sent.append(text) or True)

    app = create_app()
    client = app.test_client()
    rule = client.post("/api/chart-alerts/rules", json={
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:AAPL:5m:lin",
        "symbol": "AAPL",
        "timeframe": "5m",
        "name": "AAPL breakout",
        "condition": {"type": "price", "operator": "crossing_up", "value": 100.0},
        "message": "AAPL crossed 100",
        "frequency": "once",
        "enabled": True,
    }).json["rule"]
    bars = [
        {"time": "2026-08-03T13:30:00Z", "close": 99.0},
        {"time": "2026-08-03T13:35:00Z", "close": 101.0},
    ]

    result = client.post("/api/chart-alerts/evaluate-batch", json={
        "workspace_id": "workspace-1",
        "bars": {"AAPL": bars},
        "notify": True,
        "as_of": "2026-08-03T13:45:00Z",
    })

    assert result.status_code == 200
    payload = result.json
    assert payload["event_count"] == 1
    assert payload["notification"]["attempted"] == 1
    assert payload["notification"]["delivered"] == 1
    assert rule["id"] in payload["events"][0]["alert_id"]
    assert sent and "AAPL crossed 100" in sent[0]


def test_chart_alert_worker_loads_bars_updates_state_and_notifies(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage
    from agent_console.chart_alert_worker import run_chart_alert_cycle

    rule = storage.save_chart_alert_rule({
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:AAPL:5m:lin",
        "symbol": "AAPL",
        "timeframe": "5m",
        "name": "AAPL breakout",
        "condition": {"type": "price", "operator": "crossing_up", "value": 100.0},
        "message": "AAPL crossed 100",
        "frequency": "once",
        "enabled": True,
    })
    idx = pd.date_range("2026-08-03 13:30", periods=2, freq="5min", tz="UTC")
    bars = pd.DataFrame({"Close": [99.0, 101.0]}, index=idx)
    calls: list[tuple[str, str]] = []
    sent: list[str] = []

    def load_bars(symbol: str, timeframe: str):
        calls.append((symbol, timeframe))
        return bars

    result = run_chart_alert_cycle(
        workspace_id="workspace-1",
        notify=True,
        load_bars_fn=load_bars,
        send_fn=lambda text: sent.append(text) or True,
        as_of="2026-08-03T13:45:00Z",
    )

    assert calls == [("AAPL", "5m")]
    assert result["rule_count"] == 1
    assert result["event_count"] == 1
    assert result["missing_bars"] == []
    assert result["notification"]["delivered"] == 1
    assert sent and "AAPL crossed 100" in sent[0]
    saved = storage.get_chart_alert_rule(rule["id"])
    assert saved["last_state"]["triggered"] is True
    assert saved["last_state"]["event"]["alert_id"] == rule["id"]


def test_chart_alert_run_history_round_trips_and_filters(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage

    saved = storage.save_chart_alert_run({
        "workspace_id": "workspace-1",
        "rule_count": 2,
        "event_count": 1,
        "missing_bars": [{"symbol": "MSFT", "timeframe": "5m"}],
        "notification": {"attempted": 1, "delivered": 1, "failed": 0, "failures": []},
        "status": "ok",
    })

    assert saved["id"] > 0
    assert saved["workspace_id"] == "workspace-1"
    assert saved["event_count"] == 1
    assert saved["notification"]["delivered"] == 1
    listed = storage.list_chart_alert_runs(workspace_id="workspace-1")
    assert [row["id"] for row in listed] == [saved["id"]]
    assert listed[0]["missing_bars"][0]["symbol"] == "MSFT"
    assert storage.list_chart_alert_runs(workspace_id="other") == []


def test_chart_alert_worker_persists_run_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage
    from agent_console.chart_alert_worker import run_chart_alert_cycle

    storage.save_chart_alert_rule({
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:AAPL:5m:lin",
        "symbol": "AAPL",
        "timeframe": "5m",
        "name": "AAPL breakout",
        "condition": {"type": "price", "operator": "crossing_up", "value": 100.0},
        "message": "AAPL crossed 100",
        "frequency": "once",
        "enabled": True,
    })
    bars = pd.DataFrame(
        {"Close": [99.0, 101.0]},
        index=pd.date_range("2026-08-03 13:30", periods=2, freq="5min", tz="UTC"),
    )

    result = run_chart_alert_cycle(
        workspace_id="workspace-1",
        notify=False,
        load_bars_fn=lambda symbol, timeframe: bars,
        as_of="2026-08-03T13:45:00Z",
    )

    runs = storage.list_chart_alert_runs(workspace_id="workspace-1")
    assert result["run_id"] == runs[0]["id"]
    assert runs[0]["rule_count"] == 1
    assert runs[0]["event_count"] == 1
    assert runs[0]["status"] == "ok"


def test_chart_alert_run_history_api_routes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage
    from agent_console.server import create_app

    storage.save_chart_alert_run({
        "workspace_id": "workspace-1",
        "rule_count": 2,
        "event_count": 1,
        "missing_bars": [],
        "notification": {"attempted": 1, "delivered": 1, "failed": 0, "failures": []},
        "status": "ok",
    })

    client = create_app().test_client()
    result = client.get("/api/chart-alerts/runs", query_string={"workspace_id": "workspace-1"})

    assert result.status_code == 200
    payload = result.json
    assert payload["ok"] is True
    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["event_count"] == 1
    assert payload["runs"][0]["notification"]["delivered"] == 1


def test_chart_alert_manual_run_api_invokes_worker(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import chart_alert_worker
    from agent_console.server import create_app

    calls: list[dict] = []
    monkeypatch.setattr(chart_alert_worker, "run_chart_alert_cycle", lambda **kwargs: calls.append(kwargs) or {
        "ok": True,
        "workspace_id": "workspace-1",
        "rule_count": 2,
        "event_count": 1,
        "events": [{"alert_id": "alert-1"}],
        "missing_bars": [],
        "notification": {"attempted": 1, "delivered": 1, "failed": 0, "failures": []},
        "run_id": 9,
        "created_at": "2026-08-01T00:05:00+00:00",
    })

    result = create_app().test_client().post("/api/chart-alerts/run", json={
        "workspace_id": "workspace-1",
        "symbols": ["AAPL"],
        "notify": True,
        "limit": 5,
    })

    assert result.status_code == 200
    assert result.json["ok"] is True
    assert result.json["run_id"] == 9
    assert calls == [{
        "workspace_id": "workspace-1",
        "symbols": ["AAPL"],
        "notify": True,
        "limit": 5,
    }]


def test_chart_alert_api_routes_round_trip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app

    app = create_app()
    client = app.test_client()
    payload = {
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:MSFT:1d:lin",
        "symbol": "MSFT",
        "timeframe": "1d",
        "name": "MSFT support",
        "condition": {"type": "price", "operator": "less_than", "value": 390.0},
        "message": "support failed",
        "frequency": "once",
    }

    saved = client.post("/api/chart-alerts/rules", json=payload)
    assert saved.status_code == 200
    assert saved.json["ok"] is True
    rule = saved.json["rule"]

    listing = client.get("/api/chart-alerts/rules", query_string={"workspace_id": "workspace-1"})
    assert listing.status_code == 200
    assert listing.json["rules"][0]["id"] == rule["id"]

    evaluation = client.post(
        f"/api/chart-alerts/rules/{rule['id']}/evaluate",
        json={"previous_price": 391.0, "current_price": 389.0, "as_of": "2026-08-01T12:00:00Z"},
    )
    assert evaluation.status_code == 200
    assert evaluation.json["triggered"] is True
    assert evaluation.json["event"]["symbol"] == "MSFT"


def test_chart_alert_api_evaluates_multi_condition_payload(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app

    app = create_app()
    client = app.test_client()
    saved = client.post("/api/chart-alerts/rules", json={
        "workspace_id": "workspace-1",
        "store_key": "cw:workspace-1:MSFT:1d:lin",
        "symbol": "MSFT",
        "timeframe": "1d",
        "name": "MSFT price + RSI",
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_up", "value": 400.0},
                {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 65.0},
            ]
        },
    })
    rule = saved.json["rule"]

    evaluation = client.post(
        f"/api/chart-alerts/rules/{rule['id']}/evaluate",
        json={
            "previous_price": 399.0,
            "current_price": 401.0,
            "previous_values": {"rsi_14": 62.0},
            "current_values": {"rsi_14": 63.0},
            "as_of": "2026-08-01T12:00:00Z",
        },
    )

    assert evaluation.status_code == 200
    assert evaluation.json["triggered"] is True
    assert evaluation.json["event"]["condition_count"] == 2
