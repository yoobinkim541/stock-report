from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import chart_replay  # noqa: E402


def _bars():
    index = pd.date_range("2026-08-03 09:30", periods=6, freq="5min", tz="America/New_York")
    return pd.DataFrame(
        {
            "Open": [100, 101, 103, 104, 100, 98],
            "High": [102, 104, 106, 108, 105, 101],
            "Low": [99, 100, 102, 99, 96, 94],
            "Close": [101, 103, 104, 101, 98, 96],
            "Volume": [1000] * 6,
        },
        index=index,
    )


def _session(**settings):
    return chart_replay.new_session(
        symbol="MSFT",
        timeframe="5m",
        cursor=0,
        initial_cash=10_000,
        settings={"fees_bps": 0, "slippage_bps": 0, "max_leverage": 1, **settings},
    )


def test_market_order_cannot_see_current_or_future_bar_before_advance():
    session = chart_replay.submit_order(_session(), {"id": "m1", "type": "market", "side": "buy", "qty": 10})

    assert session["fills"] == []
    assert session["orders"][0]["status"] == "pending"

    advanced = chart_replay.advance(session, _bars(), steps=1)

    assert advanced["cursor"] == 1
    assert advanced["fills"][0]["price"] == pytest.approx(101)
    assert advanced["fills"][0]["timestamp"] == _bars().index[1].isoformat()
    assert advanced["positions"]["MSFT"]["qty"] == 10


def test_limit_and_stop_orders_use_only_subsequent_bar_range():
    session = chart_replay.submit_order(_session(), {"id": "l1", "type": "limit", "side": "buy", "qty": 5, "price": 99})
    session = chart_replay.advance(session, _bars(), steps=3)
    assert session["orders"][0]["status"] == "filled"
    assert session["fills"][0]["cursor"] == 3
    assert session["fills"][0]["price"] == pytest.approx(99)

    stop = chart_replay.submit_order(_session(), {"id": "s1", "type": "stop", "side": "buy", "qty": 2, "price": 105})
    stop = chart_replay.advance(stop, _bars(), steps=2)
    assert stop["fills"][0]["cursor"] == 2
    assert stop["fills"][0]["price"] == pytest.approx(105)


def test_bracket_children_activate_after_entry_and_same_bar_collision_is_conservative():
    session = chart_replay.submit_order(
        _session(),
        {"id": "b1", "type": "market", "side": "buy", "qty": 10,
         "bracket": {"stop": 100, "target": 107}},
    )
    session = chart_replay.advance(session, _bars(), steps=1)

    children = {row["role"]: row for row in session["orders"] if row.get("parent_id") == "b1"}
    assert children["stop"]["status"] == "pending"
    assert children["target"]["status"] == "pending"
    assert children["stop"]["active_after_cursor"] == 1

    session = chart_replay.advance(session, _bars(), steps=2)

    exit_fill = session["fills"][-1]
    assert exit_fill["order_id"].endswith(":stop")
    assert exit_fill["price"] == pytest.approx(100)
    assert session["positions"] == {}
    assert children["target"]["id"] in {row["id"] for row in session["orders"] if row["status"] == "cancelled"}


def test_partial_exit_preserves_remaining_position_and_average_cost():
    session = chart_replay.submit_order(_session(), {"id": "in", "type": "market", "side": "buy", "qty": 10})
    session = chart_replay.advance(session, _bars(), steps=1)
    session = chart_replay.submit_order(session, {"id": "half", "type": "market", "side": "sell", "qty": 4})
    session = chart_replay.advance(session, _bars(), steps=1)

    assert session["positions"]["MSFT"]["qty"] == 6
    assert session["positions"]["MSFT"]["avg_price"] == pytest.approx(101)
    assert session["realized_pnl"] == pytest.approx((103 - 101) * 4)


