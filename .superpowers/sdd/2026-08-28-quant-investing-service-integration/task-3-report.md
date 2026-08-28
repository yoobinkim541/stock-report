# Task 3 Implementation Report: Risk- and Cost-Aware Target Allocation

## Status

Implemented the Task 3 signal-to-position boundary in `/home/ubuntu/projects/stock-report`.
The allocation layer consumes the public `SignalPanel` contract and stops at
target weights. It does not create orders or alter execution simulator internals.

## Implemented

- Added `AllocationResult`, `allocate_targets()`, and `estimate_shrunk_covariance()`.
- Added Ledoit-Wolf covariance estimation with a finite, symmetric PSD fallback.
- Added deterministic cost-aware objective evaluation in `ml.optimization`:
  signal return minus covariance risk, turnover penalty, and bps transaction cost.
- Added invalid/low-confidence zeroing, per-position, gross exposure,
  target-volatility, and turnover constraints in the specified order.
- Added structured diagnostics for each truncation, plus per-row objective and
  transaction-cost diagnostics.
- Added deterministic `equal_weight` and `risk_budget` allocation modes.
- Added strict portfolio optimizer and numeric configuration validation without
  introducing arbitrary code execution.
- Routed only specs that explicitly contain `signal`, `portfolio`, or `execution`
  through `SignalPanel` and allocation. Legacy RSI/EMA specs, including those
  with only `data_profile`, continue to use the existing rule simulator.
- Exposed target weights and allocation diagnostics on `StrategyRun` and its
  signal trace for the next execution task.

## Verification

- `./.venv/bin/pytest tests/test_strategy_allocation.py -q`
  - `9 passed`
- `./.venv/bin/pytest tests/test_strategy_signals.py tests/test_strategy_registry.py tests/test_strategy_contracts.py tests/test_strategy_studio.py -q`
  - `40 passed`
- `./.venv/bin/pytest tests/test_ml_optimization.py -q`
  - `14 passed`
- `./.venv/bin/python -m compileall -q ml/strategy_studio ml/optimization.py`
  - passed
- `git diff --check`
  - passed

## Risks and Trade-offs

- The default objective uses a deterministic coordinate selection rather than a
  global convex solver, which keeps dependencies and behavior stable but can be
  conservative for large universes.
- Covariance is estimated from the supplied return panel and reused for target
  rows. Callers that require strict point-in-time risk estimates must pass a
  point-in-time return panel; the next validation task should make that
  provenance explicit.
- Allocation diagnostics and weights remain pandas objects in the existing
  research DTO shape. The later API/reporting work owns JSON serialization.

## Changed Files

- `ml/strategy_studio/allocation.py`
- `ml/strategy_studio/spec.py`
- `ml/strategy_studio/engine.py`
- `ml/optimization.py`
- `tests/test_strategy_allocation.py`
