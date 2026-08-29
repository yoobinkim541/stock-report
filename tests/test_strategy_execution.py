from __future__ import annotations

import json

import pandas as pd
import pytest

from ml.strategy_studio import (
    ExecutionResult,
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
from ml.strategy_studio.engine import (
    _close_panel_from_store,
    _execution_bars,
    _normalize_price_store,
    compile_strategy,
)
from ml.strategy_studio.profiles import profile_health


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


def test_partial_target_orders_reconcile_executed_quantity_and_never_oversell():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
    ])}
    targets = pd.DataFrame(
        {"AAPL": [1.0, 1.0, 0.0, 0.0]},
        index=bars["AAPL"].index[:4],
    )

    result = run_execution_backtest(
        targets,
        bars,
        ExecutionConfig(initial_cash=1_000, latency_bars=1, max_participation_rate=0.5),
    )

    assert [intent.quantity for intent in result.intents] == pytest.approx([10, 5, 10, 5])
    assert [fill.filled_qty for fill in result.fills] == pytest.approx([5, 5, 5, 5])
    assert result.positions["AAPL"].quantity == pytest.approx(0)
    assert result.equity["cash"].iloc[-1] == pytest.approx(1_000)
    assert result.equity["nav"].iloc[-1] == pytest.approx(1_000)
    assert (result.equity["nav"] >= 0).all()


def test_delayed_reduction_does_not_sell_against_pending_buy():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
    ])}
    targets = pd.DataFrame(
        {"AAPL": [1.0, 1.0, 0.0, 0.0]},
        index=bars["AAPL"].index[:4],
    )

    result = run_execution_backtest(
        targets,
        bars,
        ExecutionConfig(initial_cash=1_000, latency_bars=2, max_participation_rate=0.5),
    )

    assert [intent.side for intent in result.intents] == ["buy", "sell"]
    assert [fill.filled_qty for fill in result.fills] == pytest.approx([5, 5])
    assert result.positions["AAPL"].quantity == pytest.approx(0)


def test_target_reversal_cancels_pending_buy_before_latency_expiry():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    targets = pd.DataFrame(
        {"AAPL": [1.0, 0.0, 0.0]},
        index=bars["AAPL"].index[:3],
    )

    result = run_execution_backtest(
        targets,
        bars,
        ExecutionConfig(initial_cash=1_000, latency_bars=2),
    )

    assert len(result.intents) == 1
    assert [(fill.status, fill.reason) for fill in result.fills] == [("cancelled", "target_reduced")]
    assert result.positions["AAPL"].quantity == pytest.approx(0)
    assert result.equity["nav"].iloc[-1] == pytest.approx(1_000)


def test_target_reduction_replaces_pending_buy_with_current_delta():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    targets = pd.DataFrame(
        {"AAPL": [1.0, 0.5, 0.5, 0.5]},
        index=bars["AAPL"].index[:4],
    )

    result = run_execution_backtest(
        targets,
        bars,
        ExecutionConfig(initial_cash=1_000, latency_bars=2),
    )

    assert [intent.quantity for intent in result.intents] == pytest.approx([10, 5])
    assert [(fill.status, fill.reason, fill.filled_qty) for fill in result.fills] == [
        ("cancelled", "target_reduced", 0),
        ("filled", "market_open", 5),
    ]
    assert result.positions["AAPL"].quantity == pytest.approx(5)


def test_partial_fill_issues_residual_order_on_later_target():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
    ])}
    targets = pd.DataFrame(
        {"AAPL": [1.0, 1.0]},
        index=bars["AAPL"].index[:2],
    )

    result = run_execution_backtest(
        targets,
        bars,
        ExecutionConfig(initial_cash=1_000, latency_bars=1, max_participation_rate=0.5),
    )

    assert [intent.quantity for intent in result.intents] == pytest.approx([10, 5])
    assert [fill.filled_qty for fill in result.fills] == pytest.approx([5, 5])


