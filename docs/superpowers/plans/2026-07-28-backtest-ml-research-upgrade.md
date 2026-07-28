# Backtest ML Research Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-ready slice of the quant reference upgrade: event-driven backtest primitives, lifecycle-aware intraday labels, and a common validation gate.

**Architecture:** Keep existing strategy scripts intact and add small shared modules that they can adopt incrementally. The new modules are pure, no-network, and tested with synthetic data so they can become stable interfaces for later KR/US strategy migrations.

**Tech Stack:** Python 3.11, pandas/numpy, existing `ml.adaptive.costs`, existing `ml.validation`, pytest.

## Global Constraints

- Do not replace existing `backtest/*` strategy scripts in this slice.
- All new behavior must be pure and testable without network calls.
- Backtest orders must use next-bar execution semantics by default to avoid lookahead.
- Lifecycle labels must mirror the intraday partial stop/partial target policy.
- Validation gates must degrade to `OBSERVE` or `NO-GO`, never silently approve insufficient evidence.

---

### Task 1: Event-Driven Backtest Primitives

**Files:**
- Create: `ml/event_backtest.py`
- Test: `tests/test_event_backtest.py`

**Interfaces:**
- Produces: `Order`, `Fill`, `PortfolioState`, `BacktestSummary`
- Produces: `simulate_orders(prices: pandas.DataFrame, orders: list[Order], *, initial_cash: float, market: str = "US", fill_mode: str = "next_open") -> BacktestSummary`

- [ ] **Step 1: Write failing tests**

```python
def test_simulate_orders_fills_on_next_open_and_applies_costs():
    prices = pd.DataFrame({
        "Open": [100.0, 102.0, 105.0],
        "Close": [101.0, 104.0, 106.0],
    }, index=pd.date_range("2026-01-01", periods=3, freq="D"))
    summary = simulate_orders(prices, [Order(ts=prices.index[0], symbol="A", side="buy", qty=10)], initial_cash=2000.0)
    assert summary.fills[0].price == 102.0
    assert summary.final_nav > 2000.0
    assert summary.cost_paid > 0
```

- [ ] **Step 2: Run test and verify missing module/function failure**

Run: `.venv/bin/python -m pytest tests/test_event_backtest.py -q`

- [ ] **Step 3: Implement minimal event backtester**

Use dataclasses. For `next_open`, find the first row strictly after `order.ts` and fill at `Open`. Apply `ml.adaptive.costs.order_cost(abs(notional), side, market)`. Maintain cash, integer shares, NAV curve, total turnover, fills.

- [ ] **Step 4: Add conservative same-bar rejection test**

Orders at the final bar should be rejected with no fill because there is no next bar.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_event_backtest.py -q`

---

### Task 2: Lifecycle-Aware Intraday ML Labels

**Files:**
- Modify: `ml/intraday_experiment.py`
- Test: `tests/test_intraday_experiment.py`

**Interfaces:**
- Produces: `LifecycleOutcomeLabel`
- Produces: `label_lifecycle_decision(decision: DecisionSnapshot, prices: list[dict], *, cfg: dict | None = None) -> LifecycleOutcomeLabel`

- [ ] **Step 1: Write failing lifecycle label tests**

```python
def test_label_lifecycle_decision_records_partial_target_then_full_target():
    decision = DecisionSnapshot(... decision="long", features={"price": 100.0}, risk_budget=2.0, expected_edge=2.0, ...)
    label = label_lifecycle_decision(decision, [{"minute": 0, "price": 100}, {"minute": 1, "high": 102.2, "low": 100.5, "price": 101.8}, {"minute": 2, "high": 104.5, "low": 101.5, "price": 104.0}])
    assert label.partial_target_hit is True
    assert label.full_target_hit is True
    assert label.quality_label == "good"
```

- [ ] **Step 2: Run test and verify missing function failure**

Run: `.venv/bin/python -m pytest tests/test_intraday_experiment.py -q`

- [ ] **Step 3: Implement lifecycle labeler**

Reuse `ml.intraday_lifecycle.initialize_lifecycle`, `evaluate_exit_plan`, and `apply_filled_leg`. Convert price rows into bars with `h/l/c`, compute net R from filled legs, and return pending when entry price or path bars are missing.

- [ ] **Step 4: Add stop-first regression test**

If a bar hits full stop and full target together, `full_stop_hit` must be true and `quality_label == "bad"`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_intraday_experiment.py tests/test_intraday_lifecycle.py -q`

---

### Task 3: Common Strategy Validation Gate

**Files:**
- Create: `ml/strategy_gate.py`
- Test: `tests/test_strategy_gate.py`

**Interfaces:**
- Produces: `strategy_gate(returns, benchmark_returns=None, *, n_trials: int = 1, min_samples: int = 60) -> dict`

- [ ] **Step 1: Write failing gate tests**

```python
def test_strategy_gate_rejects_small_samples():
    assert strategy_gate([0.01] * 10)["verdict"] == "NO-GO"
```

- [ ] **Step 2: Run test and verify missing module/function failure**

Run: `.venv/bin/python -m pytest tests/test_strategy_gate.py -q`

- [ ] **Step 3: Implement minimal gate**

Use `ml.validation.sharpe_ratio`, `deflated_sharpe_ratio`, and `ml.adaptive.reward.max_drawdown`. Return `GO` only when sample count passes, cumulative excess is positive, DSR is either unavailable for `n_trials <= 1` or at least 0.50, and strategy MDD is no worse than `1.1x` benchmark MDD if benchmark is provided.

- [ ] **Step 4: Add benchmark drawdown regression test**

A strategy that outperforms but has much worse drawdown should return `OBSERVE`, not `GO`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_strategy_gate.py -q`

---

### Task 4: Verification

**Files:**
- Test: affected test suite

- [ ] **Step 1: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_event_backtest.py tests/test_intraday_experiment.py tests/test_strategy_gate.py tests/test_intraday_lifecycle.py -q`

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest -q`

- [ ] **Step 3: Report remaining trade-offs**

Document that this slice creates shared primitives and labels, while migration of all existing `backtest/*` scripts to the event engine remains a follow-up.
