from __future__ import annotations

import pandas as pd

from agent_console.chart_alerts import evaluate_chart_alert, evaluate_price_alert


def _rule(operator: str, value: float = 100.0, **extra):
    return {
        "id": "alert-1",
        "symbol": "AAPL",
        "name": "AAPL level",
        "condition": {"type": "price", "operator": operator, "value": value},
        **extra,
    }


def test_price_alert_crossing_operators_use_previous_and_current_price():
    assert evaluate_price_alert(_rule("crossing"), previous_price=99.0, current_price=101.0)["triggered"] is True
    assert evaluate_price_alert(_rule("crossing"), previous_price=101.0, current_price=99.0)["triggered"] is True
    assert evaluate_price_alert(_rule("crossing"), previous_price=101.0, current_price=102.0)["triggered"] is False

    assert evaluate_price_alert(_rule("crossing_up"), previous_price=99.0, current_price=100.5)["triggered"] is True
    assert evaluate_price_alert(_rule("crossing_up"), previous_price=100.5, current_price=99.0)["triggered"] is False

    assert evaluate_price_alert(_rule("crossing_down"), previous_price=101.0, current_price=99.5)["triggered"] is True
    assert evaluate_price_alert(_rule("crossing_down"), previous_price=99.5, current_price=101.0)["triggered"] is False


def test_price_alert_threshold_operators_and_message_payload():
    greater = evaluate_price_alert(_rule("greater_than", 120.0), previous_price=119.0, current_price=120.01, as_of="2026-08-01T12:00:00Z")
    less = evaluate_price_alert(_rule("less_than", 80.0), previous_price=82.0, current_price=79.99)

    assert greater["triggered"] is True
    assert greater["event"]["symbol"] == "AAPL"
    assert greater["event"]["operator"] == "greater_than"
    assert greater["event"]["threshold"] == 120.0
    assert greater["event"]["current_price"] == 120.01
    assert greater["event"]["as_of"] == "2026-08-01T12:00:00Z"
    assert less["triggered"] is True


def test_price_alert_frequency_once_suppresses_after_trigger():
    rule = _rule("greater_than", 120.0, frequency="once", last_state={"triggered": True})

    result = evaluate_price_alert(rule, previous_price=119.0, current_price=121.0)

    assert result["triggered"] is False
    assert result["reason"] == "already_triggered"


def test_multi_condition_alert_requires_all_conditions_to_trigger():
    rule = {
        "id": "alert-2",
        "symbol": "AAPL",
        "name": "AAPL price + RSI",
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_up", "value": 100.0},
                {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 70.0},
            ]
        },
    }

    result = evaluate_chart_alert(
        rule,
        previous_price=99.0,
        current_price=101.0,
        previous_values={"rsi_14": 68.0},
        current_values={"rsi_14": 69.0},
        as_of="2026-08-01T12:00:00Z",
    )
    failed = evaluate_chart_alert(
        rule,
        previous_price=99.0,
        current_price=101.0,
        previous_values={"rsi_14": 71.0},
        current_values={"rsi_14": 72.0},
    )

    assert result["triggered"] is True
    assert result["event"]["condition_count"] == 2
    assert result["event"]["matched_conditions"] == ["price:crossing_up", "indicator:rsi_14:less_than"]
    assert failed["triggered"] is False
    assert failed["reason"] == "condition_not_met"


def test_drawing_line_alert_interpolates_threshold_at_current_time():
    rule = {
        "id": "alert-3",
        "symbol": "AAPL",
        "name": "trendline break",
        "condition": {
            "type": "drawing_line",
            "operator": "crossing_up",
            "x0": "2026-08-01T10:00:00Z",
            "y0": 100.0,
            "x1": "2026-08-01T12:00:00Z",
            "y1": 110.0,
        },
    }

    result = evaluate_chart_alert(
        rule,
        previous_price=103.0,
        current_price=106.0,
        previous_values={"time": "2026-08-01T10:30:00Z"},
        current_values={"time": "2026-08-01T11:00:00Z"},
        as_of="2026-08-01T11:00:00Z",
    )

    assert result["triggered"] is True
    assert result["event"]["threshold"] == 105.0
    assert result["event"]["operator"] == "crossing_up"


