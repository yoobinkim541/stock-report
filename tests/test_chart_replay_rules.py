from __future__ import annotations

import pandas as pd

from dashboard import chart_replay, chart_replay_rules


def _bars():
    index = pd.date_range("2026-08-03 09:30", periods=6, freq="5min", tz="America/New_York")
    return pd.DataFrame({
        "Open": [99, 101, 103, 104, 98, 97],
        "High": [101, 103, 105, 106, 100, 99],
        "Low": [98, 100, 102, 103, 96, 95],
        "Close": [99, 102, 104, 105, 97, 96],
        "Volume": [1000] * 6,
    }, index=index)


def _session(cursor=1):
    return chart_replay.new_session(
        symbol="MSFT", timeframe="5m", cursor=cursor, initial_cash=10_000,
        session_id="rules-session",
    )


def _spec():
    return {
        "name": "price gate",
        "market": "us",
        "timeframe": "5m",
        "base_symbol": "MSFT",
        "universe": {"type": "list", "symbols": ["MSFT"]},
        "indicators": [],
        "rules": {
            "entry": [{"field": "close", "op": ">", "value": 100, "label": "entry-above-100"}],
            "exit": [{"field": "close", "op": "<", "value": 100, "label": "exit-below-100"}],
        },
        "sizing": {"type": "fixed_pct", "position_pct": 0.5},
        "costs": {"fees_bps": 0, "slippage_bps": 0, "spread_bps": 0},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass"},
    }


def test_strategy_packet_evaluates_only_visible_bars_and_records_exact_trace():
    packet = chart_replay_rules.strategy_packet(_spec())
    session = chart_replay_rules.attach_rule_packet(_session(cursor=1), packet)

    evaluated = chart_replay_rules.evaluate_and_apply(session, _bars())

    order = evaluated["orders"][-1]
    decision = evaluated["events"][-2]
    assert order["type"] == "market" and order["side"] == "buy"
    assert order["qty"] == 49
    assert decision["type"] == "rule_decision"
    assert decision["decision"] == "enter_long"
    assert decision["evaluations"][0]["trace"][0]["status"] == "true"
    assert decision["as_of"] == _bars().index[1].isoformat()


def test_rule_evaluation_is_idempotent_per_packet_and_cursor():
    packet = chart_replay_rules.strategy_packet(_spec())
    session = chart_replay_rules.attach_rule_packet(_session(cursor=1), packet)
    once = chart_replay_rules.evaluate_and_apply(session, _bars())
    twice = chart_replay_rules.evaluate_and_apply(once, _bars())
    assert twice == once


def test_future_signal_does_not_trigger_before_cursor_reaches_it():
    condition = {
        "type": "price", "field": "close", "operator": "less_than", "value": 98,
        "symbol": "MSFT", "timeframe": "5m",
    }
    packet = chart_replay_rules.condition_packet(
        condition, symbol="MSFT", timeframe="5m", action="enter_long", position_pct=0.5,
    )
    session = chart_replay_rules.attach_rule_packet(_session(cursor=2), packet)

    before = chart_replay_rules.evaluate_and_apply(session, _bars())
    assert before["orders"] == []
    assert before["events"][-1]["decision"] is None

    reached = chart_replay.advance(before, _bars(), steps=2)
    reached = chart_replay_rules.evaluate_and_apply(reached, _bars())
    assert reached["orders"][-1]["side"] == "buy"
    assert reached["events"][-1]["type"] == "order_submitted"


def test_strategy_exit_rule_submits_full_position_exit():
    packet = chart_replay_rules.strategy_packet(_spec())
    session = _session(cursor=4)
    session["cash"] = 4_900
    session["positions"] = {"MSFT": {"qty": 50, "avg_price": 102}}
    session = chart_replay_rules.attach_rule_packet(session, packet)

    evaluated = chart_replay_rules.evaluate_and_apply(session, _bars())

    assert evaluated["orders"][-1]["side"] == "sell"
    assert evaluated["orders"][-1]["qty"] == 50
    assert next(event for event in evaluated["events"] if event["type"] == "rule_decision")["decision"] == "exit_all"
