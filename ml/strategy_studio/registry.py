"""Deterministic registries for strategy signal providers and model adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .contracts import ModelProvenance

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

    @property
    def provenance(self) -> ModelProvenance | None:
        """Return complete provenance, or ``None`` for a legacy registration."""

        return build_model_provenance(self.model_id, self.metadata, model=self.model)

    @property
    def provenance_warnings(self) -> list[str]:
        missing = model_provenance_missing_fields(
            {**self.metadata, "model_id": self.model_id}, model=self.model
        )
        return [f"model provenance missing: {name}" for name in missing]

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "model_id": self.model_id,
            "metadata": deepcopy(self.metadata),
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
            "warnings": list(self.provenance_warnings),
        }
        return payload


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


def model_provenance_missing_fields(metadata: dict[str, object], *, model: object | None = None) -> list[str]:
    """List required provenance fields without rejecting legacy registrations."""

    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a dict")
    payload = dict(metadata)
    nested = payload.get("provenance")
    if isinstance(nested, dict):
        payload = {**nested, **payload}
    if not _has_metadata_value(payload.get("feature_names")) and not _has_metadata_value(payload.get("feature_columns")) and model is not None:
        model_features = getattr(model, "feature_names_", None)
        if _has_metadata_value(model_features):
            payload["feature_names"] = model_features
    required = (
        "model_id", "feature_version", "model_version", "feature_names",
        "train_start", "train_end", "code_commit", "seed",
    )
    missing: list[str] = []
    for field_name in required:
        value = payload.get(field_name)
        if field_name == "feature_names" and not _has_metadata_value(value):
            value = payload.get("feature_columns")
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field_name)
        elif field_name == "feature_names" and not _has_metadata_value(value):
            missing.append(field_name)
    return missing


def build_model_provenance(
    model_id: str,
    metadata: dict[str, object],
    *,
    model: object | None = None,
) -> ModelProvenance | None:
    """Build provenance for a registration, preserving incomplete legacy rows."""

    if not isinstance(metadata, dict):
        raise TypeError("metadata must be a dict")
    payload = dict(metadata)
    nested = payload.get("provenance")
    if isinstance(nested, dict):
        payload = {**nested, **payload}
    payload["model_id"] = str(model_id or payload.get("model_id") or "")
    if not _has_metadata_value(payload.get("feature_names")) and not _has_metadata_value(payload.get("feature_columns")) and model is not None:
        model_features = getattr(model, "feature_names_", None)
        if _has_metadata_value(model_features):
            payload["feature_names"] = model_features
    if model_provenance_missing_fields(payload):
        return None
    try:
        feature_names = payload.get("feature_names") if _has_metadata_value(payload.get("feature_names")) else payload.get("feature_columns") or ()
        profiles = payload.get("profiles") if _has_metadata_value(payload.get("profiles")) else payload.get("supported_profiles") or ()
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        return ModelProvenance(
            model_id=payload["model_id"],  # type: ignore[arg-type]
            feature_version=payload["feature_version"],  # type: ignore[arg-type]
            train_start=payload["train_start"],  # type: ignore[arg-type]
            train_end=payload["train_end"],  # type: ignore[arg-type]
            code_commit=payload["code_commit"],  # type: ignore[arg-type]
            seed=payload["seed"],  # type: ignore[arg-type]
            metrics=metrics,
            model_version=str(payload.get("model_version") or ""),
            feature_names=feature_names,  # type: ignore[arg-type]
            profiles=profiles,  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        return None


def get_model_provenance(model_id: str) -> ModelProvenance | None:
    """Return validated provenance for a model registration when complete."""

    registered = get_model(model_id)
    return registered.provenance if registered is not None else None


def _has_metadata_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        return len(value) > 0  # type: ignore[arg-type]
    except TypeError:
        return True


def _normalise_name(value: object, field_name: str) -> str:
    name = str(value or "").strip().lower()
    if not name:
        raise ValueError(f"{field_name} is required")
    return name
