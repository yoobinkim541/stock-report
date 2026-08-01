# Daily Mock Trading Momentum Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared momentum-following overlay to the daily KR and US mock trading engines so strong trends can tilt ranking and sizing without replacing the current base policy.

**Architecture:** Introduce one pure momentum-overlay module that scores daily price history against a benchmark and regime, then returns a bounded overlay state (`base_score`, `momentum_score`, `selection_score`, `momentum_multiplier`, `overlay_active`). Wire that state into the KR and US daily cron paths so the rebalance planner can rank names and scale target weights with the same logic in both markets. Keep the paper dashboard read-only but expose the overlay fields in the ledger so users can see when the engine is riding trend versus falling back to the base policy.

**Tech Stack:** Python 3.11, pandas, Streamlit, pytest, existing `crons/`, `ml/`, `dashboard/` modules.

## Global Constraints

- Daily KR and US mock trading only; intraday systems must remain unchanged.
- Base policy remains canonical; momentum overlay fails closed on missing, stale, or low-confidence data.
- Feature flags default off: `KR_MOCK_MOMENTUM_OVERLAY_ENABLED`, `US_MOCK_MOMENTUM_OVERLAY_ENABLED`.
- No new database, no separate account, no manual approval flow.
- Paper UI must show overlay contribution explicitly and keep existing KPI/order-history behavior intact.

---

### Task 1: Build the shared momentum overlay helper and prove the scoring rules

**Files:**
- Create: `ml/mock_momentum_overlay.py`
- Create: `tests/test_mock_momentum_overlay.py`

**Interfaces:**
- Consumes: daily close series for candidate and benchmark, optional volume series, market label, regime label, freshness flag
- Produces: `build_momentum_features(...)`, `score_momentum_overlay(...)` returning `base_score`, `momentum_score`, `selection_score`, `momentum_multiplier`, `overlay_active`, `momentum_state`, `reason_codes`

- [ ] **Step 1: Write the failing tests first**

```python
def test_strong_trend_in_risk_on_raises_selection_score():
    close = _trend_series([100, 102, 104, 106, 108, 110, 112, 114])
    bench = _trend_series([100, 100, 100, 100, 100, 100, 100, 100])
    feats = build_momentum_features(close, bench)
    out = score_momentum_overlay(0.58, feats, market="kr", regime="risk_on", freshness_ok=True)
    assert out["overlay_active"] is True
    assert out["momentum_state"] == "strong"
    assert out["selection_score"] > 0.58
    assert out["momentum_multiplier"] > 1.0


def test_mean_reverting_series_falls_back_to_base_score():
    close = _chop_series([100, 101, 99, 100, 98, 101, 99, 100])
    bench = _trend_series([100, 100, 100, 100, 100, 100, 100, 100])
    feats = build_momentum_features(close, bench)
    out = score_momentum_overlay(0.62, feats, market="us", regime="risk_off", freshness_ok=False)
    assert out["overlay_active"] is False
    assert out["selection_score"] == 0.62
    assert out["momentum_state"] == "inactive"
    assert out["reason_codes"]
```

- [ ] **Step 2: Make the helper pure and bounded**

Implement the helper so it derives a compact feature set and clamps the overlay:

```python
def build_momentum_features(close, benchmark_close, volume=None, *, asof=None) -> dict:
    ...

def score_momentum_overlay(base_score: float, features: dict, *, market: str,
                           regime: str | None, freshness_ok: bool) -> dict:
    ...
```

Keep the feature list compact and explainable:

- `mom12`
- `mom63`
- `close_vs_sma50`
- `close_vs_sma200`
- `relative_strength_60d`
- `accel20`
- `volume_confirmation`

- [ ] **Step 3: Verify the helper in isolation**

Run: `./.venv/bin/python -m pytest tests/test_mock_momentum_overlay.py -q`

Expected: strong-trend, weak-trend, and fallback cases all pass; the overlay stays bounded and deterministic.

---

### Task 2: Wire the overlay into the daily KR and US mock-trading engines

