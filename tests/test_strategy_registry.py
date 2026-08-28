from __future__ import annotations

import pytest

from ml.strategy_studio.registry import (
    RegisteredModel,
    get_model,
    get_signal_provider,
    register_model,
    register_signal_provider,
)


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
