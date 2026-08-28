from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timezone
import hashlib
import json
from math import isfinite
from typing import Any, Mapping, Sequence


_EVENT_TYPES = {
    "data": "DataStamp",
    "datastamp": "DataStamp",
    "data_stamp": "DataStamp",
    "snapshot": "DataSnapshot",
    "datasnapshot": "DataSnapshot",
    "data_snapshot": "DataSnapshot",
    "model": "ModelProvenance",
    "modelprovenance": "ModelProvenance",
    "model_provenance": "ModelProvenance",
    "signal": "SignalOutput",
    "signaloutput": "SignalOutput",
    "signal_output": "SignalOutput",
    "order": "OrderIntent",
    "orderintent": "OrderIntent",
    "order_intent": "OrderIntent",
    "fill": "FillEvent",
    "fillevent": "FillEvent",
    "fill_event": "FillEvent",
    "position": "PositionState",
    "positionstate": "PositionState",
    "position_state": "PositionState",
}


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object, field_name: str) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalise_timestamp(value: object, field_name: str, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return None

    if isinstance(value, datetime):
        parsed = value
    elif hasattr(value, "to_pydatetime"):
        parsed = value.to_pydatetime()
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            if required:
                raise ValueError(f"{field_name} is required")
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    else:
        raise TypeError(f"{field_name} must be a datetime or ISO timestamp")

    # pandas.NaT exposes the same conversion method as a Timestamp, but it is
    # not a real datetime and must not become the literal string ``NaT``.
    if not isinstance(parsed, datetime):
        raise ValueError(f"{field_name} must be a valid ISO timestamp")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _nonnegative_number(value: object, field_name: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    if number < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return number


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _bounded_number(value: object, field_name: str, lower: float, upper: float) -> float:
    number = _number(value, field_name)
    if not lower <= number <= upper:
        raise ValueError(f"{field_name} must be between {lower} and {upper}")
    return number


def _mapping_copy(value: object, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a dict")
    return deepcopy(dict(value))


@dataclass(frozen=True, slots=True)
class DataStamp:
    symbol: str
    timestamp: str
    source: str
    timeframe: str
    quality: str
    session: str = "regular"
    adjustment: str = "raw"
    received_at: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    metadata: dict[str, Any] | None = None
    available_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        object.__setattr__(self, "timestamp", _normalise_timestamp(self.timestamp, "timestamp"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "timeframe", _required_text(self.timeframe, "timeframe"))
        object.__setattr__(self, "quality", _required_text(self.quality, "quality"))
        object.__setattr__(self, "session", _required_text(self.session, "session"))
        object.__setattr__(self, "adjustment", _required_text(self.adjustment, "adjustment"))
        object.__setattr__(self, "received_at", _normalise_timestamp(self.received_at, "received_at", required=False))
        object.__setattr__(self, "available_at", _normalise_timestamp(self.available_at, "available_at", required=False))
        _validate_timestamp_order(self.timestamp, self.received_at, self.available_at)
        for field_name in ("open", "high", "low", "close"):
            object.__setattr__(self, field_name, _nonnegative_number(getattr(self, field_name), field_name, optional=True))
        object.__setattr__(self, "volume", _nonnegative_number(self.volume, "volume", optional=True))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))


@dataclass(frozen=True, slots=True)
class DataSnapshot:
    """Immutable, JSON-safe provenance for one normalized data input.

    ``DataStamp.timestamp`` is the market/event time.  ``received_at`` and
    ``available_at`` describe the transport and pipeline boundaries and are
    intentionally optional when the collector did not record them.
    """

    data_stamps: list[DataStamp]
    raw_ref: str | None
    quality: str
    warnings: list[str] | None = None
    source_coverage: dict[str, Any] | None = None
    freshness: dict[str, Any] | None = None
    snapshot_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data_stamps, Sequence) or isinstance(self.data_stamps, (str, bytes)):
            raise TypeError("data_stamps must be a list")
        stamps: list[DataStamp] = []
        for value in self.data_stamps:
            if isinstance(value, DataStamp):
                stamps.append(value)
            elif isinstance(value, Mapping):
                stamps.append(DataStamp(**dict(value)))
            else:
                raise TypeError("data_stamps must contain DataStamp values")
        stamps.sort(key=lambda value: (_timestamp_sort_key(value.timestamp), value.symbol, value.source))
        object.__setattr__(self, "data_stamps", stamps)
        object.__setattr__(self, "raw_ref", _optional_text(self.raw_ref, "raw_ref") or None)
        object.__setattr__(self, "quality", _required_text(self.quality, "quality").lower())
        object.__setattr__(self, "warnings", _normalise_strings(self.warnings))
        object.__setattr__(self, "source_coverage", _mapping_copy(self.source_coverage, "source_coverage"))
        object.__setattr__(self, "freshness", _mapping_copy(self.freshness, "freshness"))
        supplied_id = _optional_text(self.snapshot_id, "snapshot_id")
        object.__setattr__(self, "snapshot_id", supplied_id or _snapshot_id(stamps, self.raw_ref, self.quality))

    @property
    def symbols(self) -> list[str]:
        return sorted({stamp.symbol for stamp in self.data_stamps})

    @property
    def event_start(self) -> str | None:
        return self.data_stamps[0].timestamp if self.data_stamps else None

    @property
    def event_end(self) -> str | None:
        return self.data_stamps[-1].timestamp if self.data_stamps else None

    @property
    def latest_received_at(self) -> str | None:
        return _latest_stamp_time(self.data_stamps, "received_at")

    @property
    def latest_available_at(self) -> str | None:
        return _latest_stamp_time(self.data_stamps, "available_at")

    @property
    def latest_transport_at(self) -> str | None:
        """Return the newest observed availability/receipt/event boundary."""

        values = [
            stamp.available_at or stamp.received_at or stamp.timestamp
            for stamp in self.data_stamps
        ]
        return max(values, key=_timestamp_sort_key) if values else None

    def to_provenance(self) -> dict[str, Any]:
        """Return the data section consumed by Task 5 validation."""

        sources = sorted({stamp.source for stamp in self.data_stamps})
        return {
            "data": {
                "source": sources[0] if len(sources) == 1 else ",".join(sources),
                "version": self.snapshot_id,
                "as_of": self.event_end,
                "status": self.quality,
                "freshness": (self.freshness or {}).get("status") or self.quality,
                "received_at": self.latest_received_at,
                "available_at": self.latest_available_at,
                "raw_ref": self.raw_ref,
                "source_coverage": self.source_coverage or {},
                "warnings": list(self.warnings or []),
            }
        }

    def to_dict(self) -> dict[str, Any]:
        return _serialize_value({
            "data_stamps": self.data_stamps,
            "raw_ref": self.raw_ref,
            "quality": self.quality,
            "warnings": list(self.warnings or []),
            "source_coverage": self.source_coverage or {},
            "freshness": self.freshness or {},
            "snapshot_id": self.snapshot_id,
        })  # type: ignore[return-value]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DataSnapshot":
        if not isinstance(payload, Mapping):
            raise TypeError("DataSnapshot payload must be a mapping")
        return cls(
            data_stamps=list(payload.get("data_stamps") or []),
            raw_ref=payload.get("raw_ref"),
            quality=str(payload.get("quality") or "unknown"),
            warnings=list(payload.get("warnings") or []),
            source_coverage=dict(payload.get("source_coverage") or {}),
            freshness=dict(payload.get("freshness") or {}),
            snapshot_id=payload.get("snapshot_id"),
        )


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    """Reproducibility metadata for a trained model artifact."""

    model_id: str
    feature_version: str
    train_start: str
    train_end: str
    code_commit: str
    seed: int
    metrics: dict[str, float | None]
    model_version: str = ""
    feature_names: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_id", _required_text(self.model_id, "model_id"))
        object.__setattr__(self, "feature_version", _required_text(self.feature_version, "feature_version"))
        train_start = _normalise_timestamp(self.train_start, "train_start")
        train_end = _normalise_timestamp(self.train_end, "train_end")
        if _timestamp_sort_key(train_start) > _timestamp_sort_key(train_end):
            raise ValueError("train_start must be <= train_end")
        object.__setattr__(self, "train_start", train_start)
        object.__setattr__(self, "train_end", train_end)
        object.__setattr__(self, "code_commit", _required_text(self.code_commit, "code_commit"))
        if isinstance(self.seed, bool):
            raise ValueError("seed must be a non-negative integer")
        try:
            seed = int(self.seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("seed must be a non-negative integer") from exc
        if seed < 0 or seed != self.seed:
            raise ValueError("seed must be a non-negative integer")
        object.__setattr__(self, "seed", seed)
        if not isinstance(self.metrics, Mapping):
            raise TypeError("metrics must be a dict")
        metrics: dict[str, float | None] = {}
        for key, value in self.metrics.items():
            if value is None:
                metrics[str(key)] = None
                continue
            number = _number(value, f"metrics.{key}")
            metrics[str(key)] = number
        object.__setattr__(self, "metrics", dict(sorted(metrics.items())))
        object.__setattr__(self, "model_version", _optional_text(self.model_version, "model_version"))
        object.__setattr__(self, "feature_names", _normalise_strings(self.feature_names, as_tuple=True))
        object.__setattr__(self, "profiles", _normalise_strings(self.profiles, as_tuple=True))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model_id": self.model_id,
            "feature_version": self.feature_version,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "code_commit": self.code_commit,
            "seed": self.seed,
            "metrics": dict(self.metrics),
        }
        if self.model_version:
            payload["model_version"] = self.model_version
        if self.feature_names:
            payload["feature_names"] = list(self.feature_names)
        if self.profiles:
            payload["profiles"] = list(self.profiles)
        return payload

    def to_provenance(self, *, status: str = "complete") -> dict[str, Any]:
        return {
            "model": {
                **self.to_dict(),
                "as_of": self.train_end,
                "status": _required_text(status, "status"),
                "freshness": _required_text(status, "status"),
            }
        }


@dataclass(frozen=True, slots=True)
class SignalOutput:
    symbol: str
    score: float
    confidence: float
    as_of: str
    feature_version: str = ""
    model_version: str = ""
    reason: str = ""
    provider: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        object.__setattr__(self, "score", _number(self.score, "score"))
        object.__setattr__(self, "confidence", _bounded_number(self.confidence, "confidence", 0.0, 1.0))
        object.__setattr__(self, "as_of", _normalise_timestamp(self.as_of, "as_of"))
        object.__setattr__(self, "feature_version", _optional_text(self.feature_version, "feature_version"))
        object.__setattr__(self, "model_version", _optional_text(self.model_version, "model_version"))
        object.__setattr__(self, "reason", _optional_text(self.reason, "reason"))
        object.__setattr__(self, "provider", _optional_text(self.provider, "provider"))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    symbol: str
    side: str
    quantity: float
    decision_at: str
    decision_price: float | None = None
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    run_id: str = ""
    strategy_id: str = ""
    strategy_version: int | None = None
    submitted_at: str | None = None
    reason: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        side = _required_text(self.side, "side").lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", _nonnegative_number(self.quantity, "quantity"))
        object.__setattr__(self, "decision_at", _normalise_timestamp(self.decision_at, "decision_at"))
        object.__setattr__(self, "decision_price", _nonnegative_number(self.decision_price, "decision_price", optional=True))
        order_type = _required_text(self.order_type, "order_type").lower()
        if order_type not in {"market", "limit", "stop", "stop_limit"}:
            raise ValueError(f"unsupported order type: {order_type}")
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "limit_price", _nonnegative_number(self.limit_price, "limit_price", optional=True))
        object.__setattr__(self, "stop_price", _nonnegative_number(self.stop_price, "stop_price", optional=True))
        object.__setattr__(self, "run_id", _optional_text(self.run_id, "run_id"))
        object.__setattr__(self, "strategy_id", _optional_text(self.strategy_id, "strategy_id"))
        if self.strategy_version is not None:
            if isinstance(self.strategy_version, bool) or int(self.strategy_version) != self.strategy_version or int(self.strategy_version) < 0:
                raise ValueError("strategy_version must be a non-negative integer")
            object.__setattr__(self, "strategy_version", int(self.strategy_version))
        object.__setattr__(self, "submitted_at", _normalise_timestamp(self.submitted_at, "submitted_at", required=False))
        object.__setattr__(self, "reason", _optional_text(self.reason, "reason"))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))


