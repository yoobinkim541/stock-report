#!/usr/bin/env python3
"""test_leverage_structural_backtest.py — Tier 3 레버리지 게이트 폐형해 (무네트워크)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))

import leverage_structural_backtest as lev


def _eff(w):
    return w.get("under", 0) * 1 + w.get("QLD", 0) * 2 + w.get("TQQQ", 0) * 3 + w.get("cash", 0) * 0


# ── synth LETF ────────────────────────────────────────────────────────
def test_synth_l1_identity():
    r = np.array([0.01, -0.02, 0.005])
    assert np.allclose(lev.synth_letf_daily(r, 1.0, 0.001), r)   # L=1 → r (감쇠/비용 0)


def test_synth_drag_emerges_not_injected():
    r = np.array([0.1, -0.1] * 12)                              # 변동성만, 평균≈0
    nav1 = np.prod(1 + lev.synth_letf_daily(r, 1.0, 0.0, 0.0))
    nav2 = np.prod(1 + lev.synth_letf_daily(r, 2.0, 0.0, 0.0))
    assert nav2 < nav1 < 1.0                                    # 감쇠 자연발생 (비용 0인데도)


def test_synth_financing_reduces():
    r = np.full(50, 0.001)
    assert lev.synth_letf_daily(r, 2.0, 0.0002).sum() < lev.synth_letf_daily(r, 2.0, 0.0).sum()


def test_synth_expense_reduces():
    r = np.full(50, 0.001)
    hi = lev.synth_letf_daily(r, 2.0, 0.0, expense=0.02).sum()
    lo = lev.synth_letf_daily(r, 2.0, 0.0, expense=0.0).sum()
    assert hi < lo


# ── 비중 ──────────────────────────────────────────────────────────────
def test_near_high_effective_leverage():
    assert _eff(lev._near_high_weights(0.92)) == pytest.approx(0.92)
    assert _eff(lev._near_high_weights(1.3)) == pytest.approx(1.3)
    assert _eff(lev._near_high_weights(1.5)) == pytest.approx(1.5)


def test_deep_bucket_ignores_base_lev():
    assert lev._bucket_weights(-0.30, 1.0) == lev._bucket_weights(-0.30, 1.5)  # 깊은 낙폭 동일
    assert lev._bucket_weights(-0.20, 1.0) == lev._bucket_weights(-0.20, 1.5)


def test_near_high_uses_base_lev():
    assert lev._bucket_weights(-0.01, 1.3) != lev._bucket_weights(-0.01, 1.0)  # near-high만 차이


# ── _best_L / decide_verdict ──────────────────────────────────────────
def _g(L, ok=True, obj=0.1, dsr=0.99, px=0.99, mdd=40.0):
    return {"L": L, "budget_ok": ok, "obj": obj, "dsr": dsr, "psr_excess": px, "mdd": mdd}


def test_best_L_picks_max_passing():
    grid = [_g(1.0), _g(1.2), _g(1.3), _g(1.4, ok=False)]
    assert lev._best_L(grid, {"pbo": 0.2}) == 1.3


def test_best_L_none_when_pbo_high():
    assert lev._best_L([_g(1.2)], {"pbo": 0.7}) is None


def test_best_L_none_when_budget_breach():
    assert lev._best_L([_g(1.2, ok=False)], {"pbo": 0.2}) is None


def test_best_L_none_when_dsr_low():
    assert lev._best_L([_g(1.2, dsr=0.5)], {"pbo": 0.2}) is None


def _proxy(best_L):
    return {"best_L": best_L, "grid": [_g(l) for l in (1.2, 1.3, 1.4, 1.5)]}


def test_verdict_go_both_proxies():
    v = lev.decide_verdict({"SPY": _proxy(1.2), "QQQ": _proxy(1.3)})
    assert v["verdict"] == "GO" and v["reco_L"] == 1.2          # 보수적 min


def test_verdict_nogo():
    assert lev.decide_verdict({"SPY": _proxy(None), "QQQ": _proxy(None)})["verdict"] == "NO-GO"


def test_verdict_conditional_on_disagreement():
    assert lev.decide_verdict({"SPY": _proxy(1.3), "QQQ": _proxy(None)})["verdict"] == "조건부"


# ── 통합 (monkeypatch·무네트워크) ─────────────────────────────────────
def test_run_all_structure(monkeypatch):
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2005-01-01", periods=1500)
    px = pd.Series(100 * np.cumprod(1 + rng.normal(0.0003, 0.012, 1500)), index=idx)
    monkeypatch.setattr(lev, "_fetch", lambda s: px)
    monkeypatch.setattr(lev, "_rf_series",
                        lambda index: (pd.Series(0.03 / 252, index=index),
                                       pd.Series(0.035 / 252, index=index)))
    out = lev.run_all()
    assert out["verdict"]["verdict"] in ("GO", "조건부", "NO-GO")
    assert len(out["results"]["SPY"]["grid"]) == 5
    assert out["results"]["SPY"]["pbo"] is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