def test_execution_result_to_dict_is_directly_json_safe():
    result = ExecutionResult(
        equity=pd.DataFrame({"nav": [1_000.0]}, index=pd.date_range("2026-01-01", periods=1)),
        summary={"final_nav": 1_000.0},
    )

    payload = result.to_dict()

    assert payload["equity"]["columns"] == ["nav"]
    json.dumps(payload)


def test_participation_cap_is_aggregated_across_same_bar_intents():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    intents = [
        OrderIntent("AAPL", "buy", 40, bars["AAPL"].index[0], run_id="intent-a"),
        OrderIntent("AAPL", "buy", 40, bars["AAPL"].index[0], run_id="intent-b"),
    ]

    fills = execute_intents(
        intents,
        bars,
        ExecutionConfig(latency_bars=1, max_participation_rate=0.5),
    )

    assert sum(fill.filled_qty for fill in fills) == pytest.approx(50)
    assert [fill.filled_qty for fill in fills] == pytest.approx([40, 10])
    assert fills[1].status == "partial"


def test_execution_result_and_strategy_trace_are_json_safe():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        {"open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 1000.0},
    ])}
    targets = pd.DataFrame({"AAPL": [1.0]}, index=bars["AAPL"].index[:1])
    result = run_execution_backtest(targets, bars, ExecutionConfig(initial_cash=10_000))

    json.dumps(result.to_dict())

    prices = bars["AAPL"].rename(columns=lambda field: f"AAPL__{field}")
    spec = StrategySpec.from_dict({
        "name": "json trace",
        "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "factor", "plugin": "momentum", "lookback": 1},
        "portfolio": {"optimizer": "equal_weight", "max_position_pct": 1.0},
        "execution": {"latency_bars": 1},
    })
    run = run_strategy_backtest(spec, prices)
    report = build_strategy_report(run)

    json.dumps(run.signals)
    json.dumps(report["signals"])
    json.dumps(report)


def test_invalid_signal_engine_trace_is_json_safe():
    prices = pd.DataFrame({"AAPL": [100.0, 101.0]}, index=pd.date_range("2026-01-01", periods=2))
    spec = StrategySpec.from_dict({
        "name": "invalid json trace",
        "base_symbol": "AAPL",
        "universe": {"type": "list", "symbols": ["AAPL"]},
        "signal": {"type": "factor", "plugin": "value"},
    })

    run = run_strategy_backtest(spec, prices)

    assert run.ok is False
    json.dumps(run.signals)


def test_no_eligible_bar_is_cancelled_when_configured():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
    ])}
    intent = OrderIntent("AAPL", "buy", 10, bars["AAPL"].index[0])

    fills = execute_intents([intent], bars, ExecutionConfig(latency_bars=1, cancel_unfilled=True))

    assert fills[0].status == "cancelled"
    assert fills[0].reason == "no_eligible_bar"


def test_same_time_same_symbol_ordering_is_input_order_independent():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    first = OrderIntent("AAPL", "buy", 30, bars["AAPL"].index[0], reason="intent-a")
    second = OrderIntent("AAPL", "buy", 30, bars["AAPL"].index[0], reason="intent-b")
    config = ExecutionConfig(latency_bars=1, max_participation_rate=0.5)

    ordered = execute_intents([first, second], bars, config)
    shuffled = execute_intents([second, first], bars, config)

    assert ordered == shuffled
    assert all(fill.run_id.startswith("execution-") for fill in ordered)


def test_target_reconciliation_is_causal_to_the_current_bar():
    targets = pd.DataFrame(
        {"AAPL": [1.0, 1.0]},
        index=pd.date_range("2026-01-01", periods=2),
    )
    base_bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
    ])}
    changed_future = {"AAPL": base_bars["AAPL"].copy()}
    changed_future["AAPL"].loc[changed_future["AAPL"].index[2], ["open", "high", "low"]] = float("nan")
    config = ExecutionConfig(initial_cash=1_000, latency_bars=2, max_participation_rate=0.5)

    base = run_execution_backtest(targets, base_bars, config)
    changed = run_execution_backtest(targets, changed_future, config)

    assert base.intents == changed.intents
    cutoff = base_bars["AAPL"].index[1].tz_localize("UTC")
    assert [fill for fill in base.fills if fill.filled_at and pd.Timestamp(fill.filled_at) <= cutoff] == [
        fill for fill in changed.fills if fill.filled_at and pd.Timestamp(fill.filled_at) <= cutoff
    ]
    assert base.equity.loc[:cutoff].equals(changed.equity.loc[:cutoff])


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