def test_partial_exit_resizes_pending_bracket_children_to_remaining_position():
    session = chart_replay.submit_order(
        _session(),
        {"id": "entry", "type": "market", "side": "buy", "qty": 10,
         "bracket": {"stop": 100, "target": 107}},
    )
    session = chart_replay.advance(session, _bars(), steps=1)
    session = chart_replay.submit_order(
        session,
        {"id": "manual-half", "type": "market", "side": "sell", "qty": 4},
    )
    session = chart_replay.advance(session, _bars(), steps=1)

    children = [row for row in session["orders"] if row.get("parent_id") == "entry"]
    assert session["positions"]["MSFT"]["qty"] == 6
    assert {row["qty"] for row in children if row["status"] == "pending"} == {6}
    assert {event["order_id"] for event in session["events"] if event["type"] == "order_resized"} == {
        "entry:stop", "entry:target",
    }


def test_market_order_at_final_bar_remains_pending_without_a_future_open():
    session = chart_replay.advance(_session(), _bars(), steps=99)
    session = chart_replay.submit_order(
        session,
        {"id": "late", "type": "market", "side": "buy", "qty": 1},
    )

    unchanged = chart_replay.advance(session, _bars(), steps=1)

    assert unchanged["cursor"] == len(_bars()) - 1
    assert unchanged["fills"] == []
    assert unchanged["orders"][-1]["status"] == "pending"


def test_fees_slippage_leverage_nav_and_drawdown_are_explicit():
    session = _session(fees_bps=10, slippage_bps=20, max_leverage=2)
    session = chart_replay.submit_order(session, {"id": "lev", "type": "market", "side": "buy", "qty": 150})
    session = chart_replay.advance(session, _bars(), steps=1)

    fill = session["fills"][0]
    assert fill["price"] == pytest.approx(101 * 1.002)
    assert fill["fee"] == pytest.approx(fill["price"] * 150 * 0.001)
    assert session["cash"] < 0
    assert session["metrics"]["gross_exposure"] > session["metrics"]["nav"]

    session = chart_replay.advance(session, _bars(), steps=4)
    assert session["metrics"]["drawdown"] < 0
    assert session["metrics"]["max_drawdown"] <= session["metrics"]["drawdown"]


def test_order_rejected_when_it_exceeds_leverage_or_available_shares():
    too_large = chart_replay.submit_order(_session(), {"id": "big", "type": "market", "side": "buy", "qty": 200})
    too_large = chart_replay.advance(too_large, _bars(), steps=1)
    assert too_large["orders"][0]["status"] == "rejected"
    assert too_large["orders"][0]["reason"] == "leverage"

    sell = chart_replay.submit_order(_session(), {"id": "sell", "type": "market", "side": "sell", "qty": 1})
    sell = chart_replay.advance(sell, _bars(), steps=1)
    assert sell["orders"][0]["status"] == "rejected"
    assert sell["orders"][0]["reason"] == "shares"


def test_rewind_requires_a_new_branch_instead_of_mutating_history():
    session = chart_replay.advance(_session(), _bars(), steps=3)

    with pytest.raises(ValueError, match="branch"):
        chart_replay.set_cursor(session, 1)

    branch = chart_replay.branch_session(session, cursor=1, session_id="branch-1")
    assert branch["id"] == "branch-1"
    assert branch["cursor"] == 1
    assert branch["parent_id"] == session["id"]
    assert all(int(event.get("cursor", 0)) <= 1 for event in branch["events"])


def test_branch_rebuilds_cash_positions_and_order_status_at_cursor():
    session = chart_replay.submit_order(_session(), {"id": "entry", "type": "market", "side": "buy", "qty": 10})
    session = chart_replay.advance(session, _bars(), steps=3)

    before_fill = chart_replay.branch_session(session, cursor=0, session_id="before")
    after_fill = chart_replay.branch_session(session, cursor=1, session_id="after", bars=_bars())

    assert before_fill["cash"] == pytest.approx(10_000)
    assert before_fill["positions"] == {}
    assert before_fill["fills"] == []
    assert before_fill["orders"][0]["status"] == "pending"
    assert after_fill["cash"] == pytest.approx(8_990)
    assert after_fill["positions"]["MSFT"] == {"qty": 10, "avg_price": pytest.approx(101)}
    assert after_fill["orders"][0]["status"] == "filled"
    assert after_fill["metrics"]["nav"] == pytest.approx(10_020)