@dataclass(frozen=True, slots=True)
class FillEvent:
    run_id: str
    symbol: str
    side: str
    requested_qty: float
    filled_qty: float
    decision_price: float | None
    fill_price: float | None
    status: str
    decision_at: str
    filled_at: str | None
    submitted_at: str | None = None
    accepted_at: str | None = None
    fee: float = 0.0
    slippage: float = 0.0
    strategy_id: str = ""
    strategy_version: int | None = None
    reason: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        side = _required_text(self.side, "side").lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "requested_qty", _nonnegative_number(self.requested_qty, "requested_qty"))
        object.__setattr__(self, "filled_qty", _nonnegative_number(self.filled_qty, "filled_qty"))
        if self.filled_qty > self.requested_qty + 1e-12:
            raise ValueError("filled_qty must be <= requested_qty")
        object.__setattr__(self, "decision_price", _nonnegative_number(self.decision_price, "decision_price", optional=True))
        object.__setattr__(self, "fill_price", _nonnegative_number(self.fill_price, "fill_price", optional=True))
        status = _required_text(self.status, "status").lower()
        if status not in {"pending", "accepted", "partial", "filled", "rejected", "cancelled", "canceled"}:
            raise ValueError(f"unsupported fill status: {status}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "decision_at", _normalise_timestamp(self.decision_at, "decision_at"))
        object.__setattr__(self, "filled_at", _normalise_timestamp(self.filled_at, "filled_at", required=False))
        object.__setattr__(self, "submitted_at", _normalise_timestamp(self.submitted_at, "submitted_at", required=False))
        object.__setattr__(self, "accepted_at", _normalise_timestamp(self.accepted_at, "accepted_at", required=False))
        object.__setattr__(self, "fee", _nonnegative_number(self.fee, "fee") or 0.0)
        object.__setattr__(self, "slippage", _nonnegative_number(self.slippage, "slippage") or 0.0)
        object.__setattr__(self, "strategy_id", _optional_text(self.strategy_id, "strategy_id"))
        if self.strategy_version is not None:
            if isinstance(self.strategy_version, bool) or int(self.strategy_version) != self.strategy_version or int(self.strategy_version) < 0:
                raise ValueError("strategy_version must be a non-negative integer")
            object.__setattr__(self, "strategy_version", int(self.strategy_version))
        object.__setattr__(self, "reason", _optional_text(self.reason, "reason"))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    @property
    def notional(self) -> float:
        """Return filled notional at the effective execution price."""

        if self.fill_price is None:
            return 0.0
        return float(self.filled_qty * self.fill_price)


@dataclass(frozen=True, slots=True)
class PositionState:
    symbol: str
    quantity: float
    average_price: float | None = 0.0
    as_of: str | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    market_price: float | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        object.__setattr__(self, "quantity", _nonnegative_number(self.quantity, "quantity"))
        object.__setattr__(self, "average_price", _nonnegative_number(self.average_price, "average_price", optional=True))
        object.__setattr__(self, "as_of", _normalise_timestamp(self.as_of, "as_of", required=False))
        object.__setattr__(self, "realized_pnl", _number(self.realized_pnl, "realized_pnl"))
        object.__setattr__(self, "unrealized_pnl", _number(self.unrealized_pnl, "unrealized_pnl"))
        object.__setattr__(self, "market_price", _nonnegative_number(self.market_price, "market_price", optional=True))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))


