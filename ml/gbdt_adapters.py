"""Small GBDT adapter layer for ranker experiments.

The production ranker keeps LightGBM as the default champion.  XGBoost is
treated as an optional challenger: if the package is installed we can train and
score it on the same windows, otherwise callers get a clean unavailable result.
"""
from __future__ import annotations

import importlib.util
from typing import Iterable, Sequence

import numpy as np
import pandas as pd


class BackendUnavailable(RuntimeError):
    """Raised when a requested backend package is not installed."""


_ALIASES = {
    "lgb": "lightgbm",
    "lgbm": "lightgbm",
    "lightgbm": "lightgbm",
    "xgb": "xgboost",
    "xgboost": "xgboost",
    "sklearn": "sklearn",
    "scikit": "sklearn",
    "scikit-learn": "sklearn",
}

_MODULE_BY_BACKEND = {
    "lightgbm": "lightgbm",
    "xgboost": "xgboost",
    "sklearn": "sklearn",
}

_LABELS = {
    "lightgbm": "LightGBM",
    "xgboost": "XGBoost",
    "sklearn": "scikit-learn",
}


def normalize_backend(backend: str | None) -> str:
    """Return canonical backend name."""
    if backend is None:
        return "lightgbm"
    key = str(backend).strip().lower()
    if key not in _ALIASES:
        allowed = ", ".join(sorted(set(_ALIASES.values())))
        raise ValueError(f"unknown GBDT backend: {backend!r} (allowed: {allowed})")
    return _ALIASES[key]


def backend_label(backend: str | None) -> str:
    """Human-facing backend label."""
    try:
        return _LABELS.get(normalize_backend(backend), str(backend or "unknown"))
    except ValueError:
        return str(backend or "unknown")


def backend_available(backend: str | None) -> bool:
    """Return True when the backend package can be imported."""
    name = normalize_backend(backend)
    module = _MODULE_BY_BACKEND[name]
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def available_backends(candidates: Iterable[str] = ("lightgbm", "xgboost", "sklearn")) -> tuple[str, ...]:
    """Return installed canonical backends from candidates."""
    out: list[str] = []
    for backend in candidates:
        name = normalize_backend(backend)
        if name not in out and backend_available(name):
            out.append(name)
    return tuple(out)


def _require_backend(backend: str) -> None:
    if not backend_available(backend):
        raise BackendUnavailable(f"{backend_label(backend)} is not installed")


def make_ranker(backend: str = "lightgbm", random_state: int = 42, **overrides):
    """Create a LambdaRank/pairwise ranker for date-grouped cross-sectional data."""
    name = normalize_backend(backend)
    _require_backend(name)

    if name == "lightgbm":
        import lightgbm as lgb

        params = {
            "objective": "lambdarank",
            "n_estimators": 200,
            "num_leaves": 31,
            "learning_rate": 0.05,
            "min_child_samples": 5,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 0.1,
            "random_state": random_state,
            "verbose": -1,
            "n_jobs": -1,
        }
        params.update(overrides)
        return lgb.LGBMRanker(**params)

    if name == "xgboost":
        from xgboost import XGBRanker

        params = {
            "objective": "rank:pairwise",
            "n_estimators": 200,
            "max_depth": 4,
            "learning_rate": 0.05,
            "min_child_weight": 5,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "random_state": random_state,
            "n_jobs": -1,
            "tree_method": "hist",
            "verbosity": 0,
        }
        params.update(overrides)
        return XGBRanker(**params)

    raise BackendUnavailable(f"{backend_label(name)} does not provide a ranker adapter")


def make_regressor(backend: str = "lightgbm", random_state: int = 42, **overrides):
    """Create a regressor backend for excess-return prediction."""
    name = normalize_backend(backend)
    _require_backend(name)

    if name == "lightgbm":
        import lightgbm as lgb

        params = {
            "objective": "regression",
            "n_estimators": 200,
            "num_leaves": 31,
            "learning_rate": 0.05,
            "min_child_samples": 20,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 0.1,
            "random_state": random_state,
            "verbose": -1,
            "n_jobs": -1,
        }
        params.update(overrides)
        return lgb.LGBMRegressor(**params)

    if name == "xgboost":
        from xgboost import XGBRegressor

        params = {
            "objective": "reg:squarederror",
            "n_estimators": 200,
            "max_depth": 4,
            "learning_rate": 0.05,
            "min_child_weight": 20,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "random_state": random_state,
            "n_jobs": -1,
            "tree_method": "hist",
            "verbosity": 0,
        }
        params.update(overrides)
        return XGBRegressor(**params)

    if name == "sklearn":
        from sklearn.linear_model import Ridge

        params = {"alpha": 1.0}
        params.update(overrides)
        return Ridge(**params)

    raise BackendUnavailable(f"{backend_label(name)} does not provide a regressor adapter")


def fit_ranker_model(
    model,
    X: np.ndarray,
    labels: np.ndarray,
    groups: Sequence[int],
    feature_names: Sequence[str],
    backend: str,
):
    """Fit a ranker while hiding backend-specific fit keyword differences."""
    name = normalize_backend(backend)
    if name == "lightgbm":
        return model.fit(X, labels, group=list(groups), feature_name=list(feature_names))
    if name == "xgboost":
        try:
            return model.fit(X, labels, group=list(groups), verbose=False)
        except TypeError:
            return model.fit(X, labels, group=list(groups))
    return model.fit(X, labels)


def fit_regressor_model(
    model,
    X: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
    backend: str,
):
    """Fit a regressor while keeping LightGBM feature names when supported."""
    name = normalize_backend(backend)
    if name == "lightgbm":
        try:
            return model.fit(X, y, feature_name=list(feature_names))
        except TypeError:
            return model.fit(X, y)
    return model.fit(X, y)


def feature_importance(model, feature_names: Sequence[str]) -> pd.Series:
    """Return sorted feature importance with a zero fallback."""
    names = list(feature_names)
    values = getattr(model, "feature_importances_", None)
    if values is None and hasattr(model, "coef_"):
        values = np.abs(np.asarray(model.coef_)).ravel()
    if values is None:
        values = np.zeros(len(names), dtype=float)
    values = np.asarray(values, dtype=float).ravel()
    if len(values) != len(names):
        values = np.resize(values, len(names)) if len(values) else np.zeros(len(names), dtype=float)
    return pd.Series(values, index=names, name="importance").sort_values(ascending=False)


def is_ranker_model(model) -> bool:
    """Return True for model classes whose output scale is ranker-specific."""
    return type(model).__name__ in {"LGBMRanker", "XGBRanker"}


def model_backend_name(model) -> str:
    """Best-effort backend inference for older cached RankerResult objects."""
    module = getattr(type(model), "__module__", "")
    cls = type(model).__name__.lower()
    if "lightgbm" in module or cls.startswith("lgbm"):
        return "lightgbm"
    if "xgboost" in module or cls.startswith("xgb"):
        return "xgboost"
    if "sklearn" in module:
        return "sklearn"
    return "unknown"
