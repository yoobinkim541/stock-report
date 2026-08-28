# Task 6 Implementation Report: Data, Universe, and Model Provenance

## Status

Implemented Task 6 in `/home/ubuntu/projects/stock-report`. The provenance
layer is additive: existing price collectors continue to return the same
`DataFrame` shapes, with normalized snapshots attached through `.attrs`.

## Implemented

- Added `DataSnapshot` and `ModelProvenance` JSON-safe contracts, deterministic
  snapshot IDs, stable ordering, ISO timestamp normalization, and event
  `timestamp` versus `received_at`/`available_at` ordering checks.
- Added `normalize_data_snapshot()` for source, timeframe, session,
  adjustment, quality, raw reference, timestamp, and OHLCV metadata. Missing
  availability, invalid events, duplicate events, and invalid values remain
  visible as warnings; no event timestamp is reused as transport metadata.
- Added `source_freshness()` and `source_coverage()` diagnostics with explicit
  evaluation timestamps, age limits, stale warnings, per-source symbol counts,
  missing symbols, and JSON-compatible output.
- Added `point_in_time_universe()` with timezone normalization, inclusive
  effective intervals, open-ended membership only for explicit missing expiry,
  deterministic symbol output, and rejection-by-omission for invalid interval
  endpoints.
- Attached snapshots to yfinance price frames and propagated source coverage
  and serialized snapshots into ML dataset metadata without changing the
  legacy collector return contract.
- Added model provenance construction/exposure in the registry and prediction
  diagnostics. Stale inputs retain predictions for audit but set confidence to
  zero and emit `data_stale`; no newer value is substituted silently.
- Kept incomplete legacy model registrations loadable for existing callers,
  while exposing missing provenance diagnostics and supporting strict
  `require_provenance` prediction calls.

## Verification

- `./.venv/bin/pytest tests/test_strategy_registry.py tests/test_ml_data_sources.py tests/test_ml_universe.py -q`
  - `32 passed`
- `./.venv/bin/pytest tests/test_strategy_registry.py tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_data_pipeline_resilience.py -q`
  - `36 passed`
- `./.venv/bin/pytest tests/test_strategy_registry.py tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_strategy_contracts.py tests/test_strategy_signals.py tests/test_ml_models.py tests/test_strategy_validation.py -q`
  - `113 passed, 13 warnings`
- `./.venv/bin/python -m compileall -q ml/strategy_studio ml/data_pipeline.py ml/models.py`
  - passed
- `git diff --check`
  - passed

## Risks and Trade-offs

- The collector boundary records an observed local receipt time when a
  collector did not supply one; replay callers can pass a fixed receipt time.
  Availability remains `None` when the source does not publish it, with a
  visible warning rather than a fabricated timestamp.
- Legacy registry metadata remains accepted to preserve Task 2 behavior.
  Such registrations expose missing-provenance warnings and cannot satisfy a
  strict provenance-required prediction or Task 5 promotion check.
- The point-in-time helper intentionally skips rows with invalid effective
  dates. This can reduce coverage, but prevents an invalid expiry from being
  interpreted as an open-ended membership interval.
- Task 5's parked P1 chronology-proof alias fail-open issue is unchanged and
  remains a required fix in the final whole-branch review. Task 6 does not
  weaken its activation safety.

## Changed Files

- `ml/data_pipeline.py`
- `ml/models.py`
- `ml/strategy_studio/contracts.py`
- `ml/strategy_studio/registry.py`
- `ml/strategy_studio/__init__.py`
- `tests/test_ml_data_sources.py`
- `tests/test_ml_universe.py`
- `tests/test_strategy_registry.py`
- `.superpowers/sdd/2026-08-28-quant-investing-service-integration/task-6-report.md`
