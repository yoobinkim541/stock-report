"""Event-driven backtest primitives."""

import pandas as pd

from ml.event_backtest import Order, simulate_orders


def test_simulate_orders_fills_on_next_open_and_applies_costs():
    prices = pd.DataFrame({
        "Open": [100.0, 102.0, 105.0],
        "Close": [101.0, 104.0, 106.0],
    }, index=pd.date_range("2026-01-01", periods=3, freq="D"))

    summary = simulate_orders(
        prices,
        [Order(ts=prices.index[0], symbol="A", side="buy", qty=10)],
        initial_cash=2000.0,
        market="US",
    )

    assert summary.fills[0].price == 102.0
    assert summary.final_nav > 2000.0
    assert summary.cost_paid > 0
    assert summary.positions["A"] == 10


def test_simulate_orders_rejects_final_bar_next_open_order():
    prices = pd.DataFrame({
        "Open": [100.0, 102.0],
        "Close": [101.0, 103.0],
    }, index=pd.date_range("2026-01-01", periods=2, freq="D"))

    summary = simulate_orders(
        prices,
        [Order(ts=prices.index[-1], symbol="A", side="buy", qty=10)],
        initial_cash=2000.0,
    )

    assert summary.fills == []
    assert summary.rejected[0]["reason"] == "no_next_bar"
    assert summary.final_nav == 2000.0