def serialize_event(value: object) -> dict[str, object]:
    """Convert a strategy event DTO into a JSON-compatible field mapping."""
    if not isinstance(value, (DataStamp, DataSnapshot, ModelProvenance, SignalOutput, OrderIntent, FillEvent, PositionState)):
        raise TypeError("value must be a strategy event DTO")
    if isinstance(value, (DataSnapshot, ModelProvenance)):
        return value.to_dict()
    return {item.name: _serialize_value(getattr(value, item.name)) for item in fields(value)}


def deserialize_event(payload: dict[str, object], event_type: str) -> object:
    """Rebuild a strategy event DTO from its serialized field mapping."""
    if not isinstance(payload, dict):
        raise TypeError("event payload must be a dict")
    key = str(event_type or "").strip().lower()
    class_name = _EVENT_TYPES.get(key)
    if class_name is None:
        raise ValueError(f"unsupported event type: {event_type}")
    event_class = globals()[class_name]
    try:
        if event_class is DataSnapshot:
            return DataSnapshot.from_dict(payload)
        return event_class(**deepcopy(payload))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {key} event: {exc}") from exc


def _serialize_value(value: object) -> object:
    if isinstance(value, (DataSnapshot, ModelProvenance)):
        return value.to_dict()
    if isinstance(value, DataStamp):
        return {item.name: _serialize_value(getattr(value, item.name)) for item in fields(value)}
    if hasattr(value, "item") and callable(value.item):
        try:
            return _serialize_value(value.item())
        except (ValueError, TypeError):
            pass
    if hasattr(value, "tolist") and callable(value.tolist):
        try:
            return _serialize_value(value.tolist())
        except (ValueError, TypeError):
            pass
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, set):
        return [_serialize_value(item) for item in sorted(value, key=str)]
    if isinstance(value, float):
        return value if isfinite(value) else None
    return value


