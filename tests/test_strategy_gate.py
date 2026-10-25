"""Common strategy validation gate."""

import math

import numpy as np
import pandas as pd
import pytest

from ml.strategy_gate import strategy_gate


def test_strategy_gate_rejects_small_samples():
    result = strategy_gate([0.01] * 10, min_samples=60)

    assert result["verdict"] == "NO-GO"
    assert "min_samples" in result["reasons"]


def test_strategy_gate_go_for_positive_excess_with_controlled_drawdown():
    returns = [0.002, -0.001, 0.003, 0.001] * 30
    benchmark = [0.001, -0.001, 0.001, 0.0] * 30

    result = strategy_gate(returns, benchmark, min_samples=60, n_trials=1)

    assert result["verdict"] == "GO"
    assert result["metrics"]["n"] == 120
    assert result["metrics"]["excess_return"] > 0


def test_strategy_gate_observe_when_drawdown_is_much_worse_than_benchmark():
    returns = [0.01] * 70 + [-0.20] + [0.01] * 70
    benchmark = [0.001] * len(returns)

    result = strategy_gate(returns, benchmark, min_samples=60, n_trials=1)

    assert result["verdict"] == "OBSERVE"
    assert "drawdown" in result["reasons"]


# ── A-fix-learn: pairwise 정렬 후 최소 표본 재검증 (A-06) ─────────────────────────


def _never_go_below_min(result, min_samples):
    assert not (result["verdict"] == "GO" and result["metrics"]["n"] < min_samples)


@pytest.mark.parametrize("as_list", [False, True])
@pytest.mark.parametrize("benchmark", [[0.0], []])
def test_strategy_gate_rejects_length_mismatched_or_empty_benchmark(benchmark, as_list):
    returns = np.tile([0.01, 0.02], 30)
    result = strategy_gate(list(returns) if as_list else returns, benchmark, min_samples=60)

    assert result["verdict"] == "NO-GO"
    assert result["reasons"] == ["benchmark_misaligned"]
    _never_go_below_min(result, 60)


def test_strategy_gate_drops_rows_pairwise_and_rechecks_min_samples():
    returns = [0.002, -0.001, 0.003, 0.001] * 15            # 60
    benchmark = [0.001, -0.001, 0.001, 0.0] * 15
    returns[0] = math.nan                                     # 전략만 결측
    benchmark[1] = math.inf                                   # 벤치마크만 결측 → 공통 유효 58

    result = strategy_gate(returns, benchmark, min_samples=59)

    assert result["verdict"] == "NO-GO"
    assert "min_samples" in result["reasons"]
    assert result["metrics"]["n"] == 58


def test_strategy_gate_compares_same_rows_after_asymmetric_missing():
    r = np.array([0.01, 0.02, -0.01, 0.03] * 20)
    b = np.array([0.005, 0.01, -0.02, 0.01] * 20)
    r_nan, b_nan = r.copy(), b.copy()
    r_nan[0] = np.nan
    b_nan[1] = np.nan

    result = strategy_gate(r_nan, b_nan, min_samples=60)

    keep = np.ones(len(r), dtype=bool)
    keep[[0, 1]] = False
    expected = float(np.prod(1 + r[keep]) - np.prod(1 + b[keep]))
    assert result["metrics"]["n"] == 78
    assert result["metrics"]["excess_return"] == pytest.approx(expected, abs=1e-6)


def test_strategy_gate_aligns_indexed_series_by_date():
    idx = pd.date_range("2026-01-01", periods=80, freq="B")
    r = pd.Series([0.002, -0.001, 0.003, 0.001] * 20, index=idx)
    b = pd.Series([0.001] * 59, index=idx[-59:])            # 공통 날짜 59

    result = strategy_gate(r, b, min_samples=60)

    assert result["verdict"] == "NO-GO" and result["metrics"]["n"] == 59


def test_strategy_gate_multi_trial_without_trial_variance_is_not_go():
    returns = [0.002, -0.001, 0.003, 0.001] * 30
    benchmark = [0.001, -0.001, 0.001, 0.0] * 30

    result = strategy_gate(returns, benchmark, min_samples=60, n_trials=5)

    assert result["verdict"] == "NO-GO"
    assert "dsr_unavailable" in result["reasons"]
