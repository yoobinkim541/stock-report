from __future__ import annotations

import pytest

from ml.strategy_studio.registry import (
    RegisteredModel,
    get_model,
    get_signal_provider,
    register_model,
    register_signal_provider,
    get_model_provenance,
)
from ml.models import predict_with_metadata
from ml.strategy_studio.contracts import ModelProvenance

import pandas as pd


def test_signal_registry_returns_registered_provider():
    provider = lambda strategy, compiled: None

    register_signal_provider("task-2-test-provider", provider)

    assert get_signal_provider("task-2-test-provider") is provider


def test_signal_registry_unknown_provider_has_diagnostic():
    with pytest.raises(LookupError, match="signal provider not registered: task-2-missing"):
        get_signal_provider("task-2-missing")


def test_model_registry_preserves_model_and_metadata():
    model = object()
    metadata = {
        "model_id": "task-2-model",
        "feature_version": "features-2026-08-28",
        "model_version": "task-2-model-v1",
        "as_of": "2026-08-27T00:00:00+00:00",
    }

    register_model("task-2-model", model, metadata)
    registered = get_model("task-2-model")

    assert isinstance(registered, RegisteredModel)
    assert registered is not None
    assert registered.model is model
    assert registered.metadata == metadata
    assert get_model("task-2-missing") is None


def test_model_provenance_rejects_missing_required_training_metadata():
    with pytest.raises(ValueError, match="train_end"):
        ModelProvenance(
            model_id="model-v1",
            feature_version="features-v1",
            train_start="2025-01-01T00:00:00Z",
            train_end="",
            code_commit="abc123",
            seed=42,
            metrics={},
        )


def test_model_prediction_uses_stale_fallback_with_visible_warning():
    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    features = pd.DataFrame({"close": [100.0, 101.0]})
    result = predict_with_metadata(
        FixedModel(),
        features,
        {
            "model_id": "stale-model",
            "feature_version": "features-v1",
            "model_version": "stale-v1",
            "as_of": "2026-08-27T00:00:00Z",
            "confidence": [0.9, 0.9],
            "feature_names": ["close"],
            "train_start": "2025-01-01T00:00:00Z",
            "train_end": "2026-08-27T00:00:00Z",
            "code_commit": "abc123",
            "seed": 42,
            "data_as_of": "2026-08-28T09:00:00Z",
            "evaluation_at": "2026-08-28T10:00:00Z",
            "max_data_age_seconds": 60,
        },
    )

    assert result["confidence"] == [0.0, 0.0]
    assert "data_stale" in result["warnings"]


def test_model_registry_exposes_complete_provenance_for_validation():
    model = object()
    register_model(
        "complete-provenance-model",
        model,
        {
            "model_id": "complete-provenance-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "feature_names": ["close"],
            "train_start": "2025-01-01T00:00:00Z",
            "train_end": "2026-08-27T00:00:00Z",
            "code_commit": "abc123",
            "seed": 42,
            "metrics": {"sharpe": 1.2},
        },
    )

    provenance = get_model_provenance("complete-provenance-model")

    assert provenance is not None
    assert provenance.train_end == "2026-08-27T00:00:00+00:00"
    assert provenance.to_provenance()["model"]["model_version"] == "model-v1"
    assert provenance.to_provenance()["model"]["provenance_status"] == "complete"


def test_model_registry_surfaces_malformed_provenance_without_claiming_complete():
    registered = RegisteredModel(
        "malformed-provenance-model",
        object(),
        {
            "model_id": "malformed-provenance-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "feature_names": ["close"],
            "train_start": "not-a-timestamp",
            "train_end": "2026-08-27T00:00:00Z",
            "code_commit": "abc123",
            "seed": 42,
        },
    )

    payload = registered.to_dict()
    assert registered.provenance_status == "incomplete"
    assert payload["provenance_status"] == "incomplete"
    assert payload["provenance"] is None
    assert any("invalid" in warning for warning in payload["warnings"])


