from __future__ import annotations

from agent_console.chart_alerts import evaluate_price_alert


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
