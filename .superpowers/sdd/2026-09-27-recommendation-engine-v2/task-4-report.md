# Task 4 Report: RecommendationSnapshot and Score Calibration

## Scope

Implemented the Task 4 standalone recommendation engine without raw-cache access.
The commit is restricted to `ml/recommendation_engine.py` and
`tests/test_recommendation_engine.py`.

## Delivered Behavior

- `build_snapshot` normalizes surface candidates into the common snapshot
  contract, canonicalizes horizons, preserves source/evidence metadata, marks
  unknown required data in `missing_fields` and `data_quality`, and exposes
  stale data without creating neutral evidence.
- `shrink_probability` implements the Beta(2,2) posterior. Snapshot
  uncertainty bounds are deterministic posterior-normal bounds.
- `fit_score_calibrator` excludes non-finite held-out and training values,
  requires 100 valid rows, creates up to five monotonic score bins with the
  requested minimum bin size, and records finite held-out Brier/ECE results
  or an explicit unavailable metrics result.
- `rank_snapshots` preserves legacy order in shadow mode. Reordering occurs
  only when `active=True` and `gate.passed is True`; risk and concentration
  penalties are deterministic and bounded. News contribution remains zero in
  every shadow result and is surfaced only for active, passed-gate ranking.

## TDD Evidence

1. Added `tests/test_recommendation_engine.py` before implementation.
2. The requested `.venv/bin/pytest -q tests/test_recommendation_engine.py`
   could not start because this worktree has no `.venv/bin/pytest`.
3. `python3 -m pytest -q tests/test_recommendation_engine.py` initially failed
   with the expected missing `ml.recommendation_engine` import error.
4. During author self-review, a new chronological split regression test failed:
   80 training rows were being merged into one bin. The implementation was
   adjusted to construct four valid 20-row bins, then the focused suite passed.

## Verification

- `python3 -m pytest -q tests/test_recommendation_engine.py`: **8 passed**
- `python3 -m compileall -q ml/recommendation_engine.py`: **passed**
- `python3 -m pytest -q tests/test_recommendation_engine.py tests/test_recommendation_contracts.py tests/test_recommendation_features.py`: **22 passed**

The complete `python3 -m pytest -q` suite could not collect ten unrelated
modules because the worktree environment lacks `plotly`, `streamlit`, and
`matplotlib`, and runs Python 3.10 without `tomllib`. These are environment
dependency failures, not Task 4 test failures.

## Review

Performed an author self-review because the task explicitly prohibits
dispatching a separate reviewer. No unaddressed Task 4 implementation issues
were found after the chronological-bin correction.

## Commit

`520cdb1 add) 추천 스냅샷과 점수 보정 엔진 추가`

## Review Fix Completion (2026-09-30)

Resumed from base `520cdb1`, preserving the prior implementer's uncommitted
engine and test changes. Changes are limited to the two Task 4 code/test files
and this report; no subagents were used.

- P1 inherited missingness: merge candidate and source `missing_fields` with
  locally detected omissions, preserving order and removing duplicates before
  deriving `data_quality`. Regression coverage also checks evidence references,
  inherited quality flags, and unchanged caller inputs.
- P2 comprehensive missingness: cover dates, calibration, expected excess,
  signal/rank, sample metadata, uncertainty, freshness, coverage, risk, versions,
  and unavailable derived `total_rank_score`. Explicit zero values remain data,
  including zero coverage; defaults such as shadow status remain unchanged.
- P1 ECE: group held-out outcomes by calibrated probability, never raw-score
  boundaries. Tests exercise raw scores outside probability space, pooled equal
  probabilities, unequal group sizes, and unchanged Brier calculations.

### Exact Verification

All commands ran in `/tmp/stock-report-recommendation-v2` using the requested
pytest executable (not `python3`).

1. Initial preserved changes:
   `/home/ubuntu/projects/stock-report/.venv/bin/pytest -q tests/test_recommendation_engine.py`
   -> **10 passed in 0.66s**.
2. Added regression tests before the remaining implementation change:
   `/home/ubuntu/projects/stock-report/.venv/bin/pytest -q tests/test_recommendation_engine.py`
   -> **1 failed, 12 passed in 0.64s**; the expected failure was the absent
   `total_rank_score` missingness marker on an empty snapshot.
3. Final engine verification:
   `/home/ubuntu/projects/stock-report/.venv/bin/pytest -q tests/test_recommendation_engine.py`
   -> **13 passed in 0.57s**.
4. Focused compatibility verification:
   `/home/ubuntu/projects/stock-report/.venv/bin/pytest -q tests/test_recommendation_engine.py tests/test_recommendation_contracts.py tests/test_recommendation_features.py`
   -> **27 passed in 3.15s**.
5. `git diff --check` -> **passed**.

### Outcome and Trade-Off

All three existing review findings are addressed. More complete missingness can
produce longer quality annotations; inherited omissions are deliberately retained
even when local values exist. ECE combines raw bins sharing the same calibrated
probability, so it measures probability calibration rather than raw-bin-specific
error. No ranking, gating, integration, or dependency changes were introduced.
The full repository suite was not run; verification stayed focused on Task 4 and
its contracts/features. This report is included in the review-fix commit.
