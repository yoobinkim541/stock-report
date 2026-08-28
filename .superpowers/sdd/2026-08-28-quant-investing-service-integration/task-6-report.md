# Task 6 Implementation Report: Data, Universe, and Model Provenance

## Status

Task 6 fix round 1 is implemented in `/home/ubuntu/projects/stock-report`.
The provenance layer remains additive: existing price collectors continue to
return the same `DataFrame` shapes, with normalized snapshots attached through
`.attrs`.

## Implemented

- Added `DataSnapshot` and `ModelProvenance` JSON-safe contracts, deterministic
  snapshot IDs, stable ordering, timezone-aware ISO timestamp normalization,
  and event `timestamp` versus `received_at`/`available_at` ordering checks.
- Preserved timezone-aware collector indexes and event offsets through the
  yfinance response path. UTC is used for ordering/comparison while the
  original timezone remains visible in the event timestamp.
- Added `normalize_data_snapshot()` for source, timeframe, session,
  adjustment, quality, raw reference, timestamp, and OHLCV metadata. Missing
  receipt/availability, invalid events, duplicate events, and invalid values
  remain visible as warnings; no event or normalizer wall-clock timestamp is
  reused as transport metadata. The collector passes its observed response
  receipt explicitly.
- Added `source_freshness()` and `source_coverage()` diagnostics with explicit
  evaluation timestamps, age limits, stale warnings, per-source symbol counts,
  missing symbols, required transport metadata checks, full source diagnostics,
  and JSON-compatible output. Coverage cannot be `ok` when freshness or
  required transport metadata is unknown/missing.
- Added `point_in_time_universe()` with timezone normalization, inclusive
  effective intervals, explicit open-ended expiry handling, deterministic
  symbol output, and a list-compatible result carrying status, warnings, and
  invalid-row diagnostics instead of silently dropping malformed rows.
- Wired the PIT helper into `build_ml_dataset()`. Membership interval parsing,
  validation, filtering, and fallback are reflected in explicit
  `survivorship_status`, `survivorship_warnings`, and diagnostics. Fallback or
  exception paths report `unknown` and never claim `survivorship_free=True`.
- Attached snapshots to yfinance price frames and propagated source coverage
  and serialized snapshots into ML dataset metadata without changing the
  legacy collector return contract.
- Added model provenance construction/exposure in the registry and prediction
  diagnostics. Incomplete or malformed registrations emit warnings and do not
  produce complete provenance. Training `as_of` values later than prediction
  timestamps are reported when the feature index provides prediction times.
- Model freshness now consumes singular and plural `data_snapshots`, evaluates
  every snapshot when `evaluation_at` exists, reports `unknown` when it does
  not, and retains visible per-snapshot diagnostics. Stale inputs retain
  predictions for audit but set confidence to zero; no newer value is
  substituted silently. Strategy compilation/provider feature frames preserve
  snapshot attrs across normalization.
- Kept incomplete legacy model registrations loadable for existing callers,
  while exposing missing provenance diagnostics and supporting strict
  `require_provenance` prediction calls.

## Verification

- `./.venv/bin/pytest tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_data_pipeline_resilience.py tests/test_strategy_registry.py tests/test_strategy_signals.py -q`
  - `66 passed`
- `./.venv/bin/pytest tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_data_pipeline_resilience.py tests/test_strategy_registry.py tests/test_strategy_signals.py tests/test_strategy_contracts.py tests/test_ml_models.py tests/test_strategy_validation.py -q`
  - `128 passed, 13 warnings`
- `./.venv/bin/python -m compileall -q ml/strategy_studio ml/data_pipeline.py ml/models.py`
  - passed
- `git diff --check`
  - passed

## Risks and Trade-offs

- The collector boundary records an observed response receipt time for
  yfinance. Direct normalization and replay callers leave receipt missing
  unless explicitly supplied. Availability remains `None` when the source
  does not publish it, with a visible warning rather than a fabricated
  timestamp.
- Legacy registry metadata remains accepted to preserve Task 2 behavior.
  Such registrations expose missing-provenance warnings and cannot satisfy a
  strict provenance-required prediction or Task 5 promotion check.
- The point-in-time helper remains list-compatible, but callers that need
  completeness must inspect its status/diagnostics. Invalid rows are excluded
  from membership and exposed, which can reduce coverage rather than treating
  malformed expiry as open-ended.
- Legacy scalar `data_as_of` remains accepted only when no structured snapshot
  is present. Structured snapshots never derive freshness from event time or
  quality labels.
- Task 5's parked P1 chronology-proof alias fail-open issue is unchanged and
  remains a required fix in the final whole-branch review. Task 6 does not
  weaken its activation safety.

## Changed Files

- `ml/data_pipeline.py`
- `ml/models.py`
- `ml/strategy_studio/contracts.py`
- `ml/strategy_studio/registry.py`
- `ml/strategy_studio/__init__.py`
- `ml/strategy_studio/engine.py`
- `ml/strategy_studio/signals.py`
- `tests/test_ml_data_sources.py`
- `tests/test_ml_universe.py`
- `tests/test_data_pipeline_resilience.py`
- `tests/test_strategy_registry.py`
- `tests/test_strategy_signals.py`
- `.superpowers/sdd/2026-08-28-quant-investing-service-integration/task-6-report.md`
