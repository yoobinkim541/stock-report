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

from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ml.strategy_studio.contracts import DataSnapshot, ModelProvenance

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


_PROFILE_FRESHNESS_SECONDS = {
    "kr_intraday": 15 * 60,
    "extended_us": 15 * 60,
    "global_swing": 3 * 24 * 60 * 60,
    "bar": 3 * 24 * 60 * 60,
}


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


def predict_with_metadata(
    model: object,
    features: pd.DataFrame,
    metadata: dict[str, object],
    *,
    evaluation_at: object | None = None,
    max_data_age_seconds: float | None = None,
) -> dict[str, object]:
    """Run a registered model only when its prediction contract is complete.

    The wrapper deliberately delegates to the model's public prediction method;
    it never evaluates model code or invents a prediction when metadata or the
    feature schema is unavailable.
    """

    if not isinstance(metadata, dict):
        raise TypeError("prediction metadata must be a dict")
    required = ("model_id", "feature_version", "model_version", "as_of")
    missing = [name for name in required if _missing_prediction_metadata(metadata.get(name))]
    if "confidence" not in metadata:
        missing.append("confidence")
    if missing:
        raise ValueError(f"prediction metadata missing: {', '.join(missing)}")
    if not isinstance(features, pd.DataFrame):
        raise TypeError("model features must be a pandas DataFrame")

    provenance = _model_provenance(metadata)
    provenance_required = bool(metadata.get("require_provenance", False))
    if provenance_required and provenance is None:
        missing_provenance = _missing_model_provenance_fields(metadata)
        raise ValueError(f"model provenance missing: {', '.join(missing_provenance)}")

    expected = metadata.get("feature_names") if _has_metadata_value(metadata.get("feature_names")) else metadata.get("feature_columns")
    if expected is None:
        expected = getattr(model, "feature_names_", None)
    if isinstance(expected, str):
        expected = [expected]
    if expected is not None and list(features.columns) != [str(item) for item in expected]:
        raise ValueError(
            f"feature columns do not match: expected {[str(item) for item in expected]}, "
            f"got {[str(item) for item in features.columns]}"
        )

    adapter = getattr(model, "predict_with_metadata", None)
    raw = adapter(features) if callable(adapter) else model.predict(features)  # type: ignore[attr-defined]
    if isinstance(raw, dict):
        for name in ("model_id", "feature_version", "model_version"):
            supplied = raw.get(name)
            if supplied is not None and str(supplied) != str(metadata[name]):
                raise ValueError(f"{name} mismatch: registered {metadata[name]}, received {supplied}")
        predictions = raw.get("predictions")
        confidence = raw.get("confidence", metadata.get("confidence"))
        as_of = raw.get("as_of", metadata.get("as_of"))
    else:
        predictions = raw
        confidence = metadata.get("confidence")
        as_of = metadata.get("as_of")
    if predictions is None or confidence is None or as_of is None:
        missing_output = [
            name for name, value in (("predictions", predictions), ("confidence", confidence), ("as_of", as_of))
            if value is None
        ]
        raise ValueError(f"prediction metadata missing: {', '.join(missing_output)}")

    warnings: list[str] = []
    freshness = _data_freshness(
        features,
        metadata,
        evaluation_at=evaluation_at,
        max_data_age_seconds=max_data_age_seconds,
    )
    warnings.extend(freshness["warnings"])
    if freshness["status"] == "stale":
        # Preserve predictions for audit/explanation, but make them unusable
        # to allocation.  Never substitute a newer row from the same frame.
        confidence = _zero_confidence(confidence)

    result: dict[str, object] = {
        "model_id": str(metadata["model_id"]),
        "feature_version": str(metadata["feature_version"]),
        "model_version": str(metadata["model_version"]),
        "predictions": predictions,
        "confidence": confidence,
        "as_of": as_of,
        "freshness": freshness,
        "warnings": list(dict.fromkeys(warnings)),
        "diagnostics": list(dict.fromkeys(warnings)),
    }
    if provenance is not None:
        result["provenance"] = provenance.to_provenance()
    return result


