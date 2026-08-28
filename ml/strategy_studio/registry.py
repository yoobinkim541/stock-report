"""Deterministic registries for strategy signal providers and model adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import pandas as pd

    from .spec import StrategySpec
    from .engine import CompiledStrategy
    from .signals import SignalPanel


class SignalProvider(Protocol):
    """Callable contract implemented by a registered signal provider."""

    def __call__(self, strategy: "StrategySpec", compiled: "CompiledStrategy") -> "SignalPanel":
        ...


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    """A model object together with immutable-at-registration metadata."""

    model_id: str
    model: object
    metadata: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _normalise_name(self.model_id, "model_id"))
        if self.model is None:
            raise ValueError("model is required")
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict")
        object.__setattr__(self, "metadata", deepcopy(self.metadata))


_SIGNAL_PROVIDERS: dict[str, SignalProvider] = {}
_MODELS: dict[str, RegisteredModel] = {}


def register_signal_provider(name: str, provider: SignalProvider) -> None:
    """Register or replace a named, deterministic provider."""

    normalised = _normalise_name(name, "provider name")
    if not callable(provider):
        raise TypeError("signal provider must be callable")
    _SIGNAL_PROVIDERS[normalised] = provider


def get_signal_provider(name: str) -> SignalProvider:
    """Return a provider or raise a diagnostic lookup error."""

    normalised = _normalise_name(name, "provider name")
    try:
        return _SIGNAL_PROVIDERS[normalised]
    except KeyError as exc:
        raise LookupError(f"signal provider not registered: {normalised}") from exc


def register_model(model_id: str, model: object, metadata: dict[str, object]) -> None:
    """Register a model object without invoking or executing it."""

    registered = RegisteredModel(model_id, model, metadata)
    _MODELS[registered.model_id] = registered


def get_model(model_id: str) -> RegisteredModel | None:
    """Return a registered model, or ``None`` when it is absent."""

    normalised = _normalise_name(model_id, "model_id")
    return _MODELS.get(normalised)


def _normalise_name(value: object, field_name: str) -> str:
    name = str(value or "").strip().lower()
    if not name:
        raise ValueError(f"{field_name} is required")
    return name