def test_kr_intraday_profile_pauses_when_1m_sink_is_stale():
    decision = profile_health(
        "kr_intraday",
        last_bar_at="2026-08-28T10:00:00+09:00",
        now="2026-08-28T10:05:30+09:00",
        max_age_seconds=60,
    )

    assert decision.status == "pause"
    assert decision.reason == "stale_intraday_bar"
    assert decision.age_seconds == pytest.approx(330.0)


def test_extended_us_uses_wider_costs_than_regular_session():
    regular = execution_defaults("global_swing", session="regular")
    extended = execution_defaults("extended_us", session="extended")

    assert extended.spread_bps > regular.spread_bps
    assert extended.max_participation_rate < regular.max_participation_rate


def test_paused_profile_blocks_new_entries_but_allows_configured_exits():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    config = ExecutionConfig(
        profile="kr_intraday",
        profile_health={"status": "pause", "reason": "stale_intraday_bar", "age_seconds": 330.0},
        latency_bars=1,
    )
    intents = [
        OrderIntent("AAPL", "buy", 10, bars["AAPL"].index[0]),
        OrderIntent("AAPL", "sell", 10, bars["AAPL"].index[0]),
    ]

    fills = execute_intents(intents, bars, config)

    assert [(fill.side, fill.status, fill.reason) for fill in fills] == [
        ("buy", "cancelled", "strategy_paused"),
        ("sell", "filled", "market_open"),
    ]


def test_all_failed_quote_sources_pause_new_entries():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    config = ExecutionConfig(
        quote_health={
            "kis_ws": {"status": "pause", "reason": "stale_heartbeat"},
            "rest": {"status": "pause", "reason": "missing_heartbeat"},
        },
        latency_bars=1,
    )
    intent = OrderIntent("AAPL", "buy", 10, bars["AAPL"].index[0])

    fill = execute_intents([intent], bars, config)[0]

    assert fill.status == "cancelled"
    assert fill.reason == "strategy_paused"


def test_execution_config_parses_pause_policy_booleans_from_saved_payload():
    config = ExecutionConfig.from_dict({
        "pause_on_stale": "false",
        "allow_exits_on_pause": "false",
    })

    assert config.pause_on_stale is False
    assert config.allow_exits_on_pause is False


def test_profile_health_rejects_nonfinite_freshness_threshold():
    with pytest.raises(ValueError, match="max_age_seconds"):
        profile_health(
            "kr_intraday",
            last_bar_at="2026-08-28T10:00:00+09:00",
            now="2026-08-28T10:05:30+09:00",
            max_age_seconds=float("nan"),
        )


def test_pending_entry_is_paused_if_health_turns_stale_before_fill():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    index = bars["AAPL"].index
    config = ExecutionConfig(
        profile_health={
            "timeline": {
                index[0].isoformat(): {"status": "fresh", "reason": "fresh", "age_seconds": 0.0},
                index[1].isoformat(): {"status": "pause", "reason": "stale_intraday_bar"},
            },
        },
        latency_bars=1,
    )
    result = run_execution_backtest(
        pd.DataFrame({"AAPL": [1.0]}, index=index[:1]),
        bars,
        config,
    )

    assert result.fills[0].reason == "strategy_paused"
    assert result.fills[0].status == "cancelled"
    assert result.diagnostics[0]["type"] == "strategy_paused"


def test_explicit_pause_without_reason_remains_conservative():
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    config = ExecutionConfig(profile_health={"status": "pause"}, latency_bars=1)
    intent = OrderIntent("AAPL", "buy", 10, bars["AAPL"].index[0])

    fill = execute_intents([intent], bars, config)[0]

    assert fill.status == "cancelled"
    assert fill.reason == "strategy_paused"


