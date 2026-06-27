#!/usr/bin/env python3
"""test_us_mock_report.py — US 모의 평가 스코어카드 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import us_mock_report as R


def test_scorecard_hit_rates_and_ic():
    rows = [
        {"side": "편입", "correct": True, "policy_score": 0.9, "fwd_excess": 0.05},
        {"side": "편입", "correct": False, "policy_score": 0.3, "fwd_excess": -0.02},
        {"side": "편입", "correct": True, "policy_score": 0.7, "fwd_excess": 0.03},
        {"side": "퇴출", "correct": True, "policy_score": 0.1, "fwd_excess": -0.04},
        {"side": "퇴출", "correct": False, "policy_score": 0.2, "fwd_excess": 0.01},
    ]
    sc = R.compute_scorecard(rows)
    assert sc["buy_hit"] == pytest.approx(66.7, abs=0.2) and sc["n_buy"] == 3
    assert sc["sell_hit"] == pytest.approx(50.0) and sc["n_sell"] == 2
    assert sc["ic"] is not None and sc["ic"] > 0          # 정책점수↑ → 초과수익↑


def test_scorecard_empty():
    sc = R.compute_scorecard([])
    assert sc["buy_hit"] is None and sc["sell_hit"] is None and sc["ic"] is None
    assert sc["n_buy"] == 0 and sc["n_sell"] == 0


def test_scorecard_ic_needs_min_pairs():
    rows = [{"side": "편입", "correct": True, "policy_score": 0.9, "fwd_excess": 0.05},
            {"side": "편입", "correct": True, "policy_score": 0.7, "fwd_excess": 0.03}]
    assert R.compute_scorecard(rows)["ic"] is None        # <3 → IC 미산출


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
