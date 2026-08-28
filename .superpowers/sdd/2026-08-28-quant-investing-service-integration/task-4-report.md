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
- Aggregated participation capacity per symbol and eligible bar across all
  intents, with stable semantic ordering and deterministic fallback IDs.
- Recorded rejected and cancelled order events as `FillEvent` records with
  reasons and timestamps.
- Added `apply_fills()` position lifecycle updates with weighted average cost,
  realized P&L, fees, and invariant checks.
- Added `run_execution_backtest()` to turn target weights into order intents,
  reconcile residual orders against executed quantities, reserve only pending
  order quantities, avoid long-only oversells, mark positions to market, and
  return consistent equity, fills, trades, positions, and a cost-aware summary.
- Reworked target reconciliation into a single chronological pass so only
  current and past fill events affect decisions; partial residuals and pending
  order reservations are carried forward without consulting future bar state.
- Added causal cancel/replace handling for pending target orders: a reduced or
  reversed target releases its reservation, emits a distinct cancelled event,
  and schedules only the replacement quantity when still needed.
- Made `ExecutionResult.to_dict()`, new-strategy signal traces, and complete
  strategy reports strict JSON payloads using the public `serialize_event()`
  contract and stable `{index, columns, data}` DataFrame serialization.
- Added the profile-default compatibility hook for `bar`, `kr_intraday`,
  `global_swing`, and `extended_us`; Task 9 profile health and collector work
  remains out of scope.
- Routed only explicit signal/portfolio/execution blocks through the new path.
- Exposed execution summaries and execution trade fields in strategy reports.

## Verification

- `./.venv/bin/pytest tests/test_strategy_execution.py -q`
  - `21 passed`
- `./.venv/bin/pytest tests/test_strategy_execution.py tests/test_strategy_studio.py tests/test_trade_events.py -q`
  - `33 passed`
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
- `StrategyRun.equity` and `StrategyRun.weights` remain pandas DataFrames for
  existing in-memory consumers; `build_strategy_report()` converts them only
  at its serialization boundary, so report callers receive JSON-safe payloads.
- Close-only research panels synthesize OHLC values and use an uncapped volume
  sentinel. Real OHLCV inputs still enforce participation caps; missing volume
  is visible only through the absence of a liquidity constraint.
- A non-triggered order produces a rejected event by default and can be marked
  cancelled with `cancel_unfilled=True`. This keeps the ledger complete without
  inventing a later fill.
- Pending target orders are cancelled and replaced when a newer target reduces
  their intended exposure. This prevents stale long positions, but target
  reversal is intentionally conservative while an order is still in latency.
- `deploy/crontab.stock-report:21` is an unrelated `jipsuri-class` job from the
  pre-existing user commit `907b21a`; it was inspected and left untouched as
  out of scope for Task 4.
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