@pytest.mark.parametrize("age_seconds", [None, "bad", float("nan"), float("inf"), -1.0])
def test_malformed_fresh_health_pauses_entries_but_allows_configured_exits(age_seconds):
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    config = ExecutionConfig(
        profile_health={"status": "fresh", "reason": "fresh", "age_seconds": age_seconds},
        latency_bars=1,
    )
    intents = [
        OrderIntent("AAPL", "buy", 10, bars["AAPL"].index[0]),
        OrderIntent("AAPL", "sell", 10, bars["AAPL"].index[0]),
    ]

    fills = execute_intents(intents, bars, config)

    assert [(fill.side, fill.status, fill.reason) for fill in fills] == [
        ("buy", "cancelled", "strategy_paused"),
        ("sell", "filled", "market_open"),
    ]


@pytest.mark.parametrize("health", [
    {"status": "available"},
    {"status": "fresh", "age_seconds": 0.0, "timestamp": "not-a-timestamp"},
    {"status": "fresh", "age_seconds": 0.0, "now": float("nan")},
])
def test_health_aliases_and_invalid_clock_fields_fail_closed(health):
    bars = {"AAPL": _bars([
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100.0},
    ])}
    intent = OrderIntent("AAPL", "buy", 10, bars["AAPL"].index[0])

    fill = execute_intents(
        [intent],
        bars,
        ExecutionConfig(profile_health=health, latency_bars=1),
    )[0]

    assert fill.status == "cancelled"
    assert fill.reason == "strategy_paused"


def test_public_execution_bars_preserve_health_attrs_and_block_incomplete_snapshot():
    index = pd.date_range("2026-01-01", periods=2, freq="D")
    prices = pd.DataFrame({
        "AAPL__open": [100.0, 101.0],
        "AAPL__high": [101.0, 102.0],
        "AAPL__low": [99.0, 100.0],
        "AAPL__close": [100.0, 101.0],
        "AAPL__volume": [1000.0, 1000.0],
    }, index=index)
    prices.attrs.update({
        "profile_health": {"status": "fresh", "age_seconds": 0.0},
        "quote_health": {"status": "fresh", "age_seconds": 0.0},
        "data_snapshot": {"quality": "incomplete", "snapshot_id": "snapshot-1"},
    })
    compiled = compile_strategy({"name": "attrs", "base_symbol": "AAPL"}, prices)
    bars = _execution_bars(compiled, ["AAPL"])

    assert bars["AAPL"].attrs["profile_health"]["status"] == "fresh"
    assert bars["AAPL"].attrs["quote_health"]["status"] == "fresh"
    assert bars["AAPL"].attrs["data_snapshot"]["quality"] == "incomplete"

    fill = execute_intents(
        [OrderIntent("AAPL", "buy", 10, index[0])],
        bars,
        ExecutionConfig(latency_bars=1),
    )[0]
    assert fill.status == "cancelled"
    assert fill.reason == "strategy_paused"


def test_engine_preserves_nan_gaps_and_execution_rejects_missing_bar():
    index = pd.date_range("2026-01-01", periods=3, freq="D")
    missing = float("nan")
    prices = pd.DataFrame({
        "AAPL__open": [100.0, missing, 102.0],
        "AAPL__high": [101.0, missing, 103.0],
        "AAPL__low": [99.0, missing, 101.0],
        "AAPL__close": [100.0, missing, 102.0],
        "AAPL__volume": [1000.0, missing, 1000.0],
    }, index=index)

    store = _normalize_price_store(prices)
    close_panel = _close_panel_from_store(store)
    assert pd.isna(store.loc[index[1], "AAPL__close"])
    assert pd.isna(close_panel.loc[index[1], "AAPL"])

    compiled = compile_strategy({"name": "gap", "base_symbol": "AAPL"}, prices)
    bars = _execution_bars(compiled, ["AAPL"])
    fill = execute_intents(
        [OrderIntent("AAPL", "buy", 10, index[1])],
        bars,
        ExecutionConfig(latency_bars=0),
    )[0]
    assert fill.status == "rejected"
    assert fill.reason == "invalid_bar_price"
