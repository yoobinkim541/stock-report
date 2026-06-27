#!/usr/bin/env python3
"""test_regime_classifier.py — 추세 vs 횡보 감지 (무네트워크, 합성·결정적)."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _series(vals):
    return pd.Series(vals, index=pd.bdate_range("2015-01-01", periods=len(vals)), dtype=float)


def _trend_then_range(n_trend=250, n_range=280, start=100.0, daily=0.001, amp=2.0, seed=0):
    """연속(점프 없는) 상승추세 후 평균회귀 박스권. range 가 충분히 길어 MA200·ER 이 횡보 반영."""
    rng = np.random.default_rng(seed)
    trend = [start * (1 + daily) ** i for i in range(n_trend)]
    lvl, p, flat = trend[-1], trend[-1], []
    for _ in range(n_range):
        p += rng.normal(0, amp) + (lvl - p) * 0.25      # lvl 로 끌어당김 = range-bound
        flat.append(p)
    return _series(trend + flat)


def test_efficiency_ratio_trend_vs_chop():
    from ml.regime_classifier import efficiency_ratio
    ramp = _series([100 + i for i in range(60)])          # 직선 추세
    assert efficiency_ratio(ramp, 20).iloc[-1] > 0.95     # ER≈1
    zig = _series([100 + (i % 2) for i in range(60)])     # 0/1 지그재그(제자리)
    assert efficiency_ratio(zig, 20).iloc[-1] < 0.10      # ER≈0


def test_uptrend_not_sideways():
    from ml.regime_classifier import classify_latest
    up = _series([100 * (1.001 ** i) for i in range(300)])   # 꾸준 상승(기울기 큼)
    r = classify_latest(up)
    assert r["sideways"] is False and r["er"] > 0.9


def test_range_detected_sideways():
    from ml.regime_classifier import classify_latest
    r = classify_latest(_trend_then_range(amp=1.5, seed=0))   # 연속 추세→박스권
    assert r["sideways"] is True
    assert r["substate"] in ("sideways_calm", "sideways_choppy")
    assert r["er"] < 0.30


def test_asymmetric_fast_exit():
    from ml.regime_classifier import classify_latest
    s = _trend_then_range(n_range=120, amp=1.2, seed=1)
    spike = pd.Series([s.iloc[-1] * (1.01 ** i) for i in range(1, 16)],
                      index=pd.bdate_range(s.index[-1] + pd.Timedelta(days=1), periods=15))
    r = classify_latest(pd.concat([s, spike]))               # 박스권 후 급반등(추세 재개)
    assert r["sideways"] is False                            # 비대칭 즉시 이탈


def test_regime_series_no_lookahead():
    from ml.regime_classifier import regime_series
    s = _trend_then_range(n_trend=220, n_range=320, amp=1.5, seed=2)
    full = regime_series(s)
    for t in (300, 420, len(s) - 1):                         # 과거만으로 계산한 값과 동일해야
        assert full.iloc[t] == regime_series(s.iloc[:t + 1]).iloc[-1]
    assert full.str.startswith("sideways").any()            # 박스권 라벨 발생


def test_classify_latest_insufficient_data_graceful():
    from ml.regime_classifier import classify_latest
    assert classify_latest(_series([100, 101, 102]))["sideways"] is False   # 데이터 부족 → graceful


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