def test_prediction_warns_when_training_as_of_is_after_prediction_timestamp():
    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    features = pd.DataFrame(
        {"close": [100.0, 101.0]},
        index=pd.date_range("2026-01-01", periods=2, tz="UTC"),
    )
    result = predict_with_metadata(
        FixedModel(),
        features,
        {
            "model_id": "chronology-warning-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "as_of": "2026-01-03T00:00:00Z",
            "confidence": [0.9, 0.9],
            "feature_names": ["close"],
        },
    )

    assert "prediction_as_of_after_timestamp" in result["warnings"]


def test_prediction_freshness_is_unknown_without_evaluation_timestamp_for_snapshot():
    from ml.data_pipeline import normalize_data_snapshot

    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    features = pd.DataFrame(
        {"close": [100.0]},
        index=pd.date_range("2026-01-01", periods=1, tz="UTC"),
    )
    features.attrs["data_snapshots"] = {
        "A": normalize_data_snapshot(
            features,
            symbol="A",
            source="test-source",
            timeframe="1d",
            session="regular",
            adjustment="raw",
            received_at="2026-01-01T00:00:01Z",
            available_at="2026-01-01T00:00:02Z",
        ).to_dict(),
    }
    result = predict_with_metadata(
        FixedModel(),
        features,
        {
            "model_id": "unknown-freshness-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "as_of": "2025-12-31T00:00:00Z",
            "confidence": [0.9],
            "feature_names": ["close"],
            "max_data_age_seconds": 60,
        },
    )

    assert result["freshness"]["status"] == "unknown"
    assert "freshness_evaluation_missing" in result["warnings"]


def test_default_prediction_returns_explicit_incomplete_provenance_status():
    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    result = predict_with_metadata(
        FixedModel(),
        pd.DataFrame({"close": [100.0]}),
        {
            "model_id": "legacy-diagnostic-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "as_of": "2025-12-31T00:00:00Z",
            "confidence": [0.9],
            "feature_names": ["close"],
            "require_provenance": False,
        },
    )

    assert result["provenance_status"] == "incomplete"
    assert result["provenance"]["model"]["status"] == "incomplete"
    assert result["provenance"]["model"]["provenance_status"] == "incomplete"
    assert any("model_provenance_missing:train_start" == warning for warning in result["warnings"])


def test_default_prediction_does_not_mask_explicit_incomplete_provenance_status():
    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    result = predict_with_metadata(
        FixedModel(),
        pd.DataFrame({"close": [100.0]}),
        {
            "model_id": "malformed-status-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "as_of": "2025-12-31T00:00:00Z",
            "confidence": [0.9],
            "feature_names": ["close"],
            "train_start": "2025-01-01T00:00:00Z",
            "train_end": "2025-12-30T00:00:00Z",
            "code_commit": "abc123",
            "seed": 42,
            "provenance": {"provenance_status": "incomplete"},
        },
    )

    assert result["provenance_status"] == "incomplete"
    assert result["provenance"]["model"]["provenance_status"] == "incomplete"
    assert "model_provenance_incomplete" in result["warnings"]


def test_model_registry_does_not_mask_explicit_incomplete_provenance_status():
    registered = RegisteredModel(
        "registered-incomplete-status-model",
        object(),
        {
            "model_id": "registered-incomplete-status-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "feature_names": ["close"],
            "train_start": "2025-01-01T00:00:00Z",
            "train_end": "2025-12-30T00:00:00Z",
            "code_commit": "abc123",
            "seed": 42,
            "provenance": {"provenance_status": "incomplete"},
        },
    )

    payload = registered.to_dict()

    assert registered.provenance is None
    assert registered.provenance_status == "incomplete"
    assert payload["provenance_status"] == "incomplete"
    assert "model provenance incomplete" in payload["warnings"]
