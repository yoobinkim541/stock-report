# Task 5 Implementation Report: Time-Series Validation and Promotion Gates

## Status

Implemented Task 5 in `/home/ubuntu/projects/stock-report`. The validation
layer is additive and keeps the existing rule-only backtest path unchanged.

## Implemented

- Added `ValidationSplit` with deterministic purged walk-forward and CPCV
  generators, chronological index validation, label-end overlap purging,
  embargo blackout metadata, and split leakage diagnostics.
- Added legacy integer-position helpers in `ml.walk_forward` without changing
  the existing `walk_forward_splits` or `run_walk_forward` contracts.
- Added fold and aggregate metrics for gross/net CAGR, volatility, Sharpe,
  Sortino, max drawdown, Calmar, turnover, cost drag, hit rate, profit factor,
  trade count, benchmark excess, fold dispersion, and regime stability.
- Reused the existing DSR/PBO implementations only when observations and
  tested-configuration data are sufficient. Otherwise the report emits
  `None` plus an explicit warning rather than implying significance.
- Added data/model source, version, timestamp, freshness, future-timestamp,
  and provenance checks with deterministic evaluation timestamps.
- Added `PromotionDecision` with separate `shadow`, `pilot`, and `live`
  semantics. `single_pass` is preview-only; live activation requires the
  explicit `explicit_live_activation` flag.
- Added validation and promotion blocks to `StrategyRun` and serialized
  strategy reports. A single engine pass never becomes activation-safe.
- Expanded supported validation modes to `purged_walk_forward` and `cpcv`, and
  supported shadow/pilot environment names while retaining sandbox/paper
  aliases.
- Added focused edge-case tests for empty/small samples, overlapping labels,
  embargo, duplicate/non-monotonic indexes, leakage, stale/future provenance,
  costs, deterministic splits, gate rejection, and legacy preview behavior.

## Verification

- `./.venv/bin/pytest tests/test_strategy_validation.py tests/test_strategy_studio.py -q`
  - `16 passed`
- `./.venv/bin/pytest tests/test_strategy_contracts.py tests/test_strategy_signals.py tests/test_strategy_allocation.py tests/test_strategy_execution.py -q`
  - `68 passed`
- `./.venv/bin/python -m compileall -q ml/strategy_studio ml/walk_forward.py`
  - passed
- `git diff --check`
  - passed

## Risks and Trade-offs

- Engine integration evaluates the current run as a preview fold; it does not
  recursively execute a full multi-fold backtest. Robust validation modes are
  therefore never marked activation-safe by a single engine pass.
- Missing point-in-time provenance remains visible as a warning in reports and
  blocks activation eligibility, which is conservative for local research but
  prevents unqualified promotion.
- DSR/PBO require explicit configuration-return evidence and adequate samples;
  this avoids fabricated confidence at the cost of more `None` values for
  ordinary legacy runs.
- Turnover and cost drag follow the existing run/equity contracts, while all
  validation performance statistics use net returns where applicable.

## Changed Files

- `ml/strategy_studio/validation.py`
- `ml/strategy_studio/engine.py`
- `ml/strategy_studio/report.py`
- `ml/strategy_studio/spec.py`
- `ml/strategy_studio/__init__.py`
- `ml/walk_forward.py`
- `tests/test_strategy_validation.py`
- `.superpowers/sdd/2026-08-28-quant-investing-service-integration/task-5-report.md`

## Fix Round 1

Addressed reviewer findings without changing the legacy rule-only routing:

- Purged walk-forward and CPCV splits now require explicit label-end or horizon
  metadata, retain post-test blackout rows, and extend that blackout through the
  declared horizon plus embargo. CPCV paths expose future-training metadata and
  only strictly chronological paths can be considered for activation.
- Promotion is fail-closed for missing or incomplete provenance, missing age
  limits, missing evaluation timestamps, numeric-only DSR/PBO values, and
  non-chronological CPCV evidence. Preview diagnostics remain available.
- Volatility, Sharpe, and Sortino annualization distinguish intraday, daily,
  weekly, and monthly cadence and accept explicit `periods_per_year` settings.
  Benchmark evaluation accepts run-shaped and metric-shaped inputs and computes
  supplied net benchmark excess consistently.
- Missing trade PnLs no longer fall back to per-bar returns. Duplicate and
  overlapping fold timestamps emit warnings, while all report boundaries remain
  JSON-safe.

### Fix Round 1 Verification

- `./.venv/bin/pytest tests/test_strategy_validation.py tests/test_strategy_studio.py tests/test_strategy_contracts.py tests/test_strategy_signals.py tests/test_strategy_allocation.py tests/test_strategy_execution.py -q`
  - `30 passed` for validation/studio and `68 passed` for Task 3/4 contract suites
- `./.venv/bin/python -m compileall -q ml/strategy_studio ml/walk_forward.py`
  - passed
- `git diff --check`
  - passed

### Fix Round 1 Trade-offs

- A split request without label metadata now returns no splits with a warning,
  which is safer than assuming timestamp labels but requires callers to declare
  a zero or positive horizon explicitly.
- Default CPCV remains useful for diagnostic/PBO analysis, but it is blocked by
  the activation gate unless strict chronology is configured and evidenced.
- Freshness limits and provenance completeness are stricter for activation, so
  legacy runs continue to produce preview reports but cannot be promoted until
  their source/model metadata is supplied.