**Files:**
- Modify: `crons/kiwoom_mock_track.py`
- Modify: `crons/us_mock_track.py`
- Modify: `tests/test_kiwoom_mock.py`
- Modify: `tests/test_us_mock_track.py`

**Interfaces:**
- Consumes: `ml.mock_momentum_overlay.score_momentum_overlay(...)`
- Produces: enriched signal rows with `base_score`, `momentum_score`, `selection_score`, `momentum_multiplier`, `momentum_state`, `overlay_active`
- Produces: rebalance plans that sort by `selection_score` when overlay is enabled and scale target values by `momentum_multiplier`

- [ ] **Step 1: Write the failing integration tests**

```python
def test_kr_signals_attach_overlay_fields(monkeypatch):
    import kiwoom_mock_track as kt
    monkeypatch.setenv("KR_MOCK_MOMENTUM_OVERLAY_ENABLED", "1")
    monkeypatch.setattr(kt, "score_momentum_overlay", lambda base, feats, **kw: {
        "base_score": base, "momentum_score": 0.91, "selection_score": 0.96,
        "momentum_multiplier": 1.15, "overlay_active": True,
        "momentum_state": "strong", "reason_codes": ["trend_up"]
    })
    sigs = kt.compute_kr_signals(limit=3)
    assert all("selection_score" in s for s in sigs)
    assert any(s["overlay_active"] for s in sigs)


def test_us_plan_rebalance_uses_momentum_multiplier_for_partial_trim():
    import us_mock_track as ut
    sigs = [
        {"ticker": "A", "price": 100, "policy_score": 0.90, "selection_score": 0.96,
         "momentum_multiplier": 1.20, "overlay_active": True},
        {"ticker": "B", "price": 100, "policy_score": 0.80, "selection_score": 0.78,
         "momentum_multiplier": 0.50, "overlay_active": True},
    ]
    plan = ut.plan_rebalance(sigs, {"B": {"shares": 20}}, budget_usd=2000, max_positions=2,
                             target_multipliers={"A": 1.20, "B": 0.50})
    assert any(o["symbol"] == "B" and o["side"] == "sell" for o in plan)
    assert any(o["symbol"] == "A" and o["side"] == "buy" for o in plan)
```

- [ ] **Step 2: Make ranking and sizing overlay-aware**

In both cron paths:

```python
ranked = sorted(
    signals,
    key=lambda s: -(
        s.get("selection_score") if s.get("overlay_active")
        else s.get("policy_score") or 0
    ),
)
target_multipliers = {s["ticker"]: s.get("momentum_multiplier", 1.0) for s in signals}
plan = plan_rebalance(..., target_multipliers=target_multipliers)
```

Update the rebalance math so a lower `momentum_multiplier` naturally turns into a smaller target and can produce a partial trim or full exit when the overlay is weak or broken.

- [ ] **Step 3: Keep the feature flag behavior conservative**

When `KR_MOCK_MOMENTUM_OVERLAY_ENABLED` or `US_MOCK_MOMENTUM_OVERLAY_ENABLED` is off, the cron paths must keep today’s exact base-policy behavior and only populate overlay fields as inactive metadata.

- [ ] **Step 4: Verify the cron paths in isolation**

Run:

- `./.venv/bin/python -m pytest tests/test_kiwoom_mock.py -q`
- `./.venv/bin/python -m pytest tests/test_us_mock_track.py -q`

Expected: the new overlay assertions pass, the old rebalance behavior stays intact when the flag is off, and the partial-trim / full-exit path is covered.

---

### Task 3: Expose the overlay in the paper dashboard and decision ledger

**Files:**
- Modify: `dashboard/views.py`
- Modify: `dashboard/pages/paper.py`
- Modify: `tests/test_dashboard_pages.py`

**Interfaces:**
- Consumes: decision rows from `ml.adaptive.Ledger`, enriched with the new overlay fields
- Produces: paper-page rows and captions that show `base_score`, `momentum_score`, `selection_score`, `momentum_tilt`, `momentum_multiplier`, `momentum_state`, `regime`, `overlay_active`

