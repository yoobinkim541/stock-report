from __future__ import annotations

import pandas as pd

from agent_console import chart_alert_runner


def _bars(values, *, timeframe="1d"):
    frequency = "1h" if timeframe == "1h" else "1D"
    index = pd.date_range("2026-08-01T10:00:00Z", periods=len(values), freq=frequency)
    return pd.DataFrame({"close": values, "volume": range(100, 100 + len(values))}, index=index)


def _multi_rule(rule_id="multi"):
    return {
        "id": rule_id,
        "symbol": "AAPL",
        "timeframe": "1d",
        "name": "AAPL and QQQ",
        "enabled": True,
        "frequency": "repeating",
        "condition": {
            "op": "all",
            "children": [
                {"type": "price", "symbol": "AAPL", "timeframe": "1d", "field": "close", "operator": "greater_than", "value": 100},
                {"type": "price", "symbol": "QQQ", "timeframe": "1h", "field": "close", "operator": "crossing_up", "value": 500},
            ],
        },
    }


def test_build_condition_contexts_uses_every_symbol_timeframe_and_source_timestamp():
    contexts = chart_alert_runner.build_condition_contexts(
        _multi_rule(),
        {("AAPL", "1d"): _bars([99, 101]), ("QQQ", "1h"): _bars([499, 501], timeframe="1h")},
    )

    assert set(contexts) == {("AAPL", "1d"), ("QQQ", "1h")}
    assert contexts[("AAPL", "1d")]["current"]["close"] == 101
    assert contexts[("QQQ", "1h")]["previous"]["close"] == 499
    assert contexts[("QQQ", "1h")]["as_of"].endswith("+00:00")


def test_evaluate_alert_rules_cross_symbol_records_full_audit_but_compact_event():
    states: list[dict] = []
    events = chart_alert_runner.evaluate_alert_rules(
        [_multi_rule()],
        {("AAPL", "1d"): _bars([99, 101]), ("QQQ", "1h"): _bars([499, 501], timeframe="1h")},
        state_sink=states,
    )

    assert len(events) == 1
    assert events[0]["alert_id"] == "multi"
    assert "evaluation_trace" not in events[0]
    assert states[0]["matched"] is True
    assert states[0]["missing_contexts"] == []
    assert len(states[0]["trace"]) >= 3
    assert set(states[0]["source_timestamps"]) == {"AAPL:1d", "QQQ:1h"}


def test_missing_required_context_is_audited_and_one_bad_rule_does_not_stop_others():
    states: list[dict] = []
    valid = {
        **_multi_rule("valid"),
        "condition": {"type": "price", "operator": "greater_than", "value": 100},
    }
    events = chart_alert_runner.evaluate_alert_rules(
        [_multi_rule("missing"), {"id": "bad", "symbol": "AAPL", "condition": {"op": "all", "children": "bad"}}, valid],
        {"AAPL": _bars([99, 101])},
        state_sink=states,
    )

    assert [event["alert_id"] for event in events] == ["valid"]
    by_id = {state["rule_id"]: state for state in states}
    assert by_id["missing"]["missing_contexts"] == [{"symbol": "QQQ", "timeframe": "1h"}]
    assert by_id["bad"]["matched"] is False
    assert "children" in by_id["bad"]["reason"]


def test_once_only_rule_remains_idempotent_and_preserves_triggered_state():
    rule = {
        **_multi_rule("once"),
        "frequency": "once",
        "last_state": {"triggered": True, "last_checked_at": "2026-08-01T00:00:00Z"},
    }
    states: list[dict] = []

    events = chart_alert_runner.evaluate_alert_rules(
        [rule],
        {("AAPL", "1d"): _bars([99, 101]), ("QQQ", "1h"): _bars([499, 501], timeframe="1h")},
        state_sink=states,
    )

    assert events == []
    assert states[0]["triggered"] is True
    assert states[0]["reason"] == "already_triggered"
