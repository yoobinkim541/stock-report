#!/usr/bin/env python3
"""test_risk_model.py — Tier 1 리스크 모델 폐형해 단위테스트 (무네트워크·monkeypatch).

수학 정확성(공분산·리스크기여 Euler·유효분산 참여비·팩터 OLS·Kelly·낙폭예산)을
닫힌해로 검증. 네트워크 경로(fetch_returns/_estimate_portfolio_mdd)는 monkeypatch/회피.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml import risk_model as rm


# ── 공분산 ────────────────────────────────────────────────────────────
def test_cov_sample_equals_npcov():
    rng = np.random.default_rng(1)
    R = pd.DataFrame(rng.normal(0, 0.01, size=(120, 4)))
    got = rm.shrunk_cov(R, shrink=False)
    exp = np.cov(R.values, rowvar=False) * rm.TRADING_DAYS
    assert np.allclose(got, exp)


def test_cov_ledoitwolf_psd_and_better_conditioned():
    rng = np.random.default_rng(2)
    R = pd.DataFrame(rng.normal(0, 0.01, size=(60, 10)))     # T≈6×N → 표본 잡음 큼
    lw = rm.shrunk_cov(R, shrink=True)
    sample = rm.shrunk_cov(R, shrink=False)
    assert np.allclose(lw, lw.T)                              # 대칭
    assert np.linalg.eigvalsh(lw).min() > -1e-10             # PSD
    assert np.linalg.cond(lw) < np.linalg.cond(sample)       # 조건수 개선


# ── 리스크 기여 ───────────────────────────────────────────────────────
def test_rc_two_asset_closed_form():
    cov = [[0.04, 0.01], [0.01, 0.09]]
    out = rm.risk_contributions([0.5, 0.5], cov)
    assert out["pc"] == pytest.approx([1 / 3, 2 / 3], abs=1e-9)
    assert sum(out["pc"]) == pytest.approx(1.0)


def test_rc_euler_sums_to_one():
    rng = np.random.default_rng(3)
    A = rng.normal(size=(6, 6))
    cov = A @ A.T                                             # PSD
    w = np.abs(rng.normal(size=6)); w /= w.sum()
    out = rm.risk_contributions(w, cov)
    assert sum(out["pc"]) == pytest.approx(1.0)


def test_rc_equal_when_uncorrelated_equal_vol():
    cov = np.eye(4) * 0.04
    out = rm.risk_contributions([0.25] * 4, cov)
    assert out["pc"] == pytest.approx([0.25] * 4)


def test_rc_negative_contribution_for_hedge():
    cov = [[0.04, 0.03, -0.02], [0.03, 0.04, -0.02], [-0.02, -0.02, 0.03]]
    out = rm.risk_contributions([0.4, 0.4, 0.2], cov)
    assert out["pc"][2] < 0                                   # 헤지 자산 음수기여
    assert sum(out["pc"]) == pytest.approx(1.0)


def test_rc_zero_variance_returns_none():
    assert rm.risk_contributions([0.5, 0.5], np.zeros((2, 2))) is None


def test_rc_weight_scale_invariant():
    cov = [[0.04, 0.01], [0.01, 0.09]]
    a = rm.risk_contributions(np.array([0.5, 0.5]), cov)["pc"]
    b = rm.risk_contributions(np.array([0.5, 0.5]) * 1.7, cov)["pc"]
    assert np.allclose(a, b)


# ── 유효 분산 (참여비) ────────────────────────────────────────────────
def test_effective_bets_identity_equals_n():
    assert rm.effective_bets(np.eye(3)) == pytest.approx(3.0)


def test_effective_bets_all_ones_equals_one():
    assert rm.effective_bets(np.ones((3, 3))) == pytest.approx(1.0)


def test_effective_bets_two_blocks():
    c = np.array([[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 1, 1], [0, 0, 1, 1]], dtype=float)
    assert rm.effective_bets(c) == pytest.approx(2.0)


# ── 팩터 베타 ─────────────────────────────────────────────────────────
def _factors(seed=42, n=200):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"QQQ": rng.normal(0, 0.01, n), "TLT": rng.normal(0, 0.01, n)})


def test_factor_beta_recovery_single():
    f = _factors()
    R = pd.DataFrame({"H": 1.5 * f["QQQ"]})
    out = rm.factor_betas(R, f)
    b = out["beta"]["H"]
    assert b["mkt"] == pytest.approx(1.5, abs=1e-6)
    assert b["rate"] == pytest.approx(0.0, abs=1e-6)
    assert b["r2"] == pytest.approx(1.0, abs=1e-6)
    assert b["idio"] == pytest.approx(0.0, abs=1e-6)


def test_factor_beta_recovery_two():
    f = _factors()
    R = pd.DataFrame({"H": 0.8 * f["QQQ"] - 0.4 * f["TLT"]})
    b = rm.factor_betas(R, f)["beta"]["H"]
    assert b["mkt"] == pytest.approx(0.8, abs=1e-6)
    assert b["rate"] == pytest.approx(-0.4, abs=1e-6)


def test_factor_portfolio_net_beta():
    f = _factors()
    R = pd.DataFrame({"A": 1.0 * f["QQQ"], "B": 2.0 * f["QQQ"]})
    beta = rm.factor_betas(R, f)["beta"]
    w = {"A": 0.5, "B": 0.5}
    net = sum(w[t] * beta[t]["mkt"] for t in w)
    assert net == pytest.approx(1.5, abs=1e-6)


def test_factor_collinear_fallback():
    rng = np.random.default_rng(7)
    q = rng.normal(0, 0.01, 200)
    f = pd.DataFrame({"QQQ": q, "TLT": q + rng.normal(0, 1e-5, 200)})   # corr≈1
    out = rm.factor_betas(pd.DataFrame({"H": q}), f)
    assert out["factors"] == ["QQQ"]
    assert out["caveat"] is not None


# ── 레버리지 계기판 ───────────────────────────────────────────────────
def test_growth_optimal_kelly_formula():
    rng = np.random.default_rng(9)
    ret = pd.Series(rng.normal(0.0005, 0.0126, 500))         # 일변동성 ≈ ann 20%
    out = rm.growth_optimal_leverage(ret, rf=0.04)
    sigma = out["sigma"]
    assert out["kelly"]["moderate"] == pytest.approx((0.10 - 0.04) / sigma ** 2)
    assert out["half"]["moderate"] == pytest.approx(out["kelly"]["moderate"] / 2)


def test_drawdown_budget_cap():
    out = rm.drawdown_budget_leverage(0.40, budget=0.50)
    assert out["cap"] == pytest.approx(1.25)


def test_ruin_metrics_scaling_and_breach():
    out = rm.ruin_metrics(1.5, 0.35, budget=0.50)
    assert out["implied_mdd"] == pytest.approx(0.525)
    assert out["breach"] is True


def test_leverage_recommendation_uses_dd_cap():
    g = {"half": {"conservative": 0.2, "moderate": 0.7, "trailing": 1.8}}
    rec = rm.leverage_recommendation(g, {"cap": 1.4, "budget": 0.5}, current=0.9)
    assert rec["recommend"] == pytest.approx(1.4)
    assert rec["kelly_half"]                                   # 밴드 노출
    assert rec["current"] == pytest.approx(0.9)


# ── graceful / 요약 ───────────────────────────────────────────────────
def test_summary_single_holding_returns_none():
    assert rm.portfolio_risk_summary({"MSFT": 1.0}) is None


def test_summary_short_history_returns_none(monkeypatch):
    short = pd.DataFrame(np.random.default_rng(0).normal(0, 0.01, size=(30, 3)),
                         columns=["A", "B", "C"])
    short.attrs["dropped"] = []
    monkeypatch.setattr(rm, "fetch_returns", lambda *a, **k: short)
    assert rm.portfolio_risk_summary({"A": .3, "B": .3, "C": .4}) is None


def test_oneliner_empty_on_empty_weights():
    assert rm.risk_oneliner({}) == ""
    assert rm.dollar_vs_risk_table({}) == ""


def test_fetch_returns_inner_join(monkeypatch):
    import providers.market_data as md
    idx1 = pd.bdate_range("2024-01-01", periods=100)
    idx2 = pd.bdate_range("2024-02-01", periods=100)            # 오프셋 → 교집합 < 100

    def fake_hist(sym, period="1y"):
        idx = idx1 if sym == "A" else idx2
        return pd.DataFrame({"Close": np.linspace(100, 120, len(idx))}, index=idx)

    monkeypatch.setattr(md, "_history_cached", fake_hist)
    df = rm.fetch_returns(["A", "B"])
    inter = idx1.intersection(idx2)
    assert len(df) == len(inter) - 1                            # pct_change 1행 손실
    assert list(df.columns) == ["A", "B"]


def test_format_risk_report_none():
    assert "데이터 부족" in rm.format_risk_report(None)


def test_concentration_note_warns_low_neff():
    s = {"n_eff": 4.0, "n_assets": 9, "contributions": [("ORCL", 0.11, 0.30)]}
    note = rm._concentration_note(s)
    assert "집중" in note and "ORCL" in note


def test_concentration_note_quiet_when_diversified():
    s = {"n_eff": 8.0, "n_assets": 9, "contributions": [("MSFT", 0.11, 0.12)]}
    assert rm._concentration_note(s) == ""


def test_concentration_note_none_graceful():
    assert rm._concentration_note(None) == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
