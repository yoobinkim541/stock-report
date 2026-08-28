# Task 8 Report: Strategy Studio UI Integration

## Status

Task 8 is implemented in `/home/ubuntu/projects/stock-report`.
Task 9 profile and collector internals were not implemented.

## Implemented

- Integrated the shared strategy studio renderer into Research and retained the
  existing AI-console strategy canvas and RSI/EMA allocation interactions.
- Added saved-strategy and preset selection plus controls for data profile,
  execution profile, strategy type, signal provider, portfolio optimizer,
  validation mode, benchmark, period, and cost scenario.
- Added separate preview, run, validation, draft save, sandbox save, and live
  activation interactions using the Task 7 public strategy run and activation
  contracts.
- Added explicit draft, sandbox, paper, live, and live-blocked state rendering.
  Live activation requires a stored strategy, explicit confirmation, complete
  strict validation evidence, and the server-side activation capability; the
  UI never treats an incomplete run as live-ready.
- Rendered common result evidence for performance, drawdown, turnover,
  exposure, cost drag, total cost, partial fills, benchmark availability,
  validation folds, gate failures, data quality, provenance, diagnostics,
  signal provider, trade markers, equity, and weights.
- Added loading/error/empty states around run and validation actions and kept
  AI patch diff rendering intact.
- Added five non-RSI presets: `momentum_rank`, `mean_reversion`,
  `breakout_with_trailing_stop`, `factor_ensemble`, and `kr_intraday_vwap`.
  Each includes profile, benchmark, cost, validation, and explicit universe
  warnings; the factor preset includes an equal-weight quality-model fallback.
- Carried the parked Task 5 P1 chronology-proof alias blocker into the UI gate:
  CPCV activation remains blocked unless actual fold IDs, timestamps, future-
  training flags, nested chronology evidence, and provenance checks all agree.

## Verification

- `./.venv/bin/pytest tests/test_strategy_studio_pages.py -q`
  - 13 passed
- `./.venv/bin/pytest tests/test_dashboard_pages.py -q -k 'strategy'`
  - 3 passed, 99 deselected
- `StrategySpec` validation for all five new presets
  - all returned no validation errors
- `git diff --check`
  - passed before final staging
- `./.venv/bin/python -m compileall -q dashboard ml/strategy_studio`
  - passed

## Risks and Trade-offs

- Live activation is intentionally conservative: missing or contradictory
  validation, fold chronology, provenance, or data-quality evidence leaves the
  action disabled even if an upstream result claims success.
- The server remains the authority for the opaque activation capability and
  repeats validation on activation; the browser only exposes a gated action.
- The default cost choice preserves custom legacy RSI/EMA cost assumptions;
  selecting low or high explicitly applies the corresponding scenario.
- The new preset universes are deterministic data-only examples, but their
  non-point-in-time warnings remain visible and prevent unqualified promotion.
- Tests use Streamlit AppTest and monkeypatched Task 7 calls, so they do not
  exercise live network providers or a real activation persistence round trip.

## Changed Files

- `dashboard/strategy_studio.py`
- `dashboard/pages/research.py`
- `ml/strategy_studio/presets.py`
- `tests/test_strategy_studio_pages.py`
- `.superpowers/sdd/2026-08-28-quant-investing-service-integration/task-8-report.md`
