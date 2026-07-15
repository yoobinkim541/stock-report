"""tests/test_entry_calibration.py — 진입점수 워크포워드 캘리브레이션 w_div 배선 (무네트워크)."""
import numpy as np
import pandas as pd
import pytest

from entry_calibration import evaluate, grid_search


def _synthetic_samples(n=200, seed=5):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    div = rng.choice([-1.0, 0.0, 1.0], size=n, p=[0.1, 0.8, 0.1])
    # 강세 다이버전스일 때 선행수익이 체계적으로 더 좋게 — grid_search 가 w_div>0 을 고를 유인
    fwd = rng.normal(0.02, 0.05, n) + div * 0.03
    return pd.DataFrame({
        "ticker": "TEST", "date": dates, "category": "stock",
        "win_20": rng.uniform(0.4, 0.7, n), "exp_20": rng.uniform(0.0, 0.08, n),
        "p25_20": -rng.uniform(0.01, 0.05, n), "rsi": rng.uniform(30, 60, n),
        "dd": -rng.uniform(0.0, 0.2, n), "div": div, "fwd_20": fwd,
    })


def test_evaluate_accepts_div_column():
    samples = _synthetic_samples()
    params = {"w_win": 0.35, "w_rr": 0.25, "w_rsi": 0.15, "w_dd": 0.15, "w_div": 0.10,
              "enter_threshold": 0.55, "wait_threshold": 0.40}
    m = evaluate(samples, params)
    assert "n" in m and "mean_fwd" in m and "win_rate" in m


def test_evaluate_missing_div_column_defaults_gracefully():
    """div 컬럼이 없는 구버전 샘플 프레임도 크래시 없이 평가돼야 한다."""
    samples = _synthetic_samples().drop(columns=["div"])
    params = {"w_win": 0.40, "w_rr": 0.30, "w_rsi": 0.15, "w_dd": 0.15, "w_div": 0.0,
              "enter_threshold": 0.55, "wait_threshold": 0.40}
    m = evaluate(samples, params)
    assert m["n"] >= 0


def test_grid_search_includes_w_div_axis_and_respects_weight_sum():
    samples = _synthetic_samples(n=400)
    best, metrics = grid_search(samples)
    assert best is None or "w_div" in best
    if best is not None:
        total = best["w_win"] + best["w_rr"] + best["w_rsi"] + best["w_dd"] + best["w_div"]
        assert total == pytest.approx(1.0, abs=1e-6)
        assert 0.0 <= best["w_dd"] <= 0.35
