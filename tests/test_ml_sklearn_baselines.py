import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.sklearn_baselines import (
    make_classification_target,
    make_regression_target,
    sklearn_available,
    train_eval_split,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _close(n=50):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.Series(np.linspace(100, 130, n), index=idx, name="QQQ")


def _features(close):
    from ml.features import compute_features
    return compute_features(close.to_frame("close"))


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def test_sklearn_available_returns_bool():
    result = sklearn_available()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Target builders
# ---------------------------------------------------------------------------

def test_make_classification_target_shape_and_values():
    close = _close(20)
    target = make_classification_target(close, horizon=1)
    assert len(target) == 20
    assert set(target.dropna().unique()).issubset({0, 1})
    # Last horizon rows should be NaN (no future close to compute return against)
    assert pd.isna(target.iloc[-1])


def test_make_regression_target_returns_floats():
    close = _close(20)
    target = make_regression_target(close, horizon=1)
    assert target.dropna().dtype == float
    assert pd.isna(target.iloc[-1])


# ---------------------------------------------------------------------------
# Train/eval split
# ---------------------------------------------------------------------------

def test_train_eval_split_proportions():
    idx = pd.date_range("2024-01-01", periods=50, freq="D")
    feats = pd.DataFrame({"a": np.arange(50, dtype=float)}, index=idx)
    target = pd.Series(np.arange(50, dtype=float), index=idx, name="y")
    (X_tr, y_tr), (X_ev, y_ev) = train_eval_split(feats, target, train_frac=0.7)
    assert len(X_tr) == 35
    assert len(X_ev) == 15
    assert X_tr.shape[1] == 1


def test_train_eval_split_chronological_order():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    feats = pd.DataFrame({"a": np.arange(10, dtype=float)}, index=idx)
    target = pd.Series(np.arange(10, dtype=float), index=idx, name="y")
    (X_tr, _), (X_ev, _) = train_eval_split(feats, target)
    # All training values should be strictly less than eval values
    assert X_tr[:, 0].max() < X_ev[:, 0].min()


# ---------------------------------------------------------------------------
# sklearn model wrappers (skip if sklearn not installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not sklearn_available(), reason="sklearn not installed")
def test_logistic_baseline_fit_predict():
    from ml.sklearn_baselines import LogisticBaseline
    rng = np.random.RandomState(0)
    X = rng.randn(80, 4)
    y = (X[:, 0] > 0).astype(int)
    model = LogisticBaseline()
    model.fit(X[:60], y[:60])
    preds = model.predict(X[60:])
    assert preds.shape == (20,)
    assert set(preds).issubset({0, 1})
    proba = model.predict_proba(X[60:])
    assert proba.shape == (20, 2)


@pytest.mark.skipif(not sklearn_available(), reason="sklearn not installed")
def test_ridge_baseline_fit_predict():
    from ml.sklearn_baselines import RidgeBaseline
    rng = np.random.RandomState(1)
    X = rng.randn(80, 3)
    y = X[:, 0] * 2 + 0.1 * rng.randn(80)
    model = RidgeBaseline()
    model.fit(X[:60], y[:60])
    preds = model.predict(X[60:])
    assert preds.shape == (20,)
    assert np.all(np.isfinite(preds))


@pytest.mark.skipif(not sklearn_available(), reason="sklearn not installed")
def test_decision_tree_baseline_classification():
    from ml.sklearn_baselines import DecisionTreeBaseline, fit_baseline, predict_baseline
    rng = np.random.RandomState(2)
    X = rng.randn(100, 4)
    y = (X[:, 0] > 0).astype(int)
    model = DecisionTreeBaseline(task="classification")
    fit_baseline(model, X[:70], y[:70])
    preds = predict_baseline(model, X[70:])
    assert preds.shape == (30,)


@pytest.mark.skipif(not sklearn_available(), reason="sklearn not installed")
def test_decision_tree_baseline_regression():
    from ml.sklearn_baselines import DecisionTreeBaseline
    rng = np.random.RandomState(3)
    X = rng.randn(60, 2)
    y = X[:, 0] + rng.randn(60) * 0.1
    model = DecisionTreeBaseline(task="regression")
    model.fit(X[:40], y[:40])
    preds = model.predict(X[40:])
    assert preds.shape == (20,)


def test_runtime_error_when_sklearn_absent(monkeypatch):
    """LogisticBaseline raises RuntimeError when sklearn is unavailable."""
    import ml.sklearn_baselines as skmod
    monkeypatch.setattr(skmod, "_SKLEARN_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="scikit-learn"):
        skmod._require_sklearn()
