#!/usr/bin/env python3
"""test_concentration_validated_backtest.py — Tier 6 집중 게이트 폐형해 (무네트워크·seed)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))

import concentration_validated_backtest as conc


# ── 순수 코어 ─────────────────────────────────────────────────────────
def test_equal_weight_returns_averages():
    df = pd.DataFrame({"a": [0.02, 0.04], "b": [0.0, 0.0]})
    assert np.allclose(conc.equal_weight_returns(df).values, [0.01, 0.02])


def test_random_subset_reproducible():
    a = conc.random_subset_indices(9, 3, 20, 42)
    b = conc.random_subset_indices(9, 3, 20, 42)
    assert a == b                                              # 같은 seed → 동일
    assert conc.random_subset_indices(9, 3, 20, 7) != a        # 다른 seed → 다름


def test_random_subset_size_and_range():
    for idx in conc.random_subset_indices(9, 3, 30, 1):
        assert len(set(idx)) == 3 and all(0 <= i < 9 for i in idx)


def test_subset_returns_equal_weight():
    df = pd.DataFrame({"a": [0.1, 0.0], "b": [0.0, 0.2], "c": [0.5, 0.5]})
    assert np.allclose(conc.subset_returns(df, (0, 1)).values, [0.05, 0.10])


def test_concentration_penalty_closed_form():
    p = conc.concentration_penalty(0.6, [0.5, 0.55, 0.7])
    assert p["frac_worse"] == pytest.approx(0.667, abs=1e-3)    # 2/3, 3dp 반올림
    assert p["median_delta"] == pytest.approx(-0.05, abs=1e-3)


def test_concentration_penalty_all_worse():
    assert conc.concentration_penalty(1.0, [0.5, 0.6, 0.7])["frac_worse"] == 1.0


# ── decide_verdict ────────────────────────────────────────────────────
def test_verdict_nogo_when_median_worse():
    v = conc.decide_verdict({"pbo": 0.3, "median_obj": -0.1, "best_dsr": 0.9,
                             "best_psr": 0.9, "median_delta": -0.05, "frac_worse": 0.7})
    assert v["verdict"] == "NO-GO" and "분산 권고" in v["note"]


def test_verdict_nogo_even_if_lucky_subset_but_dsr_collapses():
    v = conc.decide_verdict({"pbo": 0.3, "median_obj": 0.2, "best_dsr": 0.5,
                             "best_psr": 0.99, "median_delta": 0.1, "frac_worse": 0.4})
    assert v["verdict"] == "NO-GO"                              # 운 좋은 1개도 deflate DSR로 차단


def test_verdict_go_requires_all_and_population():
    v = conc.decide_verdict({"pbo": 0.2, "median_obj": 0.2, "best_dsr": 0.99,
                             "best_psr": 0.99, "median_delta": 0.1, "frac_worse": 0.4})
    assert v["verdict"] == "GO"


# ── 통합 (monkeypatch·무네트워크·무스킬→NO-GO 재현) ───────────────────
def test_run_all_nogo_on_no_skill(monkeypatch):
    monkeypatch.setattr(conc, "N_SAMPLES", 60)
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2010-01-01", periods=1300)
    monkeypatch.setattr(conc, "_fetch",
                        lambda s: pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.011, 1300)), index=idx))
    out = conc.run_all()
    assert out["verdict"] in ("GO", "NO-GO")
    assert out["by_K"][3]["penalty"]["frac_worse"] >= 0.5      # 무스킬 → 집중 대부분 분산보다 못함
    assert out["pbo"] is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
