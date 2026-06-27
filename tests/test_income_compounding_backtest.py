#!/usr/bin/env python3
"""test_income_compounding_backtest.py — Tier 5 인컴 복리 게이트 폐형해 (무네트워크)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))

import income_compounding_backtest as inc


# ── 순수 코어 ─────────────────────────────────────────────────────────
def test_reinvest_nav_compounds():
    assert np.allclose(inc.reinvest_nav([0.1, 0.1]), [1.1, 1.21])


def test_cash_hoard_equals_price_when_no_div():
    assert np.allclose(inc.cash_hoard_nav([0.1, 0.1], [0.0, 0.0], [0.0, 0.0]), [1.1, 1.21])


def test_reinvest_beats_hoard_compounding():
    # 무자본드리프트·배당만: 재투자가 분배를 복리 → 현금비축보다 끝값 큼
    total = (1 + np.zeros(2)) * (1 + np.array([0.05, 0.05])) - 1
    rein = inc.reinvest_nav(total)[-1]
    hoard = inc.cash_hoard_nav([0.0, 0.0], [0.05, 0.05], [0.0, 0.0])[-1]
    assert rein > hoard


def test_after_tax_zero_tax_is_total():
    p = np.array([0.01, -0.02]); d = np.array([0.004, 0.004])
    total = (1 + p) * (1 + d) - 1
    assert np.allclose(inc.after_tax_total_ret(p, d, 0.0), total)


def test_after_tax_zero_div_is_price():
    p = np.array([0.01, -0.02])
    assert np.allclose(inc.after_tax_total_ret(p, np.zeros(2), 0.154), p)


def test_after_tax_reduces_and_widens_gap():
    p = np.array([0.0, 0.0]); d = np.array([0.01, 0.01])
    total = (1 + p) * (1 + d) - 1
    at = inc.after_tax_total_ret(p, d, 0.5)
    assert np.all(at < total)                                  # 세금이 수익 깎음 → QQQ 격차 확대


# ── decide_verdict ────────────────────────────────────────────────────
def test_verdict_nogo_includes_reinvest_discipline():
    v = inc.decide_verdict({"after_tax_psr": 0.2, "after_tax_dsr": 0.9, "obj": -0.5,
                            "pbo": 0.3, "reinvest_vs_hoard_gap": 18.0, "after_tax_gap": -9.0})
    assert v["verdict"] == "NO-GO"
    assert "재투자" in v["note"] and "18" in v["note"]


def test_verdict_go_when_engine_wins():
    v = inc.decide_verdict({"after_tax_psr": 0.99, "after_tax_dsr": 0.99, "obj": 0.3,
                            "pbo": 0.2, "reinvest_vs_hoard_gap": 5.0, "after_tax_gap": 1.0})
    assert v["verdict"] == "GO"


def test_verdict_nogo_when_pbo_high():
    v = inc.decide_verdict({"after_tax_psr": 0.99, "after_tax_dsr": 0.99, "obj": 0.3,
                            "pbo": 0.8, "reinvest_vs_hoard_gap": 5.0, "after_tax_gap": 1.0})
    assert v["verdict"] == "NO-GO"


# ── 통합 (monkeypatch·무네트워크) ─────────────────────────────────────
def test_run_all_structure(monkeypatch):
    rng = np.random.default_rng(8)
    idx = pd.bdate_range("2014-01-01", periods=1300)

    def fake(sym, adjust=True):
        drift = {"QQQ": 0.0006, "QYLD": 0.0002, "SHV": 0.00004}.get(sym, 0.0002)
        if sym == "QYLD" and adjust:
            drift += 0.00025                                   # total > price (분배)
        return pd.Series(100 * np.cumprod(1 + rng.normal(drift, 0.008, 1300)), index=idx)

    monkeypatch.setattr(inc, "_fetch", fake)
    out = inc.run_all()
    assert out["verdict"] in ("GO", "NO-GO")
    assert "qyld_reinvest" in out and "qqq_total_return" in out
    assert out["pbo"] is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
