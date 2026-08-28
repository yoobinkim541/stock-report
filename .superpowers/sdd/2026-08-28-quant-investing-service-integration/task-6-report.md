# Task 6 Implementation Report: Data, Universe, and Model Provenance

## Status

Task 6 fix round 3 is implemented in `/home/ubuntu/projects/stock-report`.
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
- Default-compatible predictions now always expose `provenance_status`,
  `provenance_warnings`, and an explicitly incomplete nested model payload when
  registration provenance is missing, malformed, or explicitly incomplete.
  Explicit invalid provenance states can no longer be masked by complete
  top-level metadata fields.
- Strict promotion now requires explicit per-fold provenance checks with data
- Strict validation and promotion now require typed, non-empty source/model
  identity, feature version, model version, feature names, training start/end,
  as-of, code commit, seed, freshness status, and explicit
  `provenance_status="complete"`. Training chronology must satisfy
  `train_start <= train_end <= as_of <= evaluation_at`; contradictory aliases,
  malformed values, blank status, and future training metadata are rejected.
  Generated complete model provenance now carries the explicit status field.
- Activation revalidates the complete per-fold model and data payload instead of
  inferring completeness from `model.status` or `freshness`. Legacy prediction
  calls remain compatible and diagnostic-rich when `require_provenance=False`,
  but that compatibility path cannot authorize activation.

## Verification

- `./.venv/bin/pytest tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_data_pipeline_resilience.py tests/test_strategy_registry.py tests/test_strategy_signals.py -q`
  - `69 passed`
- `./.venv/bin/pytest tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_data_pipeline_resilience.py tests/test_strategy_registry.py tests/test_strategy_signals.py tests/test_strategy_validation.py -q`
  - `109 passed, 2 warnings`
- `./.venv/bin/pytest tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_data_pipeline_resilience.py tests/test_strategy_registry.py tests/test_strategy_signals.py tests/test_strategy_contracts.py tests/test_ml_models.py tests/test_strategy_validation.py -q`
  - `136 passed, 13 warnings`
- `./.venv/bin/pytest tests/test_strategy_validation.py::test_strict_provenance_rejects_blank_model_provenance_status tests/test_strategy_validation.py::test_activation_rejects_falsely_complete_payload_missing_required_model_fields tests/test_strategy_registry.py::test_model_registry_exposes_complete_provenance_for_validation -q`
  - `3 passed`
- `./.venv/bin/pytest tests/test_strategy_validation.py::test_default_compatible_prediction_provenance_cannot_activate_task5 tests/test_strategy_validation.py::test_activation_rejects_aggregate_only_provenance_claim_without_fold_checks tests/test_strategy_registry.py::test_default_prediction_does_not_mask_explicit_incomplete_provenance_status -q`
  - `3 passed`
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
  strict provenance-required prediction or Task 5 promotion check. Strict
  promotion reports must now carry explicit per-fold provenance evidence with
  all required typed model fields; aggregate-only hand-authored reports are
  intentionally rejected.
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

## Fix Round 4

Addressed the remaining strict activation validation findings:

- Strict model provenance now requires a `metrics` mapping and validates every
  metric name/value as a non-empty string and a finite number or `None`.
  Normalized metrics are retained in the validation output instead of being
  dropped, and missing or malformed metrics invalidate activation evidence.
- Canonical `feature_names` now uses presence semantics. The legacy
  `feature_columns` fallback is used only when the `feature_names` key is
  absent; explicit `None` or another invalid value fails strict validation.
- Added focused activation regressions for missing/malformed metrics and
  explicit-null feature names, plus an assertion that valid metrics survive
  validation serialization.
- Task 5 behavior remains unchanged, including the parked chronology-proof
  alias blocker. Legacy prediction compatibility and the existing Task 6
  timestamp, PIT universe, freshness, coverage, and plural snapshot behavior
  remain covered by the regression suites.

### Fix Round 4 Verification

- `./.venv/bin/pytest tests/test_strategy_validation.py -q`
  - `43 passed, 2 warnings`
- `./.venv/bin/pytest tests/test_ml_data_sources.py tests/test_ml_universe.py tests/test_data_pipeline_resilience.py tests/test_strategy_registry.py tests/test_strategy_signals.py tests/test_strategy_validation.py -q`
  - `112 passed, 2 warnings`
- `./.venv/bin/pytest tests/test_strategy_contracts.py tests/test_ml_models.py -q`
  - `27 passed, 11 warnings`
- `./.venv/bin/python -m compileall -q ml/strategy_studio ml/data_pipeline.py ml/models.py`
  - passed
- `git diff --check`
  - passed

### Fix Round 4 Risks and Trade-offs

- Strict activation now requires model metric evidence, so hand-built or older
  activation payloads without `metrics` remain diagnostic-only until upgraded.
- Empty metric mappings are accepted because the contract permits a typed
  `dict[str, float | None]`; unavailable individual metrics should be
  represented as `None`.
