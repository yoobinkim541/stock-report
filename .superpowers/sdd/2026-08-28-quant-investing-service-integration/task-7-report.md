# Task 7 Fix Round 2 Report: Non-Forgeable Activation and Provider Contracts

## Status

Task 7 fix round 2 is implemented in `/home/ubuntu/projects/stock-report`.
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
- Live authorization is now a one-time opaque capability held in a
  server-side registry. The registry retains the exact spec hash, complete
  validation-result digest and expiry, verifies object identity plus nonce,
  rechecks the full activation gate, and consumes the capability before save.
  A caller-created token with matching-looking data cannot pass this check.
- The full validation adapter keeps the typed capability beside the JSON-safe
  result; the capability is never serialized into the public run/validate
  response. A real full-validation activation test covers the adapter-to-save
  handoff and persistence path.
- Provider `plugin`, `provider`, `type`, and indicator `kind` values now use
  the registered signal-provider or existing spec allowlists. Relative and
  absolute path-like values, traversal, executable tokens, and malformed
  provider shapes remain rejected before merge or execution.
- Strategy API routes now return JSON 4xx responses for malformed/non-object
  JSON and invalid or non-positive versions. Existing chat behavior and the
  default heuristic preview/patch-preview routes remain available.

## Verification

- Focused Task 7 API/security tests: `19 passed, 132 deselected`
- API/version and legacy route regressions: `6 passed`
- Shared strategy regression suite: `119 passed, 2 warnings`
- Strategy gate/storage regressions: `5 passed`
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
- The activation capability registry is process-local and one-time. A
  multi-worker deployment needs a shared registry or equivalent signed
  capability store, and a storage failure after capability consumption
  requires a fresh full validation run.
- Task 5's chronology-proof alias issue remains parked as the final whole-
  branch activation blocker.
- The broader `tests/test_agent_console.py -k 'strategy or agent'` run was
  terminated by the environment with exit 137 before completion; the focused
  and shared regression suites above completed successfully.

## Changed Files In This Round

- `agent_console/strategy_studio.py`
- `tests/test_agent_console.py`
- `.superpowers/sdd/2026-08-28-quant-investing-service-integration/task-7-report.md`
