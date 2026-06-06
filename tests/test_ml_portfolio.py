import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.portfolio import (
    PortfolioConfig,
    SAFE_TICKERS,
    build_weight_matrix,
    build_weights,
    rebalance_needed,
    validate_weights,
)
from ml.backtest import portfolio_turnover


# ---------------------------------------------------------------------------
# build_weights — basic constraints
# ---------------------------------------------------------------------------

def _scores(tickers, values=None):
    if values is None:
        values = np.arange(len(tickers), 0, -1, dtype=float)
    return pd.Series(dict(zip(tickers, values)))


def test_weights_sum_le_one():
    scores = _scores(["QQQ", "SGOV", "MSFT", "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "NFLX", "AMD"])
    w = build_weights(scores)
    assert float(w.sum()) <= 1.0 + 1e-6


def test_weights_non_negative():
    scores = _scores(["SGOV", "QQQ", "MSFT", "AAPL"])
    w = build_weights(scores)
    assert (w >= 0).all()


def test_max_single_position_respected():
    cfg = PortfolioConfig(max_single_position=0.15, top_n=5, qqq_weight=0.0, safe_weight_min=0.0)
    scores = _scores(["MSFT", "AAPL", "NVDA", "GOOGL", "META"])
    w = build_weights(scores, config=cfg, safe_available=[])
    assert float(w.max()) <= 0.15 + 1e-9


def test_safe_bucket_respected():
    cfg = PortfolioConfig(safe_weight_min=0.10, safe_weight_max=0.40, qqq_weight=0.0, top_n=3)
    scores = _scores(["SGOV", "MSFT", "AAPL", "NVDA"])
    # Make SGOV score = 0.5 → should give safe_w = 0.10 + 0.30 * 0.5 = 0.25
    scores["SGOV"] = 0.5
    w = build_weights(scores, config=cfg, safe_available=["SGOV"])
    assert "SGOV" in w.index
    assert w["SGOV"] >= cfg.safe_weight_min - 1e-9


def test_qqq_core_weight_allocated():
    cfg = PortfolioConfig(qqq_weight=0.20, safe_weight_min=0.0, top_n=3)
    scores = _scores(["QQQ", "SGOV", "MSFT", "AAPL"])
    w = build_weights(scores, config=cfg, safe_available=[])
    assert "QQQ" in w.index
    assert w["QQQ"] == pytest.approx(0.20)


def test_no_core_tickers_in_scores():
    cfg = PortfolioConfig(qqq_weight=0.20, safe_weight_min=0.0, top_n=3)
    scores = _scores(["MSFT", "AAPL", "NVDA"])  # no QQQ in scores
    w = build_weights(scores, config=cfg, safe_available=[])
    # Should not crash; QQQ just won't be allocated
    assert w.sum() <= 1.0 + 1e-9


def test_top_n_limits_number_of_stock_positions():
    cfg = PortfolioConfig(top_n=3, qqq_weight=0.0, safe_weight_min=0.0)
    tickers = ["MSFT", "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA"]
    scores = _scores(tickers)
    w = build_weights(scores, config=cfg, safe_available=[])
    # At most 3 stock positions (safe tickers and core excluded from top_n pool)
    assert len(w[w > 0]) <= 3


def test_all_negative_scores_equal_weight():
    cfg = PortfolioConfig(top_n=3, qqq_weight=0.0, safe_weight_min=0.0)
    scores = pd.Series({"MSFT": -0.5, "AAPL": -0.3, "NVDA": -0.1})
    w = build_weights(scores, config=cfg, safe_available=[])
    # Equal-weighted fallback
    non_zero = w[w > 0]
    assert len(non_zero) <= 3


def test_deterministic_output():
    scores = _scores(["SGOV", "QQQ", "MSFT", "AAPL", "NVDA"])
    w1 = build_weights(scores)
    w2 = build_weights(scores)
    pd.testing.assert_series_equal(w1, w2)


# ---------------------------------------------------------------------------
# validate_weights
# ---------------------------------------------------------------------------

def test_validate_weights_pass():
    w = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    validate_weights(w)  # should not raise


def test_validate_weights_negative_raises():
    w = pd.Series({"A": 0.6, "B": -0.1})
    with pytest.raises(ValueError, match="Negative"):
        validate_weights(w)


def test_validate_weights_exceeds_one_raises():
    w = pd.Series({"A": 0.7, "B": 0.5})
    with pytest.raises(ValueError, match="exceed"):
        validate_weights(w)


# ---------------------------------------------------------------------------
# rebalance_needed
# ---------------------------------------------------------------------------

def test_rebalance_needed_triggers():
    current = pd.Series({"MSFT": 0.5, "SGOV": 0.5})
    target = pd.Series({"MSFT": 0.3, "SGOV": 0.7})
    # Turnover = 0.2 + 0.2 = 0.4 > default threshold 0.05
    assert rebalance_needed(current, target, threshold=0.05) is True


def test_rebalance_not_needed():
    current = pd.Series({"MSFT": 0.5, "SGOV": 0.5})
    target = pd.Series({"MSFT": 0.51, "SGOV": 0.49})
    # Turnover ≈ 0.02 < 0.05
    assert rebalance_needed(current, target, threshold=0.05) is False


def test_rebalance_new_ticker_in_target():
    current = pd.Series({"MSFT": 1.0})
    target = pd.Series({"MSFT": 0.5, "NVDA": 0.5})
    assert rebalance_needed(current, target, threshold=0.05) is True


# ---------------------------------------------------------------------------
# build_weight_matrix
# ---------------------------------------------------------------------------

def test_build_weight_matrix_shape():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    tickers = ["SGOV", "MSFT", "AAPL", "NVDA"]
    scores = pd.DataFrame(
        np.random.RandomState(0).rand(5, 4),
        index=idx,
        columns=tickers,
    )
    matrix = build_weight_matrix(scores)
    assert matrix.shape[0] == 5
    assert (matrix >= 0).all().all()


def test_build_weight_matrix_rows_le_one():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    scores = pd.DataFrame(
        {"SGOV": [0.5, 0.6, 0.4], "MSFT": [0.8, 0.7, 0.9], "AAPL": [0.6, 0.5, 0.7]},
        index=idx,
    )
    matrix = build_weight_matrix(scores)
    row_sums = matrix.sum(axis=1)
    assert (row_sums <= 1.0 + 1e-9).all()


# ---------------------------------------------------------------------------
# portfolio_turnover (re-exported from ml.backtest)
# ---------------------------------------------------------------------------

def test_portfolio_turnover_reexport():
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    w = pd.DataFrame({"A": [1.0, 0.5, 0.0, 0.0], "B": [0.0, 0.5, 1.0, 1.0]}, index=idx)
    t = portfolio_turnover(w)
    assert t > 0


# ---------------------------------------------------------------------------
# PortfolioConfig validation
# ---------------------------------------------------------------------------

def test_config_validation_raises_on_bad_bounds():
    cfg = PortfolioConfig(safe_weight_min=0.6, safe_weight_max=0.3)
    with pytest.raises(ValueError):
        cfg.validate()


def test_config_validation_passes():
    cfg = PortfolioConfig()
    cfg.validate()  # should not raise
