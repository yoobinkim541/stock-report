"""Tests for ml/sweet_spot.py — synthetic data, threshold strategy, optimizer."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.backtest import BacktestResult
from ml.sweet_spot import (
    SweetSpotResult,
    evaluate_threshold_strategy,
    generate_synthetic_market_data,
    optimize_sweet_spot,
    _generate_ml_signal,
)


# ---------------------------------------------------------------------------
# generate_synthetic_market_data
# ---------------------------------------------------------------------------

class TestGenerateSyntheticMarketData:
    def test_default_shape(self):
        data = generate_synthetic_market_data()
        assert len(data["close"]) == 756
        assert len(data["spy_close"]) == 756
        assert len(data["qqq_close"]) == 756
        assert data["features"].shape == (756, 3)

    def test_feature_columns(self):
        data = generate_synthetic_market_data()
        assert set(data["features"].columns) == {"momentum", "volatility", "sentiment"}

    def test_deterministic(self):
        d1 = generate_synthetic_market_data(seed=42)
        d2 = generate_synthetic_market_data(seed=42)
        pd.testing.assert_series_equal(d1["close"], d2["close"])
        pd.testing.assert_frame_equal(d1["features"], d2["features"])

    def test_different_seeds_differ(self):
        d1 = generate_synthetic_market_data(seed=0)
        d2 = generate_synthetic_market_data(seed=1)
        assert not d1["close"].equals(d2["close"])

    def test_prices_positive(self):
        data = generate_synthetic_market_data()
        assert (data["close"] > 0).all()
        assert (data["spy_close"] > 0).all()
        assert (data["qqq_close"] > 0).all()

    def test_features_no_nan(self):
        data = generate_synthetic_market_data()
        assert data["features"].isna().sum().sum() == 0

    def test_custom_n(self):
        data = generate_synthetic_market_data(n=200, seed=7)
        assert len(data["close"]) == 200
        assert data["features"].shape[0] == 200


# ---------------------------------------------------------------------------
# evaluate_threshold_strategy — shift(1) / no-lookahead
# ---------------------------------------------------------------------------

class TestEvaluateThresholdStrategy:
    def _flat_data(self, n: int = 30):
        """Flat prices so returns are 0; isolates position logic."""
        idx = pd.date_range("2022-01-03", periods=n, freq="B")
        close = pd.Series(np.ones(n) * 100.0, index=idx, name="asset")
        sentinel = np.concatenate([np.full(n // 2, -1.0), np.full(n - n // 2, 1.0)])
        features = pd.DataFrame({"sentiment": sentinel}, index=idx)
        return {
            "close": close,
            "spy_close": close.rename("SPY"),
            "qqq_close": close.rename("QQQ"),
            "features": features,
        }

    def test_returns_backtest_result(self):
        data = generate_synthetic_market_data(n=200, seed=0)
        result = evaluate_threshold_strategy(data, {})
        assert isinstance(result, BacktestResult)

    def test_invalid_signal_col_raises_value_error(self):
        data = generate_synthetic_market_data(n=50, seed=0)
        with pytest.raises(ValueError, match="signal_col"):
            evaluate_threshold_strategy(data, {"signal_col": "nonexistent"})

    def test_n_days_equals_input(self):
        # shift(1)[0] = NaN → map() returns safe_weight (not NaN) → no row dropped
        data = generate_synthetic_market_data(n=100, seed=0)
        result = evaluate_threshold_strategy(data, {})
        assert result.n_days == 100

    def test_shift_prevents_lookahead(self):
        """Position at day t is based on signal[t-1], not signal[t].

        Setup: flat prices (+1% each day), signal = -1 for first half, +1 for second half.
        With shift(1): strategy is out during first half (based on prior -1 signal)
                       and in during second half → captures fewer returns than buy-and-hold.
        """
        n = 40
        idx = pd.date_range("2022-01-03", periods=n, freq="B")
        ret_vals = np.full(n, 0.01)
        prices = pd.Series(100 * np.cumprod(1 + ret_vals), index=idx, name="asset")
        # Signal: -1 for first 20 days, +1 for last 20 days
        signal = np.concatenate([np.full(20, -1.0), np.full(20, 1.0)])
        features = pd.DataFrame({"sentiment": signal}, index=idx)
        data = {
            "close": prices,
            "spy_close": prices.rename("SPY"),
            "qqq_close": prices.rename("QQQ"),
            "features": features,
        }
        result = evaluate_threshold_strategy(
            data, {"threshold": 0.0, "max_weight": 1.0, "safe_weight": 0.0}
        )
        bah_ret = float((1.01 ** n) - 1)
        # Strategy only captures returns after signal turns positive (second half)
        assert result.cumulative_return < bah_ret
        assert result.cumulative_return > 0  # still earns during in-market days

    def test_flat_prices_zero_return(self):
        data = self._flat_data(30)
        result = evaluate_threshold_strategy(data, {"threshold": 0.0, "max_weight": 1.0, "safe_weight": 0.0})
        assert result.cumulative_return == pytest.approx(0.0, abs=1e-9)

    def test_safe_weight_zero_means_full_cash(self):
        """All-cash strategy should produce zero return."""
        data = generate_synthetic_market_data(n=200, seed=5)
        # With threshold=100 (always below), strategy always stays in safe_weight=0
        result = evaluate_threshold_strategy(
            data, {"threshold": 100.0, "max_weight": 1.0, "safe_weight": 0.0}
        )
        assert result.cumulative_return == pytest.approx(0.0, abs=1e-9)

    def test_max_weight_1_fully_invested(self):
        """max_weight=1, threshold very negative → always fully invested → equals buy-and-hold."""
        data = generate_synthetic_market_data(n=756, seed=1)
        result = evaluate_threshold_strategy(
            data, {"threshold": -999.0, "max_weight": 1.0, "safe_weight": 0.0}
        )
        from ml.backtest import buy_and_hold
        bah = buy_and_hold(data["close"])
        # Equity curves differ slightly because of the first-row dropna in strategy
        # but cumulative returns should be very close
        assert abs(result.cumulative_return - bah.cumulative_return) < 0.02

    def test_turnover_non_negative(self):
        data = generate_synthetic_market_data(n=200, seed=3)
        result = evaluate_threshold_strategy(data, {})
        assert result.turnover is not None
        assert result.turnover >= 0.0


# ---------------------------------------------------------------------------
# optimize_sweet_spot
# ---------------------------------------------------------------------------

class TestOptimizeSweetSpot:
    def test_returns_sweet_spot_result(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        assert isinstance(result, SweetSpotResult)

    def test_best_result_is_backtest_result(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        assert isinstance(result.best_result, BacktestResult)

    def test_ml_result_is_backtest_result(self):
        """ml_result must be an actual OOS ExcessReturnModel result, not a threshold copy."""
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        assert isinstance(result.ml_result, BacktestResult)
        assert "OOS" in result.ml_result.name or "ExcessReturnModel" in result.ml_result.name

    def test_best_score_ge_baseline_score(self):
        """Grid search must find params at least as good as baseline (threshold=0)."""
        from ml.optimization import composite_score
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        qqq_cagr = result.qqq_result.cagr or 0.0

        def _score(r: BacktestResult) -> float:
            return composite_score(
                cagr=r.cagr,
                max_drawdown=r.max_drawdown,
                turnover=r.turnover or 0.0,
                excess_return=(r.cagr or 0.0) - qqq_cagr,
            )

        assert _score(result.best_result) >= _score(result.baseline_result) - 1e-9

    def test_trials_has_expected_columns(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        for col in ("threshold", "max_weight", "safe_weight", "score", "cagr", "max_drawdown", "sharpe"):
            assert col in result.trials.columns, f"missing column: {col}"

    def test_trials_count_matches_grid(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        grid = {"threshold": [-0.5, 0.5], "max_weight": [1.0], "safe_weight": [0.0, 0.1]}
        result = optimize_sweet_spot(data, param_grid=grid)
        assert len(result.trials) == 2 * 1 * 2  # 4 combos

    def test_equity_df_has_expected_columns(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        for col in ("ML_model", "threshold", "SPY", "QQQ"):
            assert col in result.equity.columns

    def test_equity_df_not_empty(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        assert len(result.equity) > 0

    def test_wf_summary_keys(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        for key in ("n_folds", "mean_sharpe", "std_sharpe", "mean_cagr"):
            assert key in result.wf_summary, f"missing wf_summary key: {key}"

    def test_wf_summary_n_folds(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        assert result.wf_summary["n_folds"] == 2

    def test_weights_sum_to_one(self):
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        assert result.weights.sum() == pytest.approx(1.0, abs=1e-9)

    def test_default_data_used_when_none(self):
        """optimize_sweet_spot() with no args should work (uses default seed=42 data)."""
        result = optimize_sweet_spot()
        assert isinstance(result, SweetSpotResult)
        assert result.best_result.n_days > 0

    def test_deterministic(self):
        """Same seed → same best_params and ml_result."""
        data = generate_synthetic_market_data(seed=42)
        r1 = optimize_sweet_spot(data)
        r2 = optimize_sweet_spot(data)
        assert r1.best_params == r2.best_params
        assert r1.best_result.cumulative_return == pytest.approx(r2.best_result.cumulative_return)
        assert r1.ml_result.cumulative_return == pytest.approx(r2.ml_result.cumulative_return)


# ---------------------------------------------------------------------------
# plot_results (optional: only run if matplotlib is available)
# ---------------------------------------------------------------------------

class TestPlotResults:
    def test_returns_list(self):
        from ml.sweet_spot import plot_results
        data = generate_synthetic_market_data(n=756, seed=42)
        result = optimize_sweet_spot(data)
        paths = plot_results(result, outdir="/tmp")
        # Either returns [] (no matplotlib) or a list of existing file paths
        assert isinstance(paths, list)
        for p in paths:
            import os
            assert os.path.exists(p), f"expected file: {p}"
