#!/usr/bin/env python3
"""test_longterm_adaptive.py — 장기 ★목적함수 스코어카드 (무네트워크)."""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import longterm_adaptive_eval as LT      # noqa: E402


def _recs(nav_vals, qqq_vals):
    return [{"date": f"2026-01-{i+1:02d}", "total_usd": n, "qqq_price": q}
            for i, (n, q) in enumerate(zip(nav_vals, qqq_vals))]


def test_scorecard_meets_objective_when_outperform_and_lower_mdd():
    nav = [100 + i for i in range(40)]          # 단조 상승(+39%), MDD 0
    qqq = [100 + i * 0.5 for i in range(40)]     # 더 완만(+19.5%), MDD 0
    sc = LT.scorecard(_recs(nav, qqq))
    assert sc["excess"] > 0 and sc["meets"] is True


def test_scorecard_fails_when_underperform():
    nav = [100 + i * 0.2 for i in range(40)]
    qqq = [100 + i for i in range(40)]           # 지수가 더 좋음
    sc = LT.scorecard(_recs(nav, qqq))
    assert sc["excess"] < 0 and sc["meets"] is False


def test_scorecard_fails_when_mdd_worse_even_if_outperform():
    # 전략이 더 올랐지만 중간 급락(깊은 MDD) → MDD>지수 → 목표 미달
    nav = [100, 160] + [80] + [100 + i for i in range(37)]   # 큰 낙폭
    qqq = [100 + i * 0.1 for i in range(40)]                  # 완만·얕은 MDD
    sc = LT.scorecard(_recs(nav, qqq))
    assert sc["strat_mdd"] > sc["qqq_mdd"] and sc["meets"] is False


def test_scorecard_none_on_insufficient():
    assert LT.scorecard(_recs([100, 101], [100, 101])) is None


def test_conservative_scale_reduces_only():
    assert LT._conservative_scale({"strat_mdd": 30.0, "qqq_mdd": 15.0}) == pytest.approx(0.5)
    assert LT._conservative_scale({"strat_mdd": 10.0, "qqq_mdd": 15.0}) == 1.0   # MDD 양호 → 축소 없음


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
