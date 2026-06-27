#!/usr/bin/env python3
"""test_factor_premium_backtest.py — Tier 4 팩터 게이트 폐형해 (무네트워크)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))

import factor_premium_backtest as fp


# ── 순수 코어 ─────────────────────────────────────────────────────────
def test_excess_series_subtracts_market():
    assert np.allclose(fp.excess_series([0.02, 0.01], [0.01, 0.01]), [0.01, 0.00])


def test_excess_zero_when_identical():
    r = [0.01, -0.02, 0.03]
    assert np.allclose(fp.excess_series(r, r), 0.0)


def test_factor_sleeve_no_drag_injected():
    r = np.array([0.01, -0.02, 0.005])
    assert np.allclose(fp.factor_sleeve_net(r, 0.0), r)        # 페널티 주입 없음


def test_blend_equal_weight():
    idx = pd.RangeIndex(3)
    a = pd.Series([0.02, 0.04, 0.0], index=idx)
    b = pd.Series([0.0, 0.0, 0.06], index=idx)
    assert np.allclose(fp.blend_returns([a, b]).values, [0.01, 0.02, 0.03])


# ── _factor_passes ────────────────────────────────────────────────────
def _f(factor, dsr=0.99, psr=0.99, obj=0.1, bear=0.5, mdd=40.0):
    return {"factor": factor, "dsr": dsr, "psr_excess": psr, "obj": obj,
            "bear_excess_sharpe": bear, "mdd": mdd}


def test_factor_passes_all_gates():
    assert fp._factor_passes(_f("m"), 0.2)


def test_factor_fails_low_dsr():
    assert not fp._factor_passes(_f("m", dsr=0.5), 0.2)


def test_factor_fails_low_psr():
    assert not fp._factor_passes(_f("m", psr=0.4), 0.2)


def test_factor_fails_high_pbo():
    assert not fp._factor_passes(_f("m"), 0.7)


def test_factor_fails_negative_objective():
    assert not fp._factor_passes(_f("m", obj=-0.1), 0.2)


# ── decide_verdict ────────────────────────────────────────────────────
def test_verdict_go_partitions_factors():
    v = fp.decide_verdict([_f("momentum"), _f("value", dsr=0.5)], 0.2)
    assert v["verdict"] == "GO"
    assert "momentum" in v["go_factors"] and "value" in v["nogo_factors"]


def test_verdict_nogo_lists_all():
    v = fp.decide_verdict([_f("value", dsr=0.5), _f("size", psr=0.4)], 0.2)
    assert v["verdict"] == "NO-GO" and set(v["nogo_factors"]) == {"value", "size"}


def test_verdict_conditional_when_bear_negative():
    v = fp.decide_verdict([_f("momentum", bear=-0.3)], 0.2)
    assert v["verdict"] == "GO" and "조건부" in v["note"]


# ── 통합 (monkeypatch·무네트워크) ─────────────────────────────────────
def test_run_all_structure(monkeypatch):
    rng = np.random.default_rng(4)
    idx = pd.bdate_range("2014-01-01", periods=1200)
    drift = {"SPY": 0.0004, "MTUM": 0.0006, "QUAL": 0.0003, "VLUE": 0.0002,
             "IWM": 0.00035, "USMV": 0.0003}

    def fake(sym):
        return pd.Series(100 * np.cumprod(1 + rng.normal(drift.get(sym, 0.0004), 0.011, 1200)), index=idx)

    monkeypatch.setattr(fp, "_fetch", fake)
    out = fp.run_all()
    assert out["verdict"] in ("GO", "NO-GO")
    assert len(out["factors"]) == 5
    assert out["pbo"] is not None
    assert set(out["go_factors"]) | set(out["nogo_factors"]) == {f["factor"] for f in out["factors"]}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
