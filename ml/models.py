"""p7 — Model adapters for market risk score and excess-return ranking.

Priority of adapters (first available wins):
  1. LightGBM (if installed)
  2. sklearn DecisionTree/Ridge (if installed)
  3. Mean/rank baseline (always available)

Public API
----------
lightgbm_available()               — True if lightgbm is importable
MarketRiskModel                    — wraps LightGBM classifier/regressor for risk score
ExcessReturnModel                  — wraps LightGBM regressor for ranking
news_feature_ablation(model, ...)  — train/eval with and without news features
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
    _LGB_AVAILABLE = True
except ImportError:
    _LGB_AVAILABLE = False

try:
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
    from sklearn.linear_model import Ridge
    _SKLEARN_AVAILABLE = True
except ImportError:
    _SKLEARN_AVAILABLE = False


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def lightgbm_available() -> bool:
    """Return True if lightgbm is importable."""
    return _LGB_AVAILABLE


# ---------------------------------------------------------------------------
# LightGBM hyper-parameter defaults (minimal to keep tests fast)
# ---------------------------------------------------------------------------

_RISK_PARAMS: dict = {
    "objective": "binary",
    "n_estimators": 50,
    "num_leaves": 15,
    "learning_rate": 0.1,
    "random_state": 42,
    "verbose": -1,
    "n_jobs": 1,
}

_RETURN_PARAMS: dict = {
    "objective": "regression",
    "n_estimators": 50,
    "num_leaves": 15,
    "learning_rate": 0.1,
    "random_state": 42,
    "verbose": -1,
    "n_jobs": 1,
}


# ---------------------------------------------------------------------------
# Internal: pick best available model class
# ---------------------------------------------------------------------------

def _make_classifier(random_state: int = 42) -> "object":
    if _LGB_AVAILABLE:
        return lgb.LGBMClassifier(**{**_RISK_PARAMS, "random_state": random_state})
    if _SKLEARN_AVAILABLE:
        return DecisionTreeClassifier(max_depth=4, random_state=random_state)
    return _MeanClassifier()


def _make_regressor(random_state: int = 42) -> "object":
    if _LGB_AVAILABLE:
        return lgb.LGBMRegressor(**{**_RETURN_PARAMS, "random_state": random_state})
    if _SKLEARN_AVAILABLE:
        return Ridge(alpha=1.0, random_state=random_state)
    return _MeanRegressor()


# ---------------------------------------------------------------------------
# Deterministic fallbacks (no dependencies)
# ---------------------------------------------------------------------------

class _MeanClassifier:
    """Always predicts the majority class seen during training."""

    def __init__(self) -> None:
        self._majority: int = 0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_MeanClassifier":
        values, counts = np.unique(y, return_counts=True)
        self._majority = int(values[np.argmax(counts)])
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), self._majority, dtype=int)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        n = len(X)
        proba = np.zeros((n, 2))
        proba[:, self._majority] = 1.0
        return proba


class _MeanRegressor:
    """Always predicts the mean target seen during training."""

    def __init__(self) -> None:
        self._mean: float = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_MeanRegressor":
        self._mean = float(np.mean(y))
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), self._mean)


# ---------------------------------------------------------------------------
# Public model adapters
# ---------------------------------------------------------------------------

class MarketRiskModel:
    """Risk score model: binary (high-risk / low-risk) or continuous risk score.

    Uses LightGBM if available, sklearn tree if available, else mean baseline.
    """

    def __init__(self, task: str = "classification", random_state: int = 42) -> None:
        if task not in ("classification", "regression"):
            raise ValueError("task must be 'classification' or 'regression'")
        self.task = task
        self._model = _make_classifier(random_state) if task == "classification" else _make_regressor(random_state)
        self.feature_names_: Optional[list[str]] = None
        self.is_fitted: bool = False

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: Optional[list[str]] = None) -> "MarketRiskModel":
        self._model.fit(X, y)
        self.feature_names_ = list(feature_names) if feature_names is not None else None
        self.is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.predict(X)).ravel()

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if hasattr(self._model, "predict_proba"):
            return self._model.predict_proba(X)
        scores = self.predict(X)
        proba = np.stack([1 - scores, scores], axis=1)
        return proba

    @property
    def backend(self) -> str:
        if _LGB_AVAILABLE:
            return "lightgbm"
        if _SKLEARN_AVAILABLE:
            return "sklearn"
        return "mean_baseline"


class ExcessReturnModel:
    """Excess-return / ranking model: predicts forward excess returns for ranking.

    Uses LightGBM regressor if available, Ridge if available, else mean baseline.
    """

    def __init__(self, random_state: int = 42) -> None:
        self._model = _make_regressor(random_state)
        self.feature_names_: Optional[list[str]] = None
        self.is_fitted: bool = False

    def fit(self, X: np.ndarray, y: np.ndarray, feature_names: Optional[list[str]] = None) -> "ExcessReturnModel":
        self._model.fit(X, y)
        self.feature_names_ = list(feature_names) if feature_names is not None else None
        self.is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self._model.predict(X)).ravel()

    def rank_scores(self, X: np.ndarray) -> np.ndarray:
        """Return rank indices (0=best) sorted by predicted excess return descending."""
        scores = self.predict(X)
        return np.argsort(-scores)

    @property
    def backend(self) -> str:
        if _LGB_AVAILABLE:
            return "lightgbm"
        if _SKLEARN_AVAILABLE:
            return "sklearn"
        return "mean_baseline"


# ---------------------------------------------------------------------------
# News feature ablation
# ---------------------------------------------------------------------------

_NEWS_PREFIXES = ("news_", "sentiment_", "theme_", "event_")


def _news_columns(feature_names: Sequence[str]) -> list[int]:
    """Return column indices matching news/sentiment/theme/event prefixes."""
    return [
        i for i, name in enumerate(feature_names)
        if any(name.startswith(p) for p in _NEWS_PREFIXES)
    ]


def news_feature_ablation(
    model_cls: type,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_eval: np.ndarray,
    y_eval: np.ndarray,
    feature_names: list[str],
    metric_fn: Optional["callable"] = None,
) -> dict:
    """Train and evaluate model with and without news features.

    Args:
        model_cls: Class of the model (MarketRiskModel or ExcessReturnModel).
        X_train, y_train: Training data.
        X_eval, y_eval: Evaluation data.
        feature_names: Column names for X.
        metric_fn: Scoring function (y_true, y_pred) → float.
                   Defaults to accuracy for classifiers, MSE for regressors.

    Returns:
        dict with keys 'with_news', 'without_news', 'news_column_indices'.
    """
    news_idx = _news_columns(feature_names)
    non_news_idx = [i for i in range(len(feature_names)) if i not in news_idx]

    def _default_metric(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        return float(np.mean((y_true - y_pred) ** 2))

    score_fn = metric_fn if metric_fn is not None else _default_metric

    # With all features
    m_all = model_cls()
    m_all.fit(X_train, y_train, feature_names=feature_names)
    pred_all = m_all.predict(X_eval)
    score_all = score_fn(y_eval, pred_all)

    # Without news features
    if non_news_idx:
        X_tr_no = X_train[:, non_news_idx]
        X_ev_no = X_eval[:, non_news_idx]
        feat_no = [feature_names[i] for i in non_news_idx]
    else:
        X_tr_no, X_ev_no, feat_no = X_train, X_eval, feature_names

    m_no = model_cls()
    m_no.fit(X_tr_no, y_train, feature_names=feat_no)
    pred_no = m_no.predict(X_ev_no)
    score_no = score_fn(y_eval, pred_no)

    return {
        "with_news": score_all,
        "without_news": score_no,
        "news_column_indices": news_idx,
        "n_news_features": len(news_idx),
    }