def _validate_timestamp_order(timestamp: str, received_at: str | None, available_at: str | None) -> None:
    event = _timestamp_sort_key(timestamp)
    received = _timestamp_sort_key(received_at) if received_at else None
    available = _timestamp_sort_key(available_at) if available_at else None
    if received is not None and received < event:
        raise ValueError("received_at must be >= timestamp")
    if available is not None and received is not None and available < received:
        raise ValueError("available_at must be >= received_at")
    if available is not None and received is None and available < event:
        raise ValueError("available_at must be >= timestamp")


def _timestamp_sort_key(value: object) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_strings(values: object, *, as_tuple: bool = False):
    if values is None:
        output: list[str] = []
    elif isinstance(values, str):
        output = [values.strip()] if values.strip() else []
    elif isinstance(values, Sequence):
        output = [str(value).strip() for value in values if str(value).strip()]
    else:
        raise TypeError("value must contain strings")
    return tuple(output) if as_tuple else output


def _latest_stamp_time(stamps: Sequence[DataStamp], field_name: str) -> str | None:
    values = [getattr(stamp, field_name) for stamp in stamps if getattr(stamp, field_name)]
    return max(values, key=_timestamp_sort_key) if values else None


def _snapshot_id(stamps: Sequence[DataStamp], raw_ref: str | None, quality: str) -> str:
    stamp_payloads = []
    for stamp in stamps:
        payload = _serialize_value(stamp)
        if isinstance(payload, dict):
            # Receipt/availability describe transport state and must not make
            # the same raw event content receive a different snapshot id.
            payload.pop("received_at", None)
            payload.pop("available_at", None)
        stamp_payloads.append(payload)
    payload = {
        "stamps": stamp_payloads,
        "raw_ref": raw_ref,
        "quality": quality,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
