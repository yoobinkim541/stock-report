from __future__ import annotations

import pandas as pd
import pytest

from ml.strategy_studio import (
    FillEvent,
    OrderIntent,
    PositionState,
    StrategySpec,
    build_strategy_report,
    run_strategy_backtest,
)
from ml.strategy_studio.execution import (
    ExecutionConfig,
    apply_fills,
    execute_intents,
    execution_defaults,
    run_execution_backtest,
)


def _bars(rows: list[dict[str, float]], start: str = "2026-01-01") -> pd.DataFrame:
    return pd.DataFrame(rows, index=pd.date_range(start, periods=len(rows), freq="D"))


def test_execution_config_from_dict_accepts_cost_bps_and_json_booleans():
    config = ExecutionConfig.from_dict({
        "latency_bars": "2", "cost_bps": 7, "partial_fill": "false",
    })

    assert config.latency_bars == 2
    assert config.fees_bps == 7
    assert config.partial_fill is False


def test_market_order_uses_next_bar_and_applies_fee_slippage_and_partial_fill():
    bars = _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0},
        {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 100.0},
    ])
    intent = OrderIntent(
        symbol="AAPL",
        side="buy",
        quantity=80,
        decision_at=bars.index[0],
        decision_price=100.5,
    )

    fills = execute_intents(
        [intent],
        {"AAPL": bars},
        ExecutionConfig(
            latency_bars=1,
            fees_bps=5,
            slippage_bps=10,
            spread_bps=5,
            max_participation_rate=0.5,
            partial_fill=True,
        ),
    )

    assert len(fills) == 1
    assert fills[0].filled_at == "2026-01-02T00:00:00+00:00"
    assert fills[0].filled_qty == 50
    assert fills[0].fill_price > 101.0
    assert fills[0].fee > 0
    assert fills[0].slippage > 0
    assert fills[0].status == "partial"


def test_sell_stop_gap_fills_at_open_not_stop_price():
    bars = {"AAPL": _bars([
        {"open": 90.0, "high": 92.0, "low": 88.0, "close": 91.0, "volume": 1000.0},
    ], start="2026-01-02")}
    intent = OrderIntent(
        symbol="AAPL",
        side="sell",
        quantity=10,
        order_type="stop",
        stop_price=95.0,
        decision_at="2026-01-01",
    )

    fills = execute_intents([intent], bars, ExecutionConfig(latency_bars=0))

    assert fills[0].fill_price == pytest.approx(90.0)
    assert fills[0].reason == "stop_gap"


def test_limit_order_requires_intrabar_price_and_records_cancelled_order():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        {"open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0, "volume": 1000.0},
    ])}
    limit = OrderIntent(
        symbol="AAPL", side="buy", quantity=10, order_type="limit", limit_price=98.0,
        decision_at=bars["AAPL"].index[0], metadata={"cancelled": True},
    )

    fills = execute_intents([limit], bars, ExecutionConfig(latency_bars=1))

    assert fills[0].status == "cancelled"
    assert fills[0].filled_qty == 0
    assert fills[0].reason == "cancelled_by_strategy"


def test_limit_order_fills_only_when_bar_range_reaches_limit():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        {"open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0, "volume": 1000.0},
    ])}
    intent = OrderIntent(
        symbol="AAPL", side="buy", quantity=10, order_type="limit", limit_price=101.5,
        decision_at=bars["AAPL"].index[0],
    )

    fills = execute_intents([intent], bars, ExecutionConfig(latency_bars=1))

    assert fills[0].status == "filled"
    assert fills[0].fill_price == pytest.approx(101.5)
    assert fills[0].reason == "limit_touch"


def test_zero_volume_and_non_partial_liquidity_are_rejected():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 0.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
    ])}
    intents = [
        OrderIntent("AAPL", "buy", 10, bars["AAPL"].index[0]),
        OrderIntent("AAPL", "buy", 20, bars["AAPL"].index[0]),
    ]

    fills = execute_intents(
        intents,
        bars,
        ExecutionConfig(latency_bars=1, max_participation_rate=0.5, partial_fill=False),
    )

    assert [fill.status for fill in fills] == ["rejected", "rejected"]
    assert all(fill.reason == "insufficient_liquidity" for fill in fills)


def test_apply_fills_updates_average_price_and_realized_pnl():
    position = PositionState(symbol="AAPL", quantity=0, average_price=0)
    fills = [
        FillEvent(
            run_id="run-1", symbol="AAPL", side="buy", requested_qty=10, filled_qty=10,
            decision_price=100, fill_price=100, status="filled",
            decision_at="2026-01-01", filled_at="2026-01-02", fee=1,
        ),
        FillEvent(
            run_id="run-1", symbol="AAPL", side="sell", requested_qty=4, filled_qty=4,
            decision_price=110, fill_price=110, status="filled",
            decision_at="2026-01-02", filled_at="2026-01-03", fee=1,
        ),
    ]

    updated = apply_fills(position, fills)

    assert updated.quantity == 6
    assert updated.average_price == pytest.approx(100)
    assert updated.realized_pnl == pytest.approx(38)
    assert updated.as_of == "2026-01-03T00:00:00+00:00"


def test_target_weights_create_deterministic_next_bar_ledger_and_summary():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10000.0},
        {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 10000.0},
        {"open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0, "volume": 10000.0},
    ])}
    targets = pd.DataFrame(
        {"AAPL": [1.0, 0.0]},
        index=bars["AAPL"].index[:2],
    )
    config = ExecutionConfig(initial_cash=100_000, latency_bars=1)

    first = run_execution_backtest(targets, bars, config)
    second = run_execution_backtest(targets, bars, config)

    assert [fill.filled_at for fill in first.fills] == [
        "2026-01-02T00:00:00+00:00", "2026-01-03T00:00:00+00:00"
    ]
    assert first.fills == second.fills
    assert first.equity.equals(second.equity)
    assert first.summary["trade_count"] == 2
    assert first.summary["rejected_count"] == 0
    assert first.summary["filled_quantity"] == pytest.approx(2000)
    assert first.positions["AAPL"].quantity == 0


def test_execution_defaults_are_profile_specific_and_report_exposes_fill_fields():
    assert execution_defaults("kr_intraday").max_participation_rate < 1.0
    assert execution_defaults("global_swing").latency_bars == 1

    bars = pd.DataFrame({
        "AAPL__open": [100.0, 101.0, 102.0],
        "AAPL__high": [101.0, 103.0, 103.0],
        "AAPL__low": [99.0, 100.0, 101.0],
        "AAPL__close": [100.0, 102.0, 102.0],
        "AAPL__volume": [1000.0, 1000.0, 1000.0],
    }, index=pd.date_range("2026-01-01", periods=3))
    spec = StrategySpec.from_dict({
        "name": "explicit execution",
        "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "factor", "plugin": "momentum", "lookback": 1},
        "portfolio": {"optimizer": "equal_weight", "max_position_pct": 1.0},
        "execution": {"latency_bars": 1, "fees_bps": 2},
    })

    run = run_strategy_backtest(spec, bars)
    report = build_strategy_report(run)

    assert run.ok is True
    assert run.trades
    assert {
        "decision_at", "submitted_at", "filled_at", "requested_qty", "filled_qty",
        "decision_price", "fill_price", "fee", "slippage", "status",
    }.issubset(run.trades[0])
    assert report["summary"]["trade_count"] == run.metrics["trade_count"]
    assert report["execution"]["trade_count"] == run.metrics["trade_count"]