def _model_provenance(metadata: Mapping[str, object]) -> ModelProvenance | None:
    """Build complete model provenance, returning ``None`` for legacy entries."""

    payload: dict[str, object] = dict(metadata)
    nested = payload.get("provenance")
    if isinstance(nested, Mapping):
        payload = {**dict(nested), **payload}
    required = _missing_model_provenance_fields(payload)
    if required:
        return None
    try:
        feature_names = payload.get("feature_names") if _has_metadata_value(payload.get("feature_names")) else payload.get("feature_columns") or ()
        profiles = payload.get("profiles") if _has_metadata_value(payload.get("profiles")) else payload.get("supported_profiles") or ()
        return ModelProvenance(
            model_id=str(payload["model_id"]),
            feature_version=str(payload["feature_version"]),
            train_start=str(payload["train_start"]),
            train_end=str(payload["train_end"]),
            code_commit=str(payload["code_commit"]),
            seed=payload["seed"],  # type: ignore[arg-type]
            metrics=payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {},
            model_version=str(payload.get("model_version") or ""),
            feature_names=feature_names,  # type: ignore[arg-type]
            profiles=profiles,  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        return None


def _missing_model_provenance_fields(metadata: Mapping[str, object]) -> list[str]:
    required = ("model_id", "feature_version", "model_version", "feature_names", "train_start", "train_end", "code_commit", "seed")
    missing: list[str] = []
    for name in required:
        value = metadata.get(name)
        if name == "feature_names" and not _has_metadata_value(value):
            value = metadata.get("feature_columns")
        if not _has_metadata_value(value) or (name == "feature_names" and not _has_metadata_value(value)):
            missing.append(name)
    return missing


def _data_freshness(
    features: pd.DataFrame,
    metadata: Mapping[str, object],
    *,
    evaluation_at: object | None,
    max_data_age_seconds: float | None,
) -> dict[str, object]:
    snapshot = features.attrs.get("data_snapshot") if isinstance(features.attrs, Mapping) else None
    snapshot = snapshot if isinstance(snapshot, DataSnapshot) else DataSnapshot.from_dict(snapshot) if isinstance(snapshot, Mapping) and "data_stamps" in snapshot else None
    source_metadata = metadata.get("data") if isinstance(metadata.get("data"), Mapping) else {}
    data_as_of = (
        metadata.get("data_available_at") or metadata.get("data_received_at") or metadata.get("data_as_of")
        or source_metadata.get("available_at") or source_metadata.get("received_at") or source_metadata.get("as_of")
    )
    if data_as_of is None and snapshot is not None:
        data_as_of = snapshot.latest_transport_at
    evaluated = evaluation_at or metadata.get("evaluation_at")
    limit_value = max_data_age_seconds
    if limit_value is None:
        for key in ("max_data_age_seconds", "freshness_limit_seconds", "data_max_age_seconds", "profile_freshness_seconds"):
            if metadata.get(key) is not None:
                limit_value = metadata.get(key)  # type: ignore[assignment]
                break
    if limit_value is None:
        profile = metadata.get("data_profile") or metadata.get("profile")
        if isinstance(profile, Mapping):
            limit_value = profile.get("freshness_seconds") or profile.get("max_data_age_seconds")
        elif profile:
            limit_value = _PROFILE_FRESHNESS_SECONDS.get(str(profile).strip().lower())
    warnings: list[str] = []
    data_timestamp = _metadata_timestamp(data_as_of)
    evaluation_timestamp = _metadata_timestamp(evaluated)
    if snapshot is not None:
        warnings.extend(snapshot.warnings or [])
    try:
        limit = None if limit_value is None else float(limit_value)
    except (TypeError, ValueError):
        limit = None
        warnings.append("freshness_limit_invalid")
    if limit is not None and (not np.isfinite(limit) or limit < 0):
        limit = None
        warnings.append("freshness_limit_invalid")
    if data_timestamp is None:
        warnings.append("freshness_timestamp_missing")
        status = "unknown"
        age = None
    elif evaluation_timestamp is None:
        warnings.append("freshness_evaluation_missing")
        status = "unknown"
        age = None
    else:
        age = (evaluation_timestamp - data_timestamp).total_seconds()
        if age < 0:
            warnings.append("future_timestamp")
            status = "invalid"
        elif limit is None:
            warnings.append("freshness_limit_missing")
            status = "unknown"
        elif age > limit:
            warnings.append("data_stale")
            status = "stale"
        else:
            status = "fresh"
    snapshot_status = (
        (snapshot.freshness or {}).get("status") if snapshot is not None else None
    ) or (snapshot.quality if snapshot is not None else "")
    if snapshot_status in {"stale", "expired"}:
        warnings.append("data_stale")
        status = "stale"
    elif snapshot_status in {"invalid", "missing"}:
        warnings.append(f"data_quality_{snapshot_status}")
        status = "invalid" if snapshot_status == "invalid" else "unknown"
    return {
        "status": status,
        "data_as_of": data_timestamp.isoformat() if data_timestamp is not None else None,
        "evaluation_at": evaluation_timestamp.isoformat() if evaluation_timestamp is not None else None,
        "age_seconds": age,
        "max_age_seconds": limit,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _metadata_timestamp(value: object) -> pd.Timestamp | None:
    if isinstance(value, (pd.Series, pd.Index, np.ndarray, list, tuple)):
        values = [_metadata_timestamp(item) for item in list(value)]
        values = [item for item in values if item is not None]
        return max(values) if values else None
    if value is None:
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    try:
        if bool(pd.isna(parsed)):
            return None
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _has_metadata_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and bool(missing):
            return False
    except (TypeError, ValueError):
        pass
    try:
        return len(value) > 0  # type: ignore[arg-type]
    except TypeError:
        return True


def _zero_confidence(value: object) -> object:
    if isinstance(value, pd.Series):
        return pd.Series(0.0, index=value.index, name=value.name)
    if isinstance(value, pd.DataFrame):
        return pd.DataFrame(0.0, index=value.index, columns=value.columns)
    if isinstance(value, np.ndarray):
        return np.zeros(value.shape, dtype="float64")
    if isinstance(value, tuple):
        return tuple(0.0 for _ in value)
    if isinstance(value, list):
        return [0.0 for _ in value]
    return 0.0


def _missing_prediction_metadata(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return len(value) == 0  # type: ignore[arg-type]
    except TypeError:
        return False


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
