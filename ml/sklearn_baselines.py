"""p6 — sklearn baseline classifiers and regressors.

Optional dependency: scikit-learn. If not installed, availability check returns False
and fitting raises RuntimeError with a clear message.

Public API
----------
sklearn_available()                  — True if sklearn is installed
LogisticBaseline / RidgeBaseline     — thin wrappers for binary classification / regression
DecisionTreeBaseline                 — thin wrapper for tree classifier/regressor
fit_baseline(model, X, y)            — fit in-place, return model
predict_baseline(model, X)           — 1-D numpy array of predictions
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

try:
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
    _SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SKLEARN_AVAILABLE = False

if TYPE_CHECKING:
    from typing import Any


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def sklearn_available() -> bool:
    """Return True if scikit-learn is importable."""
    return _SKLEARN_AVAILABLE


def _require_sklearn() -> None:
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError(
            "scikit-learn is not installed. "
            "Install it with: pip install scikit-learn"
        )


# ---------------------------------------------------------------------------
# Thin wrappers
# ---------------------------------------------------------------------------

class LogisticBaseline:
    """Binary logistic regression wrapper (classification)."""

    def __init__(self, C: float = 1.0, max_iter: int = 200, random_state: int = 42) -> None:
        _require_sklearn()
        self._model = LogisticRegression(C=C, max_iter=max_iter, random_state=random_state)
        self.is_fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LogisticBaseline":
        self._model.fit(X, y)
        self.is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict_proba(X)


class RidgeBaseline:
    """Ridge regression wrapper (regression / score prediction)."""

    def __init__(self, alpha: float = 1.0, random_state: int = 42) -> None:
        _require_sklearn()
        self._model = Ridge(alpha=alpha, random_state=random_state)
        self.is_fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RidgeBaseline":
        self._model.fit(X, y)
        self.is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict(X)


class DecisionTreeBaseline:
    """Decision tree wrapper for classification or regression."""

    def __init__(
        self,
        task: str = "classification",
        max_depth: int = 4,
        random_state: int = 42,
    ) -> None:
        _require_sklearn()
        if task == "classification":
            self._model = DecisionTreeClassifier(max_depth=max_depth, random_state=random_state)
        else:
            self._model = DecisionTreeRegressor(max_depth=max_depth, random_state=random_state)
        self.task = task
        self.is_fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "DecisionTreeBaseline":
        self._model.fit(X, y)
        self.is_fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict(X)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def fit_baseline(model: "Any", X: np.ndarray, y: np.ndarray) -> "Any":
    """Fit a baseline model in-place and return it."""
    model.fit(X, y)
    return model


def predict_baseline(model: "Any", X: np.ndarray) -> np.ndarray:
    """Return a 1-D array of predictions from any fitted baseline model."""
    return np.asarray(model.predict(X)).ravel()


# ---------------------------------------------------------------------------
# DataFrame-level helpers for train/eval with feature DataFrames
# ---------------------------------------------------------------------------

def make_classification_target(
    close: pd.Series,
    horizon: int = 1,
    threshold: float = 0.0,
) -> pd.Series:
    """Binary target: 1 if forward return over *horizon* days > threshold, else 0.

    The target at row t is the return from close[t] to close[t+horizon],
    aligned so that it is placed on row t *after* computing features — callers
    must shift features forward by horizon or use walk-forward splits to avoid
    lookahead.
    """
    fwd_ret = close.pct_change(horizon).shift(-horizon)
    # Preserve NaN for rows where the forward return is unknown (end of series).
    return (fwd_ret > threshold).astype(float).where(fwd_ret.notna()).rename(f"target_cls_h{horizon}")


def make_regression_target(close: pd.Series, horizon: int = 1) -> pd.Series:
    """Continuous forward-return target (horizon days ahead), aligned to row t."""
    fwd_ret = close.pct_change(horizon).shift(-horizon)
    return fwd_ret.rename(f"target_ret_h{horizon}")


def train_eval_split(
    feature_df: pd.DataFrame,
    target: pd.Series,
    train_frac: float = 0.7,
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """Simple chronological train/eval split.

    Returns ((X_train, y_train), (X_eval, y_eval)).
    Rows with NaN in either features or target are dropped first.
    """
    combined = feature_df.join(target, how="inner").dropna()
    n_train = int(len(combined) * train_frac)
    train = combined.iloc[:n_train]
    eval_ = combined.iloc[n_train:]
    X_tr = train.drop(columns=[target.name]).values
    y_tr = train[target.name].values
    X_ev = eval_.drop(columns=[target.name]).values
    y_ev = eval_[target.name].values
    return (X_tr, y_tr), (X_ev, y_ev)