- [ ] **Step 1: Write the failing dashboard regression**

```python
def test_paper_decisions_show_momentum_overlay_columns():
    at = AppTest.from_string(_script("from dashboard.pages import paper", "paper.render()"),
                             default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    df = next(item.value for item in at.dataframe if "판단 근거" in item.value.columns)
    for col in ("base_score", "momentum_score", "selection_score",
                "momentum_tilt", "momentum_multiplier", "momentum_state", "overlay_active"):
        assert col in df.columns
```

- [ ] **Step 2: Propagate the overlay metadata into the ledger join**

Extend `join_decisions(...)` so it passes through the overlay fields from each decision row without renaming or dropping them.

```python
row["base_score"] = d.get("base_score")
row["momentum_score"] = d.get("momentum_score")
row["selection_score"] = d.get("selection_score")
row["momentum_tilt"] = d.get("momentum_tilt")
row["momentum_multiplier"] = d.get("momentum_multiplier")
row["momentum_state"] = d.get("momentum_state")
row["regime"] = d.get("regime")
row["overlay_active"] = d.get("overlay_active")
```

- [ ] **Step 3: Render the overlay columns in the paper table**

Update `_decisions_section(...)` so the table makes the overlay visible immediately. Keep the existing `축 피처 보기` toggle for the current momentum axes, but do not hide the new overlay state behind a second expander.

```python
base = {
    "날짜": r["date"],
    "구분": f"{_SIDE_ICON.get(r.get('side'), '')} {r.get('side', '')}",
    "종목": r.get("name") or r.get("ticker"),
    "정책점수": data.f_ratio(r.get("policy_score"), 3),
    "selection_score": data.f_ratio(r.get("selection_score"), 3),
    "momentum_state": r.get("momentum_state") or "inactive",
}
```

- [ ] **Step 4: Keep empty states honest**

If overlay fields are missing because the feature flag is off or the data is stale, render `inactive` / `—` rather than hiding the column or collapsing the row.

- [ ] **Step 5: Verify the dashboard surface**

Run: `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -q`

Expected: the paper page still boots, the decision table shows the overlay columns, and the existing KPIs / order history continue to render.

---

### Task 4: Run the full verification pass and commit the feature

**Files:**
- Modify: `docs/superpowers/plans/2026-08-01-mock-trading-momentum-overlay.md` only if the implementation diverges from the plan

**Interfaces:**
- Consumes: the new overlay helper, the KR/US cron integrations, and the paper-page ledger fields
- Produces: a clean branch with focused verification evidence and a single implementation commit

- [ ] **Step 1: Run the focused helper, cron, and dashboard tests**

Run:

- `./.venv/bin/python -m pytest tests/test_mock_momentum_overlay.py -q`
- `./.venv/bin/python -m pytest tests/test_kiwoom_mock.py -q`
- `./.venv/bin/python -m pytest tests/test_us_mock_track.py -q`
- `./.venv/bin/python -m pytest tests/test_dashboard_pages.py -q`

Expected: all focused suites pass with the overlay enabled in the test harness and with the feature flag disabled for the base-policy regression checks.

- [ ] **Step 2: Run a broader smoke pass**

Run: `./.venv/bin/python -m pytest -q`

Expected: no regressions outside the mock-trading surfaces, or if the suite is still blocked elsewhere, capture the first unrelated failure and keep the overlay changes scoped and green.

- [ ] **Step 3: Commit the implementation**

```bash
git add ml/mock_momentum_overlay.py \
        crons/kiwoom_mock_track.py \
        crons/us_mock_track.py \
        dashboard/views.py \
        dashboard/pages/paper.py \
        tests/test_mock_momentum_overlay.py \
        tests/test_kiwoom_mock.py \
        tests/test_us_mock_track.py \
        tests/test_dashboard_pages.py \
        docs/superpowers/plans/2026-08-01-mock-trading-momentum-overlay.md
git commit -m "add) 모의투자 모멘텀 추종 오버레이 구현"
```
