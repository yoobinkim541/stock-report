# Task 7 Implementation Report: Strategy API and Controlled AI Patches

## Status

Implemented Task 7 in `/home/ubuntu/projects/stock-report`.

## Implemented

- Added `run_strategy_spec()` as a JSON-safe adapter for stored or inline specs.
  Responses include the resolved profile, period, validation mode, report,
  metrics, provenance, data quality, promotion decision, warnings, errors, and
  structured diagnostics.
- Added `POST /api/strategy-studio/specs/<spec_id>/run` and
  `POST /api/strategy-studio/specs/<spec_id>/validate` while preserving the
  existing preview and chat routes.
- Added `propose_strategy_patch_with_llm()` and a structured `/patch` route.
  LLM output is parsed as JSON only, validated against `StrategySpec`, and
  restricted to `parameters`, `rules`, and `providers`. Rule fields, provider
  targets, parameter blocks, and executable-content markers are allowlisted.
  LLM unavailability returns `llm_unavailable`; it never becomes a heuristic
  result presented as an LLM patch.
- Added explicit sandbox save handling for validation and patch responses.
  Draft saves cannot persist `promotion.environment=live`; activation versions
  are created through the explicit activation adapter.
- Added `POST /api/strategy-studio/specs/<spec_id>/activate`. Live activation
  requires `confirm_live=true`, an affirmative validation result, an accepted
  activation-safe promotion decision, complete per-fold provenance evidence,
  and an activation-safe validation mode. No version is saved when any gate
  fails.
- Added an API-side CPCV chronology-proof check that rejects contradictory or
  incomplete aliases even if an upstream promotion payload claims acceptance.
  The parked Task 5 P1 chronology-proof alias blocker remains a final
  whole-branch blocker and was not weakened or silently repaired here.
- Added `agent.request_structured_output()` as a separate entry point over the
  existing LLM provider chain without changing legacy chat routing or fallback
  behavior.

## Verification

- `./.venv/bin/pytest tests/test_agent_console.py -k 'strategy_validate or strategy_run_adapter or strategy_run_route or llm_patch or live_activation or draft_save or contradictory_cpcv or structured_patch_route' -q`
  - `12 passed`
- `./.venv/bin/pytest tests/test_agent_console.py::test_strategy_studio_api_routes tests/test_agent_console.py::test_server_endpoints tests/test_agent_console.py::test_strategy_studio_version_store_round_trips -q`
  - `3 passed`
- `./.venv/bin/pytest tests/test_strategy_studio.py tests/test_strategy_validation.py tests/test_strategy_contracts.py tests/test_strategy_signals.py tests/test_strategy_allocation.py tests/test_strategy_execution.py -q`
  - `119 passed, 2 warnings`
- `./.venv/bin/python -m compileall -q agent_console ml/strategy_studio tests/test_agent_console.py`
  - passed
- `git diff --check`
  - passed
- A broader `tests/test_agent_console.py -k 'strategy or agent'` run was killed
  by the constrained test environment with exit code 137 and is not counted as
  a passing result.

## Risks and Trade-offs

- The existing engine still treats a single engine pass as preview evidence;
  non-single-pass runs remain activation-blocked until explicit out-of-sample
  folds are supplied.
- Missing point-in-time provenance remains visible as `unknown` data quality
  and blocks activation. This is conservative but prevents API-level claims of
  safe promotion without source evidence.
- The legacy heuristic `propose_strategy_patch()` remains available for the
  existing dashboard compatibility route. Structured AI patches use the new
  allowlisted path and do not fall back to that heuristic when the LLM is down.
- Task 5's parked chronology-proof alias blocker is still unresolved in the
  shared validation branch. Task 7 adds a conservative API defense and keeps
  activation blocked when chronology evidence is not independently coherent;
  final whole-branch activation safety still depends on closing the Task 5 P1.

## Changed Files

- `agent_console/agent.py`
- `agent_console/server.py`
- `agent_console/strategy_studio.py`
- `tests/test_agent_console.py`
- `.superpowers/sdd/2026-08-28-quant-investing-service-integration/task-7-report.md`
