from __future__ import annotations

import pandas as pd
import pytest

import safe_io
from agent_console import chart_alert_dispatcher, chart_alert_worker, storage


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
    # 2026-08-03(월) 14:00Z = 미국 정규장 10:00 ET.
    return pd.DataFrame(
        {"close": values},
        index=pd.date_range("2026-08-03T14:00:00Z", periods=len(values), freq=freq),
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


# ── C-fix-alerts: 평가/발송 분리·재시도·중복 방지 (실제 sqlite storage, 가짜 sender) ──

AS_OF = "2026-08-03T15:00:00Z"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "console.sqlite3"))
    monkeypatch.setattr(chart_alert_worker, "_LOCK_TARGET", str(tmp_path / "chart_alert_worker"))
    return tmp_path


def _save_rule(rule_id="r1", *, frequency="once", condition=None, symbol="AAPL", timeframe="5m"):
    return storage.save_chart_alert_rule({
        "id": rule_id,
        "workspace_id": "w1",
        "store_key": f"cw:w1:{symbol}:{timeframe}:lin",
        "symbol": symbol,
        "timeframe": timeframe,
        "name": rule_id,
        "message": f"{rule_id} fired",
        "frequency": frequency,
        "condition": condition or {"type": "price", "operator": "crossing_up", "value": 100},
    })


def _five_minute(values, start="2026-08-03T13:30:00Z"):
    return pd.DataFrame(
        {"close": values},
        index=pd.date_range(start, periods=len(values), freq="5min"),
    )


def _cycle(bars, sent, *, ok=True, notify=True, as_of=AS_OF, **kwargs):
    def sender(text):
        sent.append(text)
        if isinstance(ok, Exception):
            raise ok
        return ok(text) if callable(ok) else ok

    return chart_alert_worker.run_chart_alert_cycle(
        workspace_id="w1", notify=notify, load_bars_fn=lambda symbol, timeframe: bars,
        send_fn=sender, as_of=as_of, **kwargs,
    )


def test_C_fix_alerts_once_failed_send_is_retried_with_same_event_then_completed(isolated):
    _save_rule()
    bars = _five_minute([99.0, 101.0])
    sent: list[str] = []

    first = _cycle(bars, sent, ok=False)
    assert first["notification"]["failed"] == 1
    assert first["status"] == "partial"
    assert storage.get_chart_alert_rule("r1")["last_state"]["triggered"] is False
    pending = storage.list_chart_alert_events(rule_ids=["r1"])
    assert [row["status"] for row in pending] == ["failed"]

    second = _cycle(bars, sent, ok=True)
    assert second["notification"]["delivered"] == 1
    rows = storage.list_chart_alert_events(rule_ids=["r1"])
    assert [(row["event_id"], row["status"]) for row in rows] == [(pending[0]["event_id"], "delivered")]
    assert storage.get_chart_alert_rule("r1")["last_state"]["triggered"] is True

    third = _cycle(bars, sent, ok=True)
    assert third["notification"]["attempted"] == 0
    assert len(sent) == 2  # 실패 1회 + 성공 1회, 성공 뒤 재발송 없음


def test_C_fix_alerts_once_raised_sender_is_retried_on_new_bar_without_second_event(isolated):
    _save_rule()
    sent: list[str] = []
    first = _cycle(_five_minute([99.0, 101.0]), sent, ok=RuntimeError("telegram down"))
    assert first["ok"] is True and first["notification"]["failed"] == 1
    assert "telegram down" in first["notification"]["failures"][0]["error"]

    # 다음 봉에서 조건이 다시 교차해도 once 는 미발송 첫 이벤트 하나만 재시도한다.
    second = _cycle(_five_minute([99.0, 101.0, 99.0, 102.0]), sent, ok=True)
    notification = second["notification"]
    assert (notification["attempted"], notification["delivered"], notification["failed"]) == (1, 1, 0)
    assert len(storage.list_chart_alert_events(rule_ids=["r1"])) == 1
    assert storage.get_chart_alert_rule("r1")["last_state"]["triggered"] is True


def test_C_fix_alerts_notify_false_does_not_consume_once_then_notify_sends(isolated):
    _save_rule()
    bars = _five_minute([99.0, 101.0])
    sent: list[str] = []

    dry = _cycle(bars, sent, notify=False)
    assert dry["event_count"] == 1 and sent == []
    assert storage.list_chart_alert_events(rule_ids=["r1"]) == []
    assert storage.get_chart_alert_rule("r1")["last_state"]["triggered"] is False

    live = _cycle(bars, sent, notify=True)
    assert live["notification"]["delivered"] == 1
    assert len(sent) == 1 and "r1 fired" in sent[0]
    assert live["events"][0]["event_id"] == dry["events"][0]["event_id"]


