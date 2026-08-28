# Task 7 Fix Round 1 Report: Strategy API and Controlled AI Patches

## Status

Task 7 fix round 1 is implemented in `/home/ubuntu/projects/stock-report`.
Task 8 UI and Task 9 profiles were not changed.

## Fixes

- `run_strategy_spec()` now keeps `single_pass` as preview-only and routes
  `walk_forward`, `purged_walk_forward`, and `cpcv` through the shared split
  generators, per-fold backtest engine, `evaluate_validation_folds()`, and
  `promotion_gate()`. Run and validate responses expose direct folds plus
  validation evidence, provenance, data quality, promotion, and diagnostics.
- Provider patches now require typed feature/indicator lists or a typed signal
  mapping. Nested provider fields are allowlisted, unknown fields and scalar
  types are rejected, executable/path-like keys and values are rejected, and
  the merged result is explicitly parsed by `StrategySpec.from_dict()` before
  preview or persistence.
- The patch route revalidates both the returned patch and merged spec even when
  an upstream patch adapter claims success. Persistence also rejects malformed
  patch metadata before calling storage.
- Activation is fail-closed on non-empty uniquely identified folds, exact fold
  counts, matching per-fold provenance checks, top-level data quality status,
  top-level provenance, strict Task 6 data/model provenance evidence, full
  validation mode, and a non-preview activation-safe promotion decision.
  Task 5's parked P1 chronology-proof alias blocker remains a final whole-
  branch blocker and was not weakened.
- Live saves require an internal token issued only by a successful full
  validation path. `source=live_activation` alone and direct adapter calls do
  not authorize live persistence. Draft and sandbox saves remain separate
  explicit states.
- Strategy API routes now return JSON 4xx responses for malformed/non-object
  JSON and invalid or non-positive versions. Existing chat behavior and the
  default heuristic preview/patch-preview routes remain available.

## Verification

- Focused Task 7 API/security tests: `18 passed, 132 deselected`
- API/version regressions: `2 passed`
- Shared strategy regression suite: `119 passed, 2 warnings`
- `python -m compileall -q agent_console ml/strategy_studio tests/test_agent_console.py`: passed
- `git diff --check`: passed

## Risks and Trade-offs

- Missing point-in-time data or model provenance remains activation-blocking,
  even when an upstream result claims success. This is conservative and keeps
  Task 6's strict provenance contract intact.
- Full validation evaluates each generated out-of-sample test fold through the
  existing declarative strategy engine; model-fitting adapters are still
  outside Task 7.
- The legacy heuristic patch adapter remains for compatibility with existing
  preview callers. Structured AI patching does not silently fall back to it
  when the LLM is unavailable.
- Task 5's chronology-proof alias issue remains parked as the final whole-
  branch activation blocker.

## Changed Files

- `agent_console/server.py`
- `agent_console/strategy_studio.py`
- `tests/test_agent_console.py`
- `.superpowers/sdd/2026-08-28-quant-investing-service-integration/task-7-report.md`
