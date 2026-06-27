#!/usr/bin/env python3
"""test_sideways_backtest.py — 횡보 백테스트 순수 헬퍼 (무네트워크). conftest 가 backtest/ 를 path 에 추가."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))


def test_phase_alloc_buckets():
    from sideways_backtest import _phase_alloc
    assert _phase_alloc(0.0) == (0.92, 0.00, 0.00, 0.08)        # 고점 근처
    assert _phase_alloc(-0.12)[1] > 0                            # phase2 레버리지(QLD) 진입
    assert _phase_alloc(-0.35)[2] == 0.60                        # 크래시 TQQQ
    assert abs(sum(_phase_alloc(-0.07)) - 1.0) < 1e-9           # 합 1.0


def test_simulate_return_and_cost():
    from sideways_backtest import _simulate
    idx = pd.bdate_range("2024-01-01", periods=4)
    px = pd.DataFrame({"A": [100, 110, 121, 121], "B": [100, 100, 100, 100]}, index=idx, dtype=float)
    w = pd.DataFrame({"A": [1.0, 1.0, 1.0, 1.0], "B": [0.0, 0.0, 0.0, 0.0]}, index=idx)
    nav, net = _simulate(px, w, cost_bps=0)
    # shift(1): day2 수익 = A +10%, day3 = +10% → nav 끝 ≈ 1.21
    assert abs(nav.iloc[-1] - 1.21) < 1e-6
    # 비용: 첫날 A 0→1 전환 turnover=1 → 5bps 비용 반영 시 net 하락
    nav_c, _ = _simulate(px, w, cost_bps=5)
    assert nav_c.iloc[-1] < nav.iloc[-1]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
