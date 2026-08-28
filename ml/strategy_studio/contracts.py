from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timezone
from math import isfinite
from typing import Any, Mapping


_EVENT_TYPES = {
    "data": "DataStamp",
    "datastamp": "DataStamp",
    "data_stamp": "DataStamp",
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


def _normalise_timestamp(value: object, field_name: str, *, required: bool = True) -> datetime | None:
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

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    timestamp: datetime | str
    source: str
    timeframe: str
    quality: str
    session: str = "regular"
    adjustment: str = "raw"
    received_at: datetime | str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _required_text(self.symbol, "symbol"))
        object.__setattr__(self, "timestamp", _normalise_timestamp(self.timestamp, "timestamp"))
        object.__setattr__(self, "source", _required_text(self.source, "source"))
        object.__setattr__(self, "timeframe", _required_text(self.timeframe, "timeframe"))
        object.__setattr__(self, "quality", _required_text(self.quality, "quality"))
        object.__setattr__(self, "session", _required_text(self.session, "session"))
        object.__setattr__(self, "adjustment", _required_text(self.adjustment, "adjustment"))
        object.__setattr__(self, "received_at", _normalise_timestamp(self.received_at, "received_at", required=False))
        for field_name in ("open", "high", "low", "close"):
            object.__setattr__(self, field_name, _nonnegative_number(getattr(self, field_name), field_name, optional=True))
        object.__setattr__(self, "volume", _nonnegative_number(self.volume, "volume", optional=True))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))


@dataclass(frozen=True, slots=True)
class SignalOutput:
    symbol: str
    score: float
    confidence: float
    as_of: datetime | str
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
    decision_at: datetime | str
    decision_price: float | None = None
    order_type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    run_id: str = ""
    strategy_id: str = ""
    strategy_version: int | None = None
    submitted_at: datetime | str | None = None
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
    decision_at: datetime | str
    filled_at: datetime | str | None
    submitted_at: datetime | str | None = None
    accepted_at: datetime | str | None = None
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


@dataclass(frozen=True, slots=True)
class PositionState:
    symbol: str
    quantity: float
    average_price: float | None = 0.0
    as_of: datetime | str | None = None
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
    if not isinstance(value, (DataStamp, SignalOutput, OrderIntent, FillEvent, PositionState)):
        raise TypeError("value must be a strategy event DTO")
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
        return event_class(**deepcopy(payload))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {key} event: {exc}") from exc


def _serialize_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value
