# Task 4 Implementation Report: Common Execution Simulator and Ledger

## Status

Implemented the deterministic bar-based execution path for opted-in strategy
specifications. Legacy rule-only specifications continue to use the existing
weight simulator.

## Implemented

- Added `ExecutionConfig` validation and `ExecutionResult` ledger DTOs.
- Added deterministic `execute_intents()` support for market, limit, stop, and
  stop-limit intents.
- Applied latency bars, next-bar/open execution, stop gap handling, intrabar
  limit/stop eligibility, volume participation caps, partial fills, fees,
  slippage, and half-spread price impact.
- Recorded rejected and cancelled order events as `FillEvent` records with
  reasons and timestamps.
- Added `apply_fills()` position lifecycle updates with weighted average cost,
  realized P&L, fees, and invariant checks.
- Added `run_execution_backtest()` to turn target weights into order intents,
  mark positions to market, and return equity, fills, trades, positions, and a
  cost-aware summary.
- Added the profile-default compatibility hook for `bar`, `kr_intraday`,
  `global_swing`, and `extended_us`; Task 9 profile health and collector work
  remains out of scope.
- Routed only explicit signal/portfolio/execution blocks through the new path.
- Exposed execution summaries and execution trade fields in strategy reports.

## Verification

- `./.venv/bin/pytest tests/test_strategy_execution.py -q`
  - `9 passed`
- `./.venv/bin/pytest tests/test_strategy_execution.py tests/test_strategy_studio.py tests/test_trade_events.py -q`
  - `21 passed`
- `./.venv/bin/pytest tests/test_strategy_contracts.py tests/test_strategy_signals.py tests/test_strategy_allocation.py -q`
  - `47 passed`
- `./.venv/bin/python -m compileall -q ml/strategy_studio`
  - passed
- `git diff --check`
  - passed before staging; final staged check is part of the commit verification
- `./.venv/bin/pytest -q`
  - started but was stopped at 2% after several minutes because it was outside
    the requested focused verification window; it is not claimed as passing

## Risks and Trade-offs

- Target weights use a deterministic fixed initial cash base when converting to
  quantities. This makes replays stable, but a large overnight gap can create
  temporary negative cash because no financing or broker margin policy is
  modeled yet.
- Close-only research panels synthesize OHLC values and use an uncapped volume
  sentinel. Real OHLCV inputs still enforce participation caps; missing volume
  is visible only through the absence of a liquidity constraint.
- A non-triggered order produces a rejected event by default and can be marked
  cancelled with `cancel_unfilled=True`. This keeps the ledger complete without
  inventing a later fill.
- Short positions and exchange-specific price-limit rules are intentionally not
  added to this task because the existing `PositionState` contract is long-only;
  those policies require a later profile/operational integration.

## Changed Files

- `ml/strategy_studio/execution.py`
- `ml/strategy_studio/profiles.py`
- `ml/strategy_studio/contracts.py`
- `ml/strategy_studio/engine.py`
- `ml/strategy_studio/report.py`
- `ml/strategy_studio/__init__.py`
- `tests/test_strategy_execution.py`
