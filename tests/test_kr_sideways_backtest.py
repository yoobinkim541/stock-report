#!/usr/bin/env python3
"""test_kr_sideways_backtest.py — KR 횡보 백테스트 순수 헬퍼 (무네트워크). conftest 가 backtest/ 를 path 에."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))


def test_buyhold_nav_tracks_price():
    from kr_sideways_backtest import _simulate
    idx = pd.bdate_range("2024-01-01", periods=4)
    close = pd.Series([100, 110, 121, 121.0], index=idx)
    w = pd.Series([1.0, 1.0, 1.0, 1.0], index=idx)
    nav = _simulate(close, w, sell_bps=0, buy_bps=0)
    # shift(1): day2 +10%, day3 +10% → ≈1.21
    assert abs(nav.iloc[-1] - 1.21) < 1e-6


def test_higher_sell_cost_lowers_nav():
    from kr_sideways_backtest import _simulate
    idx = pd.bdate_range("2024-01-01", periods=5)
    close = pd.Series([100.0] * 5, index=idx)               # flat → 비용만 차이
    w = pd.Series([1.0, 1.0, 0.0, 0.0, 0.0], index=idx)     # 1→0 매도 1회(재편입 없음)
    nav_lo = _simulate(close, w, sell_bps=10, buy_bps=0)
    nav_hi = _simulate(close, w, sell_bps=50, buy_bps=0)
    assert nav_hi.iloc[-1] < nav_lo.iloc[-1]                # 매도세 ↑ → nav ↓


def test_cash_in_sideways_cuts_drawdown():
    from kr_sideways_backtest import _simulate
    from ml.adaptive import reward
    idx = pd.bdate_range("2024-01-01", periods=6)
    close = pd.Series([100, 90, 80, 80, 88, 96.0], index=idx)        # 급락 구간 존재
    base = pd.Series([1.0] * 6, index=idx)
    treat = pd.Series([1.0, 0.0, 0.0, 1.0, 1.0, 1.0], index=idx)     # 급락일 현금 회피
    base_mdd = reward.max_drawdown(list(_simulate(close, base, 0, 0).values))
    treat_mdd = reward.max_drawdown(list(_simulate(close, treat, 0, 0).values))
    assert treat_mdd < base_mdd


def test_run_includes_validation(monkeypatch):
    """run() 산출물에 Tier2 'validation' 필드(psr·dsr·n_trials) 배선 확인 (무네트워크·_fetch 모킹)."""
    import kr_sideways_backtest as kr
    idx = pd.bdate_range("2015-01-01", periods=800)
    rng = np.random.default_rng(3)
    prices = pd.Series(100 * np.cumprod(1 + rng.normal(0.0003, 0.01, 800)), index=idx)
    monkeypatch.setattr(kr, "_fetch", lambda *a, **k: prices)
    res = kr.run()
    assert res.get("validation") is not None
    val = res["validation"]
    assert 0.0 <= val["psr"] <= 1.0
    assert val["n_trials"] == 3


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
