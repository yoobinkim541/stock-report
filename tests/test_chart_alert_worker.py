from __future__ import annotations

import pandas as pd

import safe_io
from agent_console import chart_alert_worker


def _rule(rule_id, dependency="QQQ"):
    return {
        "id": rule_id,
        "workspace_id": "w1",
        "symbol": "AAPL",
        "timeframe": "1d",
        "name": rule_id,
        "enabled": True,
        "frequency": "repeating",
        "condition": {"op": "all", "children": [
            {"type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close", "operator": "greater_than", "value": 100},
            {"type": "price", "symbol": dependency, "timeframe": "1h", "field": "close", "operator": "greater_than", "value": 500},
        ]},
        "last_state": {},
    }


def _bars(values, freq):
    return pd.DataFrame(
        {"close": values},
        index=pd.date_range("2026-08-01T10:00:00Z", periods=len(values), freq=freq),
    )


def test_worker_deduplicates_requirement_loads_and_persists_every_evaluation(monkeypatch):
    rules = [_rule("r1"), _rule("r2")]
    calls: list[tuple[str, str]] = []
    states: list[tuple[str, dict]] = []
    monkeypatch.setattr(chart_alert_worker.storage, "list_chart_alert_rules", lambda **kwargs: rules)
    monkeypatch.setattr(chart_alert_worker.storage, "update_chart_alert_state", lambda rule_id, state: states.append((rule_id, state)) or {})
    monkeypatch.setattr(chart_alert_worker.storage, "save_chart_alert_run", lambda payload: {"id": 9, "created_at": "now"})

    def loader(symbol, timeframe):
        calls.append((symbol, timeframe))
        return _bars([99, 101], "1D") if timeframe == "1d" else _bars([501, 502], "1h")

    result = chart_alert_worker.run_chart_alert_cycle(load_bars_fn=loader)

    assert calls == [("AAPL", "1d"), ("QQQ", "1h")]
    assert result["event_count"] == 2
    assert [rule_id for rule_id, _state in states] == ["r1", "r2"]
    assert all(state["trace"] for _rule_id, state in states)
    assert len(result["evaluations"]) == 2


def test_run_chart_alert_cycle_skips_entirely_when_another_run_holds_the_lock(monkeypatch, tmp_path):
    """감사 #16 — 겹쳐 도는 두 사이클이 같은 "once" 룰을 동시에 미체결로 읽고
    둘 다 dispatch 해 중복 알림을 보내던 race. 락으로 겹치면 완전히 스킵해야 한다."""
    monkeypatch.setattr(chart_alert_worker, "_LOCK_TARGET", str(tmp_path / "chart_alert_worker"))
    calls = {"n": 0}

    def fake_list_rules(**kwargs):
        calls["n"] += 1
        return [_rule("r1")]
    monkeypatch.setattr(chart_alert_worker.storage, "list_chart_alert_rules", fake_list_rules)

    with safe_io.file_write_lock(chart_alert_worker._LOCK_TARGET, timeout=0):
        result = chart_alert_worker.run_chart_alert_cycle(load_bars_fn=lambda *a, **k: None)

    assert result["ok"] is False
    assert calls["n"] == 0, "락을 못 잡으면 룰 조회/평가/dispatch 를 아예 시작하면 안 됨"


def test_worker_supports_legacy_one_argument_loader_and_records_missing(monkeypatch):
    monkeypatch.setattr(chart_alert_worker.storage, "list_chart_alert_rules", lambda **kwargs: [_rule("r1")])
    states: list[tuple[str, dict]] = []
    monkeypatch.setattr(chart_alert_worker.storage, "update_chart_alert_state", lambda rule_id, state: states.append((rule_id, state)) or {})
    monkeypatch.setattr(chart_alert_worker.storage, "save_chart_alert_run", lambda payload: {"id": 10, "created_at": "now"})
    calls: list[str] = []

    def legacy_loader(symbol):
        calls.append(symbol)
        return _bars([99, 101], "1D") if symbol == "AAPL" else None

    result = chart_alert_worker.run_chart_alert_cycle(load_bars_fn=legacy_loader)

    assert calls == ["AAPL", "QQQ"]
    assert result["missing_bars"] == [{"symbol": "QQQ", "timeframe": "1h"}]
    assert states[0][1]["missing_contexts"] == [{"symbol": "QQQ", "timeframe": "1h"}]


def test_once_rule_retries_delivery_after_send_failure_then_stops(monkeypatch, tmp_path):
    """once 룰: 알림 전송 실패 → 상태에 delivered=False+event 저장 → 다음 사이클이 재전송 →
    성공 후엔 더 이상 이벤트를 내지 않는다 (종전엔 trigger 만 저장돼 영구 미전달)."""
    monkeypatch.setattr(chart_alert_worker, "_LOCK_TARGET", str(tmp_path / "chart_alert_worker"))
    rule = {**_rule("once1"), "frequency": "once"}
    monkeypatch.setattr(chart_alert_worker.storage, "list_chart_alert_rules", lambda **kwargs: [rule])
    monkeypatch.setattr(chart_alert_worker.storage, "update_chart_alert_state",
                        lambda rule_id, state: rule.__setitem__("last_state", state) or {})
    monkeypatch.setattr(chart_alert_worker.storage, "save_chart_alert_run", lambda payload: {"id": 1, "created_at": "now"})
    loader = lambda symbol, timeframe: _bars([99, 101], "1D") if timeframe == "1d" else _bars([501, 502], "1h")
    sent: list[str] = []

    r1 = chart_alert_worker.run_chart_alert_cycle(load_bars_fn=loader, notify=True, send_fn=lambda t: False)
    assert r1["event_count"] == 1 and r1["notification"]["failed"] == 1
    assert rule["last_state"]["triggered"] is True and rule["last_state"]["delivered"] is False

    r2 = chart_alert_worker.run_chart_alert_cycle(load_bars_fn=loader, notify=True, send_fn=lambda t: sent.append(t) or True)
    assert r2["event_count"] == 1 and r2["evaluations"][0]["reason"] == "retry_delivery"
    assert len(sent) == 1 and rule["last_state"]["delivered"] is True

    r3 = chart_alert_worker.run_chart_alert_cycle(load_bars_fn=loader, notify=True, send_fn=lambda t: sent.append(t) or True)
    assert r3["event_count"] == 0 and r3["evaluations"][0]["reason"] == "already_triggered"
    assert len(sent) == 1