def test_C_fix_alerts_interrupted_after_pending_save_resends_same_event_and_audits(isolated, monkeypatch):
    _save_rule()
    bars = _five_minute([99.0, 101.0])
    sent: list[str] = []

    real_dispatch = chart_alert_dispatcher.dispatch_alert_events
    calls = {"n": 0}

    def crash_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("process killed")
        return real_dispatch(*args, **kwargs)

    monkeypatch.setattr(chart_alert_dispatcher, "dispatch_alert_events", crash_first)
    crashed = _cycle(bars, sent)
    assert crashed["ok"] is False and crashed["status"] == "error"
    runs = storage.list_chart_alert_runs(workspace_id="w1")
    assert runs[0]["status"] == "error" and "process killed" in runs[0]["result"]["error"]
    pending = storage.list_chart_alert_events(rule_ids=["r1"])
    assert [row["status"] for row in pending] == ["pending"]

    recovered = _cycle(bars, sent)
    assert recovered["notification"]["delivered"] == 1
    rows = storage.list_chart_alert_events(rule_ids=["r1"])
    assert [(row["event_id"], row["status"]) for row in rows] == [(pending[0]["event_id"], "delivered")]
    assert len(sent) == 1


def test_C_fix_alerts_one_raising_send_does_not_block_other_events_and_run_is_saved(isolated):
    _save_rule("a")
    _save_rule("b", condition={"type": "price", "operator": "greater_than", "value": 100})
    sent: list[str] = []

    def flaky(text):
        if "a fired" in text:
            raise RuntimeError("boom")
        return True

    result = _cycle(_five_minute([99.0, 101.0]), sent, ok=flaky)

    assert result["notification"]["attempted"] == 2
    assert result["notification"]["delivered"] == 1
    assert result["notification"]["failed"] == 1
    assert storage.list_chart_alert_runs(workspace_id="w1")[0]["status"] == "partial"
    assert storage.get_chart_alert_rule("b")["last_state"]["triggered"] is True
    assert storage.get_chart_alert_rule("a")["last_state"]["triggered"] is False


def test_C_fix_alerts_repeating_same_bar_sends_once_and_rearms_only_after_false(isolated):
    _save_rule(frequency="repeating", condition={"type": "price", "operator": "greater_than", "value": 100})
    sent: list[str] = []
    bars = _five_minute([99.0, 101.0])

    for _ in range(10):
        _cycle(bars, sent)
    assert len(sent) == 1

    still_true = _cycle(_five_minute([99.0, 101.0, 102.0]), sent)
    assert len(sent) == 1
    assert still_true["evaluations"][0]["suppressed"] == "awaiting_rearm"

    _cycle(_five_minute([99.0, 101.0, 102.0, 99.0]), sent)  # false → 재무장
    assert len(sent) == 1
    _cycle(_five_minute([99.0, 101.0, 102.0, 99.0, 103.0]), sent)  # false→true 전이
    assert len(sent) == 2
    assert len({row["event_id"] for row in storage.list_chart_alert_events(rule_ids=["r1"])}) == 2


def test_C_fix_alerts_worker_passes_as_of_and_excludes_future_bars(isolated):
    _save_rule()
    sent: list[str] = []
    # 마지막 봉(14:00Z)은 평가 시각 13:50Z 이후 — 미래 봉이므로 교차 근거가 될 수 없다.
    bars = pd.DataFrame(
        {"close": [99.0, 99.5, 101.0]},
        index=pd.DatetimeIndex(["2026-08-03T13:40:00Z", "2026-08-03T13:45:00Z", "2026-08-03T14:00:00Z"]),
    )

    result = _cycle(bars, sent, as_of="2026-08-03T13:50:00Z")

    state = result["evaluations"][0]
    assert state["last_checked_at"] == "2026-08-03T13:50:00+00:00"
    assert state["status"] == "false"
    assert result["event_count"] == 0 and sent == []


