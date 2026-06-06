import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.models import (
    ExcessReturnModel,
    MarketRiskModel,
    lightgbm_available,
    news_feature_ablation,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _xy_classification(n=100, seed=42):
    rng = np.random.RandomState(seed)
    X = rng.randn(n, 5)
    y = (X[:, 0] > 0).astype(int)
    return X, y


def _xy_regression(n=100, seed=42):
    rng = np.random.RandomState(seed)
    X = rng.randn(n, 5)
    y = X[:, 0] * 0.5 + rng.randn(n) * 0.1
    return X, y


def _feature_names():
    return ["rsi_14", "mom_5d", "news_sentiment", "news_count", "vol_21d"]


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def test_lightgbm_available_returns_bool():
    assert isinstance(lightgbm_available(), bool)


# ---------------------------------------------------------------------------
# MarketRiskModel
# ---------------------------------------------------------------------------

def test_market_risk_model_classification():
    X, y = _xy_classification()
    model = MarketRiskModel(task="classification")
    model.fit(X[:70], y[:70])
    assert model.is_fitted
    preds = model.predict(X[70:])
    assert preds.shape == (30,)
    assert set(preds).issubset({0, 1})


def test_market_risk_model_regression():
    X, y = _xy_regression()
    model = MarketRiskModel(task="regression")
    model.fit(X[:70], y[:70])
    preds = model.predict(X[70:])
    assert preds.shape == (30,)
    assert np.all(np.isfinite(preds))


def test_market_risk_model_predict_proba():
    X, y = _xy_classification()
    model = MarketRiskModel(task="classification")
    model.fit(X[:70], y[:70])
    proba = model.predict_proba(X[70:])
    assert proba.shape[0] == 30
    assert proba.shape[1] == 2


def test_market_risk_model_backend_string():
    model = MarketRiskModel()
    assert model.backend in ("lightgbm", "sklearn", "mean_baseline")


def test_market_risk_model_invalid_task():
    with pytest.raises(ValueError, match="task must be"):
        MarketRiskModel(task="invalid")


def test_market_risk_model_feature_names_stored():
    X, y = _xy_classification()
    model = MarketRiskModel()
    feat = _feature_names()
    model.fit(X[:70], y[:70], feature_names=feat)
    assert model.feature_names_ == feat


# ---------------------------------------------------------------------------
# ExcessReturnModel
# ---------------------------------------------------------------------------

def test_excess_return_model_fit_predict():
    X, y = _xy_regression()
    model = ExcessReturnModel()
    model.fit(X[:70], y[:70])
    assert model.is_fitted
    preds = model.predict(X[70:])
    assert preds.shape == (30,)


def test_excess_return_model_rank_scores():
    X, y = _xy_regression()
    model = ExcessReturnModel()
    model.fit(X[:70], y[:70])
    ranks = model.rank_scores(X[70:])
    assert len(ranks) == 30
    # Ranks should be a permutation of 0..29
    assert sorted(ranks) == list(range(30))


def test_excess_return_model_backend_string():
    model = ExcessReturnModel()
    assert model.backend in ("lightgbm", "sklearn", "mean_baseline")


# ---------------------------------------------------------------------------
# News feature ablation
# ---------------------------------------------------------------------------

def test_news_feature_ablation_returns_dict():
    X, y = _xy_regression()
    feat = _feature_names()
    result = news_feature_ablation(
        ExcessReturnModel,
        X[:70], y[:70],
        X[70:], y[70:],
        feature_names=feat,
    )
    assert "with_news" in result
    assert "without_news" in result
    assert "news_column_indices" in result
    assert result["n_news_features"] == 2  # news_sentiment, news_count


def test_news_feature_ablation_no_news_columns():
    feat = ["rsi_14", "mom_5d", "vol_21d"]
    rng = np.random.RandomState(0)
    X = rng.randn(80, 3)
    y = X[:, 0] + rng.randn(80) * 0.1
    result = news_feature_ablation(
        ExcessReturnModel,
        X[:60], y[:60],
        X[60:], y[60:],
        feature_names=feat,
    )
    assert result["n_news_features"] == 0
    # Both scores should be equal (same data, no ablation)
    assert result["with_news"] == pytest.approx(result["without_news"], abs=1e-9)


def test_news_ablation_with_classification_model():
    X, y = _xy_classification()
    feat = _feature_names()

    def accuracy(y_true, y_pred):
        return float(np.mean(y_true == y_pred.round()))

    result = news_feature_ablation(
        MarketRiskModel,
        X[:70], y[:70],
        X[70:], y[70:],
        feature_names=feat,
        metric_fn=accuracy,
    )
    assert 0.0 <= result["with_news"] <= 1.0
    assert 0.0 <= result["without_news"] <= 1.0


# ---------------------------------------------------------------------------
# Deterministic fallback models (always available)
# ---------------------------------------------------------------------------

def test_mean_classifier_deterministic():
    from ml.models import _MeanClassifier
    X, y = _xy_classification()
    m = _MeanClassifier()
    m.fit(X, y)
    p1 = m.predict(X)
    p2 = m.predict(X)
    np.testing.assert_array_equal(p1, p2)


def test_mean_regressor_deterministic():
    from ml.models import _MeanRegressor
    X, y = _xy_regression()
    m = _MeanRegressor()
    m.fit(X, y)
    p1 = m.predict(X)
    p2 = m.predict(X)
    np.testing.assert_array_equal(p1, p2)
    assert np.all(p1 == pytest.approx(float(np.mean(y))))