def test_nested_any_and_none_alerts_delegate_to_shared_dsl():
    rule = {
        "id": "alert-nested",
        "symbol": "AAPL",
        "timeframe": "1d",
        "condition": {
            "op": "all",
            "children": [
                {"type": "price", "operator": "crossing_up", "value": 100},
                {"op": "none", "children": [
                    {"type": "indicator", "field": "rsi_14", "operator": "greater_than", "value": 80},
                ]},
            ],
        },
    }

    result = evaluate_chart_alert(
        rule,
        previous_price=99,
        current_price=101,
        previous_values={"rsi_14": 60},
        current_values={"rsi_14": 65},
        as_of="2026-08-08T12:00:00Z",
    )

    assert result["triggered"] is True
    assert result["event"]["condition_count"] == 2
    assert result["event"]["evaluation_trace"]


def test_missing_indicator_inside_none_does_not_trigger_alert():
    rule = {
        "id": "alert-unknown-none",
        "symbol": "AAPL",
        "condition": {
            "op": "none",
            "children": [
                {"type": "indicator", "field": "missing", "operator": "greater_than", "value": 1},
            ],
        },
    }

    result = evaluate_chart_alert(
        rule, previous_price=99, current_price=101, previous_values={}, current_values={},
    )

    assert result["triggered"] is False
    assert result["reason"] == "missing_price"


def test_alert_runner_computes_rsi_and_evaluates_saved_rules_from_bars():
    from agent_console.chart_alert_runner import evaluate_alert_rules

    idx = pd.date_range("2026-08-01 09:30", periods=20, freq="5min", tz="UTC")
    closes = [
        100, 99, 98, 97, 96,
        95, 96, 97, 98, 99,
        98, 97, 96, 95, 94,
        95, 96, 97, 99, 101,
    ]
    bars = pd.DataFrame({"close": closes}, index=idx)
    rule = {
        "id": "alert-rsi-1",
        "symbol": "AAPL",
        "name": "AAPL price + RSI",
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_up", "value": 100.0},
                {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 100.0},
            ]
        },
        "frequency": "once",
        "enabled": True,
    }

    events = evaluate_alert_rules([rule], {"AAPL": bars})

    assert len(events) == 1
    event = events[0]
    assert event["alert_id"] == "alert-rsi-1"
    assert event["symbol"] == "AAPL"
    assert event["current_price"] == 101.0
    assert event["previous_price"] == 99.0
    assert event["condition_count"] == 2
    assert event["matched_conditions"] == ["price:crossing_up", "indicator:rsi_14:less_than"]
    assert event["indicator_values"]["rsi_14"] < 100.0


def test_alert_dispatcher_formats_and_sends_triggered_events():
    from agent_console.chart_alert_dispatcher import dispatch_alert_events

    sent: list[str] = []
    event = {
        "alert_id": "alert-rsi-1",
        "name": "AAPL price + RSI",
        "symbol": "AAPL",
        "as_of": "2026-08-01T12:00:00Z",
        "operator": "all",
        "threshold": 100.0,
        "previous_price": 99.0,
        "current_price": 101.0,
        "matched_conditions": ["price:crossing_up", "indicator:rsi_14:less_than"],
        "message": "AAPL crossed 100 with RSI filter",
    }

    result = dispatch_alert_events([event], send_fn=lambda text: sent.append(text) or True)

    assert result["attempted"] == 1
    assert result["delivered"] == 1
    assert sent == [
        "🔔 차트 알림\n"
        "AAPL · AAPL price + RSI\n"
        "현재가 101.00 · 이전 99.00\n"
        "조건 price:crossing_up, indicator:rsi_14:less_than\n"
        "AAPL crossed 100 with RSI filter\n"
        "2026-08-01T12:00:00Z"
    ]


def test_chart_alert_worker_main_exposes_cron_friendly_cli(monkeypatch, capsys):
    from agent_console import chart_alert_worker

    calls: list[dict] = []
    monkeypatch.setattr(chart_alert_worker, "run_chart_alert_cycle", lambda **kwargs: calls.append(kwargs) or {
        "ok": True,
        "rule_count": 2,
        "event_count": 1,
        "missing_bars": [],
        "notification": {"attempted": 1, "delivered": 1, "failed": 0, "failures": []},
    })

    code = chart_alert_worker.main(["--workspace-id", "workspace-1", "--symbol", "AAPL", "--notify"])

    assert code == 0
    assert calls == [{
        "workspace_id": "workspace-1",
        "symbols": ["AAPL"],
        "notify": True,
        "limit": 200,
    }]
    assert '"event_count": 1' in capsys.readouterr().out