def test_C_fix_alerts_worker_loads_fundamentals_relative_and_atr_fields(isolated):
    days = pd.bdate_range("2026-06-01", periods=70)
    daily_aapl = pd.DataFrame({"close": [100 * 1.01 ** i for i in range(70)]}, index=days)
    daily_qqq = pd.DataFrame({"close": [500.0] * 70}, index=days)
    intraday = pd.DataFrame({
        "high": [101 + i for i in range(20)], "low": [99 + i for i in range(20)],
        "close": [100 + i for i in range(20)],
    }, index=pd.date_range("2026-08-03T13:30:00Z", periods=20, freq="5min"))
    bars = {("AAPL", "1d"): daily_aapl, ("QQQ", "1d"): daily_qqq, ("AAPL", "5m"): intraday}
    _save_rule("fund", frequency="repeating", timeframe="1d",
               condition={"type": "fundamental", "field": "per", "operator": "less_than", "value": 30})
    _save_rule("rel", frequency="repeating", timeframe="1d",
               condition={"type": "relative_performance", "field": "relative_return_20d",
                          "operator": "greater_than", "value": 0})
    _save_rule("atr", frequency="repeating", timeframe="5m",
               condition={"type": "indicator", "field": "atr_14", "operator": "greater_than", "value": 0})
    loaded: list[str] = []

    result = chart_alert_worker.run_chart_alert_cycle(
        workspace_id="w1", notify=False, as_of="2026-09-30T00:00:00Z",
        load_bars_fn=lambda symbol, timeframe: bars.get((symbol, timeframe)),
        load_fundamentals_fn=lambda symbol: loaded.append(symbol) or {"per": 25.0},
    )

    by_id = {state["rule_id"]: state for state in result["evaluations"]}
    assert loaded == ["AAPL"]
    assert {rule_id: by_id[rule_id]["status"] for rule_id in by_id} == {"fund": "true", "rel": "true", "atr": "true"}
    assert result["evaluation_summary"]["matched"] == 3
    assert result["missing_bars"] == []


def test_C_fix_alerts_run_separates_unavailable_data_from_not_matched(isolated):
    _save_rule("fund", frequency="repeating", timeframe="1d",
               condition={"type": "fundamental", "field": "per", "operator": "less_than", "value": 30})
    _save_rule("price", frequency="repeating", timeframe="1d",
               condition={"type": "price", "operator": "greater_than", "value": 1000})
    daily = pd.DataFrame({"close": [99.0, 101.0]}, index=pd.bdate_range("2026-08-03", periods=2))

    result = chart_alert_worker.run_chart_alert_cycle(
        workspace_id="w1", notify=False, as_of="2026-09-30T00:00:00Z",
        load_bars_fn=lambda symbol, timeframe: daily,
        load_fundamentals_fn=lambda symbol: {"per": None},
    )

    by_id = {state["rule_id"]: state for state in result["evaluations"]}
    assert by_id["fund"]["status"] == "unknown" and by_id["fund"]["reason"] == "missing value"
    assert by_id["price"]["status"] == "false"
    assert result["evaluation_summary"]["unknown"] == 1
    assert result["evaluation_summary"]["not_matched"] == 1
    assert result["missing_fundamentals"] == ["AAPL"]
    assert result["status"] == "partial"


def test_C_fix_alerts_default_fundamentals_loader_covers_every_supported_field(monkeypatch):
    from agent_console import chart_alert_runner
    from dashboard import cached

    monkeypatch.setattr(cached, "valuation", lambda symbol: {
        "metrics": {"per": 20.0, "forward_pe": 18.0, "pbr": 3.0, "psr": 4.0, "roe": 0.2},
        "consensus": {"target_upside_pct": 12.0},
    })
    monkeypatch.setattr(cached, "financials", lambda symbol: {"trends": {"net_margin": 0.25}})

    values = chart_alert_worker.load_chart_alert_fundamentals("AAPL")

    assert set(values) == set(chart_alert_runner.FUNDAMENTAL_FIELDS)
    assert all(value is not None for value in values.values())


@pytest.mark.parametrize("condition, message", [
    ({"type": "fundamental", "field": "ev_ebitda", "operator": "less_than", "value": 10}, "ev_ebitda"),
    ({"type": "indicator", "field": "obv", "operator": "greater_than", "value": 0}, "obv"),
    ({"type": "event", "field": "earnings", "operator": "happened_within", "window": 1, "unit": "day"}, "event"),
])
def test_C_fix_alerts_storage_rejects_fields_worker_cannot_compute(isolated, condition, message):
    with pytest.raises(ValueError, match=message):
        _save_rule(condition=condition)
