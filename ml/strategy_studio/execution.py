"""Deterministic bar-based execution simulator and position ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .contracts import FillEvent, OrderIntent, PositionState, serialize_event
from .profiles import execution_defaults


_ORDER_STATUSES = {"partial", "filled", "rejected", "cancelled"}
_BAR_FIELDS = ("open", "high", "low", "close", "volume")


def _number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return number


def _timestamp(value: object) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _timestamp_text(value: object) -> str:
    return _timestamp(value).isoformat()


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Validated assumptions shared by backtests and paper execution."""

    latency_bars: int = 1
    fees_bps: float = 0.0
    slippage_bps: float = 0.0
    spread_bps: float = 0.0
    max_participation_rate: float = 1.0
    partial_fill: bool = True
    initial_cash: float = 100_000.0
    profile: str = "bar"
    session: str = "regular"
    allow_short: bool = False
    cancel_unfilled: bool = False
    min_order_qty: float = 0.0
    run_id: str = "execution"
    pause_on_stale: bool = True
    allow_exits_on_pause: bool = True
    profile_health: object | None = None
    quote_health: object | None = None

    def __post_init__(self) -> None:
        latency = self.latency_bars
        if isinstance(latency, bool):
            raise ValueError("latency_bars must be a non-negative integer")
        try:
            latency_number = float(latency)
        except (TypeError, ValueError) as exc:
            raise ValueError("latency_bars must be a non-negative integer") from exc
        if not isfinite(latency_number) or latency_number != int(latency_number) or latency_number < 0:
            raise ValueError("latency_bars must be a non-negative integer")
        object.__setattr__(self, "latency_bars", int(latency_number))
        for name in ("fees_bps", "slippage_bps", "spread_bps"):
            object.__setattr__(self, name, _number(getattr(self, name), name, minimum=0.0))
        participation = _number(self.max_participation_rate, "max_participation_rate")
        if not 0.0 <= participation <= 1.0:
            raise ValueError("max_participation_rate must be between 0 and 1")
        object.__setattr__(self, "max_participation_rate", participation)
        if not isinstance(self.partial_fill, bool):
            raise ValueError("partial_fill must be a boolean")
        object.__setattr__(self, "initial_cash", _number(self.initial_cash, "initial_cash", minimum=0.0))
        if self.initial_cash <= 0.0:
            raise ValueError("initial_cash must be > 0")
        for name in ("profile", "session", "run_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value.lower() if name != "run_id" else value)
        if not isinstance(self.allow_short, bool):
            raise ValueError("allow_short must be a boolean")
        if not isinstance(self.cancel_unfilled, bool):
            raise ValueError("cancel_unfilled must be a boolean")
        object.__setattr__(self, "min_order_qty", _number(self.min_order_qty, "min_order_qty", minimum=0.0))
        for name in ("pause_on_stale", "allow_exits_on_pause"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> "ExecutionConfig":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise TypeError("execution config must be a dict")
        values = dict(payload)
        if "latency_bars" not in values and "latency_ms" in values:
            latency_ms = _number(values["latency_ms"], "latency_ms", minimum=0.0)
            values["latency_bars"] = 0 if latency_ms == 0 else 1
        if "cost_bps" in values and not any(
            key in values for key in ("fees_bps", "slippage_bps", "spread_bps")
        ):
            values["fees_bps"] = values["cost_bps"]
        for key in ("partial_fill", "allow_short", "cancel_unfilled"):
            if key in values:
                values[key] = _boolean(values[key], key)
        allowed = {
            "latency_bars", "fees_bps", "slippage_bps", "spread_bps",
            "max_participation_rate", "partial_fill", "initial_cash", "profile",
            "session", "allow_short", "cancel_unfilled", "min_order_qty", "run_id",
            "pause_on_stale", "allow_exits_on_pause", "profile_health", "quote_health",
        }
        return cls(**{key: values[key] for key in allowed if key in values})


@dataclass(slots=True)
class ExecutionResult:
    """Order/fill ledger plus mark-to-market portfolio output."""

    intents: list[OrderIntent] = field(default_factory=list)
    fills: list[FillEvent] = field(default_factory=list)
    positions: dict[str, PositionState] = field(default_factory=dict)
    equity: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ledger(self) -> list[FillEvent]:
        """Alias used by callers that refer to the fill stream as a ledger."""

        return self.fills

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable({
            "intents": [serialize_event(intent) for intent in self.intents],
            "fills": [serialize_event(fill) for fill in self.fills],
            "positions": {symbol: serialize_event(position) for symbol, position in self.positions.items()},
            "equity": self.equity,
            "trades": [dict(trade) for trade in self.trades],
            "summary": dict(self.summary),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "diagnostics": [dict(item) for item in self.diagnostics],
            "ok": self.ok,
        })


def to_jsonable(value: object) -> Any:
    """Convert pandas/numpy values and DTO payloads to strict JSON values."""

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.DataFrame):
        return {
            "index": [to_jsonable(item) for item in value.index.tolist()],
            "columns": [to_jsonable(item) for item in value.columns.tolist()],
            "data": [[to_jsonable(item) for item in row] for row in value.to_numpy(dtype=object).tolist()],
        }
    if isinstance(value, pd.Series):
        return {
            "index": [to_jsonable(item) for item in value.index.tolist()],
            "data": [to_jsonable(item) for item in value.tolist()],
            "name": to_jsonable(value.name),
        }
    if isinstance(value, pd.Index):
        return [to_jsonable(item) for item in value.tolist()]
    if isinstance(value, np.ndarray):
        return [to_jsonable(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return to_jsonable(value.item())
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return value


def _intent_sort_key(intent: OrderIntent) -> tuple[object, ...]:
    payload = to_jsonable(serialize_event(intent))
    semantic_payload = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return (
        _timestamp(intent.decision_at),
        intent.symbol.upper().strip(),
        intent.side,
        intent.order_type,
        str(intent.run_id),
        str(intent.strategy_id),
        str(intent.strategy_version),
        semantic_payload,
    )


def _stable_intent_id(intent: OrderIntent) -> str:
    payload = json.dumps(
        to_jsonable(serialize_event(intent)),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def execute_intents(
    intents: list[OrderIntent],
    bars: dict[str, pd.DataFrame],
    config: ExecutionConfig,
) -> list[FillEvent]:
    """Execute intents against deterministic OHLCV bars.

    The input order is deliberately ignored after a stable sort, so replaying
    the same intents produces the same event stream.
    """

    if not isinstance(config, ExecutionConfig):
        config = ExecutionConfig.from_dict(config)  # type: ignore[arg-type]
    normalized = _normalize_bars(bars)
    ordered = sorted(intents, key=_intent_sort_key)
    used_capacity: dict[tuple[str, pd.Timestamp], float] = {}
    fills: list[FillEvent] = []
    for intent in ordered:
        frame = normalized.get(intent.symbol.upper().strip())
        decision_at = _timestamp_text(intent.decision_at)
        submitted_at = _timestamp_text(intent.submitted_at or intent.decision_at)
        run_id = intent.run_id or f"{config.run_id}-{_stable_intent_id(intent)}"
        if _is_cancelled(intent):
            fills.append(_event(intent, run_id=run_id, status="cancelled", reason="cancelled_by_strategy", submitted_at=submitted_at))
            continue
        health = _health_for_event(config, normalized, intent.symbol, intent.decision_at)
        if config.pause_on_stale and _health_blocks_order(health, intent.side, config):
            fills.append(_event(
                intent,
                run_id=run_id,
                status="cancelled",
                reason="strategy_paused",
                submitted_at=submitted_at,
                metadata={
                    **dict(intent.metadata or {}),
                    "diagnostic": "strategy_paused",
                    "profile_health": health,
                },
            ))
            continue
        if frame is None:
            status = "cancelled" if config.cancel_unfilled else "rejected"
            fills.append(_event(intent, run_id=run_id, status=status, reason="missing_bars", submitted_at=submitted_at))
            continue
        if intent.quantity <= config.min_order_qty:
            fills.append(_event(intent, run_id=run_id, status="rejected", reason="quantity_below_minimum", submitted_at=submitted_at))
            continue
        eligible = _eligible_bar(frame, intent.decision_at, config.latency_bars)
        if eligible is None:
            status = "cancelled" if config.cancel_unfilled else "rejected"
            fills.append(_event(intent, run_id=run_id, status=status, reason="no_eligible_bar", submitted_at=submitted_at))
            continue
        bar_time, row = eligible
        fills.append(_execute_intent_on_bar(intent, row, bar_time, config, used_capacity))
    return fills


def _execute_intent_on_bar(
    intent: OrderIntent,
    row: pd.Series,
    bar_time: pd.Timestamp,
    config: ExecutionConfig,
    used_capacity: dict[tuple[str, pd.Timestamp], float],
) -> FillEvent:
    """Evaluate one intent only against its current eligible bar."""

    submitted_at = _timestamp_text(intent.submitted_at or intent.decision_at)
    run_id = intent.run_id or f"{config.run_id}-{_stable_intent_id(intent)}"
    base_price, reason = _eligible_price(intent, row, config.cancel_unfilled)
    accepted_at = _timestamp_text(bar_time)
    if base_price is None:
        status = "cancelled" if config.cancel_unfilled else "rejected"
        return _event(
            intent, run_id=run_id, status=status, reason=reason,
            submitted_at=submitted_at, accepted_at=accepted_at,
        )

    cap = _liquidity_cap(row["volume"], config.max_participation_rate)
    capacity_key = (intent.symbol.upper().strip(), _timestamp(bar_time))
    already_used = used_capacity.get(capacity_key, 0.0)
    remaining_cap = max(0.0, cap - already_used) if isfinite(cap) else cap
    if remaining_cap <= 0.0:
        return _event(
            intent, run_id=run_id, status="rejected", reason="insufficient_liquidity",
            submitted_at=submitted_at, accepted_at=accepted_at,
        )
    if intent.quantity > remaining_cap and not config.partial_fill:
        return _event(
            intent, run_id=run_id, status="rejected", reason="insufficient_liquidity",
            submitted_at=submitted_at, accepted_at=accepted_at,
        )

    filled_qty = min(float(intent.quantity), remaining_cap)
    used_capacity[capacity_key] = already_used + filled_qty
    status = "partial" if filled_qty + 1e-12 < intent.quantity else "filled"
    fill_price, slippage_per_unit, spread_per_unit = _apply_price_impact(
        base_price, intent.side, config,
    )
    fee = fill_price * filled_qty * config.fees_bps / 10000.0
    metadata = dict(intent.metadata or {})
    metadata.update({
        "raw_price": float(base_price),
        "spread_per_unit": float(spread_per_unit),
        "slippage_per_unit": float(slippage_per_unit),
        "volume_cap": float(cap) if isfinite(cap) else None,
        "remaining_volume_cap": float(remaining_cap) if isfinite(remaining_cap) else None,
    })
    return _event(
        intent, run_id=run_id, status=status, reason=reason,
        filled_at=_timestamp_text(bar_time), submitted_at=submitted_at,
        accepted_at=accepted_at, filled_qty=filled_qty, fill_price=fill_price,
        fee=fee, slippage=slippage_per_unit, metadata=metadata,
    )


def apply_fills(position: PositionState, fills: list[FillEvent]) -> PositionState:
    """Apply filled quantities to one long position in event order."""

    quantity = float(position.quantity)
    average_price = float(position.average_price or 0.0)
    realized_pnl = float(position.realized_pnl)
    as_of = position.as_of
    market_price = position.market_price
    for fill in fills:
        if fill.symbol != position.symbol:
            raise ValueError(f"fill symbol {fill.symbol} does not match position {position.symbol}")
        if fill.status not in {"partial", "filled"} or fill.filled_qty <= 0.0:
            continue
        qty = float(fill.filled_qty)
        price = float(fill.fill_price or 0.0)
        fee = float(fill.fee or 0.0)
        if fill.side == "buy":
            total_cost = quantity * average_price + qty * price
            quantity += qty
            average_price = total_cost / quantity if quantity else 0.0
            realized_pnl -= fee
        else:
            if qty > quantity + 1e-12:
                raise ValueError(f"cannot sell {qty} shares with only {quantity} held")
            realized_pnl += (price - average_price) * qty - fee
            quantity -= qty
            if quantity <= 1e-12:
                quantity = 0.0
                average_price = 0.0
        as_of = fill.filled_at or fill.accepted_at or fill.decision_at
        market_price = price
    return PositionState(
        symbol=position.symbol,
        quantity=quantity,
        average_price=average_price,
        as_of=as_of,
        realized_pnl=realized_pnl,
        unrealized_pnl=position.unrealized_pnl,
        market_price=market_price,
        metadata=position.metadata,
    )


def run_execution_backtest(
    target_weights: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    config: ExecutionConfig,
) -> ExecutionResult:
    """Convert target weights into intents, execute them, and mark the ledger."""

    if not isinstance(target_weights, pd.DataFrame):
        raise TypeError("target_weights must be a pandas DataFrame")
    if not isinstance(config, ExecutionConfig):
        config = ExecutionConfig.from_dict(config)  # type: ignore[arg-type]
    normalized = _normalize_bars(bars)
    targets = target_weights.copy()
    if targets.empty:
        return ExecutionResult(summary=_summary([], [], pd.DataFrame(), config))
    targets.index = pd.to_datetime(targets.index, utc=True)
    targets = targets.sort_index()
    targets.columns = [str(column).upper().strip() for column in targets.columns]
    targets = targets.loc[:, ~targets.columns.duplicated()]
    intents, fills, warnings, diagnostics = _execute_target_weights(targets, normalized, config)
    equity, positions, mark_warnings = _mark_ledger(fills, normalized, config)
    warnings.extend(mark_warnings)
    trades = [_fill_to_trade(fill) for fill in fills if fill.status in {"partial", "filled"} and fill.filled_qty > 0]
    summary = _summary(intents, fills, equity, config)
    return ExecutionResult(
        intents=intents,
        fills=fills,
        positions=positions,
        equity=equity,
        trades=trades,
        summary=summary,
        warnings=warnings,
        diagnostics=diagnostics,
    )


def _normalize_bars(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    if not isinstance(bars, Mapping):
        raise TypeError("bars must be a dict keyed by symbol")
    normalized: dict[str, pd.DataFrame] = {}
    for raw_symbol, raw_frame in bars.items():
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol:
            continue
        if not isinstance(raw_frame, pd.DataFrame):
            raise TypeError(f"bars[{symbol}] must be a pandas DataFrame")
        frame = raw_frame.copy()
        frame.attrs = dict(getattr(raw_frame, "attrs", {}) or {})
        if frame.empty:
            normalized[symbol] = pd.DataFrame(columns=_BAR_FIELDS, dtype="float64")
            continue
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index(kind="mergesort")
        columns = {str(column).strip().lower().replace(" ", "_"): column for column in frame.columns}
        for field_name in _BAR_FIELDS:
            if field_name not in columns:
                if field_name in {"open", "high", "low"} and "close" in columns:
                    frame[field_name] = frame[columns["close"]]
                elif field_name == "volume":
                    # A close-only research panel has no liquidity observation;
                    # treat it as uncapped instead of inventing a volume value.
                    frame[field_name] = np.inf
                else:
                    raise ValueError(f"bars[{symbol}] is missing {field_name}")
            else:
                frame[field_name] = pd.to_numeric(frame[columns[field_name]], errors="coerce")
        frame = frame.loc[:, list(_BAR_FIELDS)]
        normalized[symbol] = frame
    return normalized


def _eligible_bar(frame: pd.DataFrame, decision_at: object, latency_bars: int) -> tuple[pd.Timestamp, pd.Series] | None:
    if frame.empty:
        return None
    decision = _timestamp(decision_at)
    positions = np.flatnonzero(frame.index >= decision)
    if len(positions) == 0:
        return None
    position = int(positions[0]) + latency_bars
    if position >= len(frame):
        return None
    return frame.index[position], frame.iloc[position]


def _eligible_price(intent: OrderIntent, row: pd.Series, cancel_unfilled: bool) -> tuple[float | None, str]:
    open_price = _valid_price(row.get("open"))
    high = _valid_price(row.get("high"))
    low = _valid_price(row.get("low"))
    if open_price is None or high is None or low is None:
        return None, "invalid_bar_price"
    if intent.order_type == "market":
        return open_price, "market_open"
    if intent.order_type == "limit":
        if intent.limit_price is None:
            return None, "missing_limit_price"
        if intent.side == "buy" and low <= intent.limit_price:
            return float(intent.limit_price), "limit_touch"
        if intent.side == "sell" and high >= intent.limit_price:
            return float(intent.limit_price), "limit_touch"
        return None, "limit_not_reached"
    if intent.stop_price is None:
        return None, "missing_stop_price"
    triggered, gap = _stop_triggered(intent.side, float(intent.stop_price), open_price, high, low)
    if not triggered:
        return None, "stop_not_triggered"
    if intent.order_type == "stop":
        return (open_price if gap else float(intent.stop_price)), ("stop_gap" if gap else "stop_trigger")
    if intent.limit_price is None:
        return None, "missing_limit_price"
    if intent.side == "buy" and low <= intent.limit_price:
        return float(intent.limit_price), "stop_limit_trigger"
    if intent.side == "sell" and high >= intent.limit_price:
        return float(intent.limit_price), "stop_limit_trigger"
    return None, "stop_limit_not_reached"


def _stop_triggered(side: str, stop: float, open_price: float, high: float, low: float) -> tuple[bool, bool]:
    if side == "buy":
        if open_price >= stop:
            return True, True
        return high >= stop, False
    if open_price <= stop:
        return True, True
    return low <= stop, False


def _valid_price(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number > 0.0 else None


def _liquidity_cap(volume: object, participation: float) -> float:
    try:
        value = float(volume)
    except (TypeError, ValueError):
        return 0.0
    if np.isinf(value):
        return np.inf
    if not isfinite(value) or value <= 0.0:
        return 0.0
    return value * participation


def _apply_price_impact(base_price: float, side: str, config: ExecutionConfig) -> tuple[float, float, float]:
    spread_per_unit = base_price * config.spread_bps / 20000.0
    slippage_per_unit = base_price * config.slippage_bps / 10000.0
    direction = 1.0 if side == "buy" else -1.0
    return base_price + direction * (spread_per_unit + slippage_per_unit), slippage_per_unit, spread_per_unit


def _is_cancelled(intent: OrderIntent) -> bool:
    metadata = intent.metadata or {}
    return bool(metadata.get("cancelled") or metadata.get("canceled") or metadata.get("cancel"))


def _event(
    intent: OrderIntent,
    *,
    run_id: str,
    status: str,
    reason: str,
    submitted_at: str,
    accepted_at: str | None = None,
    filled_at: str | None = None,
    filled_qty: float = 0.0,
    fill_price: float | None = None,
    fee: float = 0.0,
    slippage: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> FillEvent:
    return FillEvent(
        run_id=run_id,
        symbol=intent.symbol,
        side=intent.side,
        requested_qty=float(intent.quantity),
        filled_qty=float(filled_qty),
        decision_price=intent.decision_price,
        fill_price=fill_price,
        status=status,
        decision_at=intent.decision_at,
        filled_at=filled_at,
        submitted_at=submitted_at,
        accepted_at=accepted_at,
        fee=fee,
        slippage=slippage,
        strategy_id=intent.strategy_id,
        strategy_version=intent.strategy_version,
        reason=reason,
        metadata=metadata or intent.metadata,
    )


def _health_mapping(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            value = value.to_dict()
        except Exception:
            return None
    if not isinstance(value, Mapping):
        return None
    payload = dict(value)
    status = str(payload.get("status") or "").strip().lower()
    reason = payload.get("reason")
    quality = str(payload.get("quality") or "").strip().lower()
    if status and reason is not None:
        return payload
    if quality in {"missing", "incomplete", "invalid", "degraded"}:
        return {**payload, "status": "pause", "reason": "incomplete_bars"}
    if status in {"available", "complete", "ok"}:
        return {**payload, "status": "fresh", "reason": "fresh"}
    if status in {"degraded", "missing", "unavailable", "disabled", "stale"}:
        return {**payload, "status": "pause", "reason": str(reason or "source_unavailable")}
    if payload.get("fresh") is False:
        return {**payload, "status": "pause", "reason": str(payload.get("reason") or "stale_quote")}
    return None


def _health_at(value: object, symbol: str, at: object) -> dict[str, Any] | None:
    """Read one health record or the latest record in a saved replay timeline."""

    payload = _health_mapping(value)
    if payload is not None:
        return payload
    if not isinstance(value, Mapping):
        return None
    symbol_key = str(symbol or "").upper().strip()
    for key in (symbol_key, symbol, "default", "current"):
        candidate = value.get(key) if key else None
        mapped = _health_mapping(candidate)
        if mapped is not None:
            return mapped
    source_health = _aggregate_source_health(value)
    if source_health is not None:
        return source_health
    timeline = value.get("timeline") or value.get("by_timestamp") or value.get("history")
    if not isinstance(timeline, Mapping):
        timeline = value
    selected: dict[str, Any] | None = None
    selected_at: pd.Timestamp | None = None
    current = _timestamp(at)
    for stamp, candidate in timeline.items():
        mapped = _health_mapping(candidate)
        if mapped is None:
            continue
        try:
            timestamp = _timestamp(stamp)
        except (TypeError, ValueError):
            continue
        if timestamp <= current and (selected_at is None or timestamp > selected_at):
            selected = mapped
            selected_at = timestamp
    return selected


def _aggregate_source_health(value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Combine optional source heartbeats using the same fallback policy as readers."""

    records: list[tuple[str, dict[str, Any]]] = []
    for source, candidate in value.items():
        if str(source) in {"timeline", "by_timestamp", "history"}:
            continue
        mapped = _health_mapping(candidate)
        if mapped is not None:
            records.append((str(source), mapped))
    if not records:
        return None

    active = [(source, health) for source, health in records
              if str(health.get("status") or "").strip().lower() != "disabled"]
    if not active:
        return {
            "status": "pause",
            "reason": "quote_source_disabled",
            "sources": [source for source, _health in records],
        }

    fresh = [(source, health) for source, health in active
             if not _health_blocks_entries(health)]
    if fresh:
        return {
            "status": "fresh",
            "reason": "fresh",
            "source": fresh[0][0],
            "sources": [source for source, _health in active],
        }

    source, health = active[0]
    return {
        **health,
        "source": health.get("source") or source,
        "sources": [item[0] for item in active],
    }


def _health_for_event(
    config: ExecutionConfig,
    bars: dict[str, pd.DataFrame],
    symbol: str,
    at: object,
) -> dict[str, Any] | None:
    frame = bars.get(str(symbol or "").upper().strip())
    frame_snapshot = frame.attrs.get("data_snapshot") if frame is not None else None
    snapshot_health = None
    if frame_snapshot is not None:
        snapshot_quality = getattr(frame_snapshot, "quality", None)
        if snapshot_quality is None and isinstance(frame_snapshot, Mapping):
            snapshot_quality = frame_snapshot.get("quality")
        if str(snapshot_quality or "").strip().lower() not in {"", "complete", "ok", "fresh"}:
            snapshot_health = {"status": "pause", "reason": "incomplete_bars", "quality": snapshot_quality}
    candidates = [
        _health_at(config.profile_health, symbol, at),
        _health_at(config.quote_health, symbol, at),
        _health_at(frame.attrs.get("profile_health") if frame is not None else None, symbol, at),
        _health_at(frame.attrs.get("quote_health") if frame is not None else None, symbol, at),
        _health_at(frame.attrs.get("source_health") if frame is not None else None, symbol, at),
        snapshot_health,
    ]
    blocked = next((candidate for candidate in candidates if candidate and _health_blocks_entries(candidate)), None)
    return blocked or next((candidate for candidate in candidates if candidate), None)


def _health_blocks_entries(health: Mapping[str, Any] | None) -> bool:
    if not health:
        return False
    status = str(health.get("status") or "pause").strip().lower()
    if status == "fresh" and health.get("fresh") is not False:
        return False
    return True


def _health_blocks_order(
    health: Mapping[str, Any] | None,
    side: str,
    config: ExecutionConfig,
) -> bool:
    if not _health_blocks_entries(health):
        return False
    return str(side or "").strip().lower() == "buy" or not config.allow_exits_on_pause


def _execute_target_weights(
    targets: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    config: ExecutionConfig,
) -> tuple[list[OrderIntent], list[FillEvent], list[str], list[dict[str, Any]]]:
    """Create and execute target orders in one causal chronological pass."""

    warnings: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    intents: list[OrderIntent] = []
    fills: list[FillEvent] = []
    held_quantities: dict[str, float] = {symbol: 0.0 for symbol in bars}
    pending_buys: dict[str, float] = {}
    pending_sells: dict[str, float] = {}
    scheduled: dict[pd.Timestamp, list[OrderIntent]] = {}
    unscheduled: list[OrderIntent] = []
    used_capacity: dict[tuple[str, pd.Timestamp], float] = {}

    target_events: dict[pd.Timestamp, list[pd.Series]] = {}
    for timestamp, row in targets.iterrows():
        target_events.setdefault(_timestamp(timestamp), []).append(row)
    event_times = set(target_events)
    for frame in bars.values():
        event_times.update(frame.index)

    def process_due(bar_time: pd.Timestamp) -> None:
        for intent in sorted(scheduled.pop(bar_time, []), key=_intent_sort_key):
            symbol = intent.symbol.upper().strip()
            pending = pending_buys if intent.side == "buy" else pending_sells
            pending[symbol] = max(0.0, pending.get(symbol, 0.0) - intent.quantity)
            frame = bars[symbol]
            row = frame.loc[bar_time]
            fill = _execute_intent_on_bar(intent, row, bar_time, config, used_capacity)
            fills.append(fill)
            if fill.status in {"partial", "filled"} and fill.filled_qty > 0.0:
                if intent.side == "buy":
                    held_quantities[symbol] = held_quantities.get(symbol, 0.0) + fill.filled_qty
                else:
                    held_quantities[symbol] = max(
                        0.0, held_quantities.get(symbol, 0.0) - fill.filled_qty,
                    )

    def cancel_pending_orders(
        symbol: str,
        side: str,
        event_time: pd.Timestamp,
        reason: str,
    ) -> None:
        pending = pending_buys if side == "buy" else pending_sells
        for eligible_time in sorted(list(scheduled)):
            remaining: list[OrderIntent] = []
            for intent in sorted(scheduled[eligible_time], key=_intent_sort_key):
                if intent.symbol.upper().strip() != symbol or intent.side != side:
                    remaining.append(intent)
                    continue
                pending[symbol] = max(0.0, pending.get(symbol, 0.0) - intent.quantity)
                metadata = dict(intent.metadata or {})
                metadata.update({
                    "cancelled_at": _timestamp_text(event_time),
                    "cancelled_by": "target_reconciliation",
                })
                run_id = intent.run_id or f"{config.run_id}-{_stable_intent_id(intent)}"
                fills.append(_event(
                    intent, run_id=run_id, status="cancelled", reason=reason,
                    submitted_at=_timestamp_text(intent.submitted_at or intent.decision_at),
                    metadata=metadata,
                ))
            if remaining:
                scheduled[eligible_time] = remaining
            else:
                scheduled.pop(eligible_time, None)

    for event_time in sorted(event_times):
        # Existing orders at this bar settle before decisions made at this time.
        process_due(event_time)
        for timestamp_row in target_events.get(event_time, []):
            for symbol in targets.columns:
                frame = bars.get(symbol)
                if frame is None or frame.empty:
                    warnings.append(f"target skipped for {symbol}: missing bars")
                    continue
                price = _decision_price(frame, event_time)
                if price is None:
                    warnings.append(f"target skipped for {symbol} at {_timestamp_text(event_time)}: missing decision price")
                    continue
                try:
                    weight = float(timestamp_row.get(symbol, 0.0))
                except (TypeError, ValueError):
                    warnings.append(f"target skipped for {symbol} at {_timestamp_text(event_time)}: invalid weight")
                    continue
                if not isfinite(weight):
                    warnings.append(f"target skipped for {symbol} at {_timestamp_text(event_time)}: invalid weight")
                    continue
                if weight < 0.0:
                    warnings.append(f"negative target clipped for {symbol} at {_timestamp_text(event_time)}")
                    weight = 0.0
                target_quantity = max(0.0, weight * config.initial_cash / price)
                current_quantity = max(0.0, held_quantities.get(symbol, 0.0))
                pending_buy_quantity = pending_buys.get(symbol, 0.0)
                pending_sell_quantity = pending_sells.get(symbol, 0.0)
                if target_quantity + 1e-12 < current_quantity + pending_buy_quantity:
                    reason = "target_reversal" if target_quantity < current_quantity else "target_reduced"
                    cancel_pending_orders(symbol, "buy", event_time, reason)
                    pending_buy_quantity = 0.0
                if target_quantity > current_quantity - pending_sell_quantity + 1e-12:
                    reason = "target_reversal" if target_quantity > current_quantity else "target_increased"
                    cancel_pending_orders(symbol, "sell", event_time, reason)
                    pending_sell_quantity = 0.0
                if target_quantity >= current_quantity:
                    delta = max(
                        0.0,
                        target_quantity - current_quantity - pending_buy_quantity,
                    )
                else:
                    delta = -max(
                        0.0,
                        current_quantity - target_quantity - pending_sell_quantity,
                    )
                if abs(delta) <= config.min_order_qty:
                    continue
                health = _health_for_event(config, bars, symbol, event_time)
                if config.pause_on_stale and _health_blocks_order(
                    health,
                    "buy" if delta > 0.0 else "sell",
                    config,
                ):
                    diagnostic = {
                        "type": "strategy_paused",
                        "profile": config.profile,
                        "symbol": symbol,
                        "at": _timestamp_text(event_time),
                        "reason": str((health or {}).get("reason") or "stale_market_data"),
                        "status": str((health or {}).get("status") or "pause"),
                        "age_seconds": (health or {}).get("age_seconds"),
                        "action": "new_entries_blocked" if delta > 0.0 else "exits_blocked",
                    }
                    diagnostics.append(diagnostic)
                    warnings.append(f"strategy paused for {symbol}: {diagnostic['reason']}")
                    continue
                intent = OrderIntent(
                    symbol=symbol,
                    side="buy" if delta > 0.0 else "sell",
                    quantity=abs(delta),
                    decision_at=event_time,
                    decision_price=price,
                    run_id=config.run_id,
                    reason="target_weight",
                    metadata={"target_weight": weight, "target_quantity": target_quantity},
                )
                intents.append(intent)
                eligible_position = _eligible_bar_position(frame, event_time, config.latency_bars)
                if eligible_position is None:
                    unscheduled.append(intent)
                    continue
                eligible_time = frame.index[eligible_position]
                scheduled.setdefault(eligible_time, []).append(intent)
                pending_orders = pending_buys if intent.side == "buy" else pending_sells
                pending_orders[symbol] = pending_orders.get(symbol, 0.0) + intent.quantity
        # latency_bars=0 orders are eligible after their current decision.
        process_due(event_time)

    for intent in sorted(unscheduled, key=_intent_sort_key):
        status = "cancelled" if config.cancel_unfilled else "rejected"
        submitted_at = _timestamp_text(intent.submitted_at or intent.decision_at)
        run_id = intent.run_id or f"{config.run_id}-{_stable_intent_id(intent)}"
        fills.append(_event(
            intent, run_id=run_id, status=status, reason="no_eligible_bar",
            submitted_at=submitted_at,
        ))
    return intents, fills, warnings, diagnostics


def _eligible_bar_position(frame: pd.DataFrame, decision_at: object, latency_bars: int) -> int | None:
    if frame.empty:
        return None
    decision = _timestamp(decision_at)
    positions = np.flatnonzero(frame.index >= decision)
    if len(positions) == 0:
        return None
    position = int(positions[0]) + latency_bars
    return position if position < len(frame) else None


def _intents_from_targets(
    targets: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    config: ExecutionConfig,
) -> tuple[list[OrderIntent], list[str]]:
    """Backward-compatible target intent helper for internal callers."""

    intents, _, warnings, _ = _execute_target_weights(targets, bars, config)
    return intents, warnings


def _decision_price(frame: pd.DataFrame, timestamp: object) -> float | None:
    eligible = frame.loc[frame.index <= _timestamp(timestamp), "close"]
    if eligible.empty:
        return None
    return _valid_price(eligible.iloc[-1])


def _mark_ledger(
    fills: list[FillEvent],
    bars: dict[str, pd.DataFrame],
    config: ExecutionConfig,
) -> tuple[pd.DataFrame, dict[str, PositionState], list[str]]:
    timeline = _timeline(bars)
    if timeline.empty:
        return pd.DataFrame(columns=["cash", "positions_value", "nav", "gross_nav", "gross_return", "net_return", "cost_drag", "turnover", "exposure"]), {}, ["no bars available for mark-to-market"]
    by_time: dict[pd.Timestamp, list[FillEvent]] = {}
    for fill in fills:
        if fill.filled_at is not None and fill.status in {"partial", "filled"}:
            by_time.setdefault(_timestamp(fill.filled_at), []).append(fill)
    symbols = sorted(bars)
    positions = {
        symbol: PositionState(symbol=symbol, quantity=0.0, average_price=0.0)
        for symbol in symbols
    }
    cash = config.initial_cash
    gross_cash = config.initial_cash
    quantities = {symbol: 0.0 for symbol in symbols}
    gross_quantities = {symbol: 0.0 for symbol in symbols}
    rows: list[dict[str, float]] = []
    warnings: list[str] = []
    previous_nav = config.initial_cash
    previous_gross_nav = config.initial_cash
    total_cost = 0.0
    previous_total_cost = 0.0
    for timestamp in timeline:
        for fill in sorted(by_time.get(timestamp, []), key=lambda item: (item.symbol, item.side, item.requested_qty)):
            qty = float(fill.filled_qty)
            price = float(fill.fill_price or 0.0)
            raw_price = float((fill.metadata or {}).get("raw_price") or price)
            if fill.side == "buy":
                cash -= qty * price + fill.fee
                gross_cash -= qty * raw_price
                quantities[fill.symbol] = quantities.get(fill.symbol, 0.0) + qty
                gross_quantities[fill.symbol] = gross_quantities.get(fill.symbol, 0.0) + qty
            else:
                cash += qty * price - fill.fee
                gross_cash += qty * raw_price
                quantities[fill.symbol] = quantities.get(fill.symbol, 0.0) - qty
                gross_quantities[fill.symbol] = gross_quantities.get(fill.symbol, 0.0) - qty
            total_cost += fill.fee + qty * abs(price - raw_price)
            if fill.symbol in positions:
                positions[fill.symbol] = apply_fills(positions[fill.symbol], [fill])
                quantities[fill.symbol] = positions[fill.symbol].quantity

        positions_value = 0.0
        gross_positions_value = 0.0
        exposure_value = 0.0
        for symbol in symbols:
            price = _mark_price(bars[symbol], timestamp)
            if price is None:
                continue
            positions_value += quantities[symbol] * price
            gross_positions_value += gross_quantities[symbol] * price
            exposure_value += abs(quantities[symbol] * price)
        nav = cash + positions_value
        gross_nav = gross_cash + gross_positions_value
        daily_cost_drag = (total_cost - previous_total_cost) / config.initial_cash
        rows.append({
            "cash": cash,
            "positions_value": positions_value,
            "nav": nav,
            "gross_nav": gross_nav,
            "gross_return": gross_nav / previous_gross_nav - 1.0 if previous_gross_nav else 0.0,
            "net_return": nav / previous_nav - 1.0 if previous_nav else 0.0,
            "cost_drag": daily_cost_drag,
            "turnover": exposure_value / config.initial_cash,
            "exposure": exposure_value / nav if nav else 0.0,
        })
        previous_total_cost = total_cost
        previous_nav = nav
        previous_gross_nav = gross_nav
    equity = pd.DataFrame(rows, index=timeline)
    if len(equity) > 1:
        equity["turnover"] = equity["turnover"].diff().abs().fillna(equity["turnover"])
    for symbol, position in positions.items():
        price = _mark_price(bars[symbol], timeline[-1])
        if price is not None:
            unrealized = (price - float(position.average_price or 0.0)) * position.quantity
            positions[symbol] = PositionState(
                symbol=position.symbol, quantity=position.quantity,
                average_price=position.average_price, as_of=_timestamp_text(timeline[-1]),
                realized_pnl=position.realized_pnl, unrealized_pnl=unrealized,
                market_price=price, metadata=position.metadata,
            )
    return equity, positions, warnings


def _timeline(bars: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    indexes = [frame.index for frame in bars.values() if not frame.empty]
    if not indexes:
        return pd.DatetimeIndex([], tz="UTC")
    return pd.DatetimeIndex(sorted(set().union(*(set(index) for index in indexes))), tz="UTC")


def _mark_price(frame: pd.DataFrame, timestamp: object) -> float | None:
    values = frame.loc[frame.index <= _timestamp(timestamp), "close"]
    if values.empty:
        return None
    return _valid_price(values.iloc[-1])


def _fill_to_trade(fill: FillEvent) -> dict[str, Any]:
    row = asdict(fill)
    row["date"] = (fill.filled_at or fill.decision_at or "")[:10]
    row["action"] = f"{fill.side}_{fill.status}"
    row["quantity"] = fill.filled_qty
    return row


def _summary(
    intents: list[OrderIntent],
    fills: list[FillEvent],
    equity: pd.DataFrame,
    config: ExecutionConfig,
) -> dict[str, Any]:
    executable = [fill for fill in fills if fill.status in {"partial", "filled"} and fill.filled_qty > 0]
    fees = float(sum(fill.fee for fill in executable))
    slippage_cost = float(sum(
        fill.filled_qty * abs(float((fill.metadata or {}).get("slippage_per_unit") or fill.slippage))
        for fill in executable
    ))
    spread_cost = float(sum(
        fill.filled_qty * abs(float((fill.metadata or {}).get("spread_per_unit") or 0.0))
        for fill in executable
    ))
    final_nav = float(equity["nav"].iloc[-1]) if not equity.empty else config.initial_cash
    initial_nav = config.initial_cash
    nav_series = equity["nav"] if "nav" in equity else pd.Series(dtype="float64")
    max_drawdown = 0.0
    if not nav_series.empty:
        max_drawdown = float((nav_series / nav_series.cummax() - 1.0).min())
    return {
        "initial_cash": config.initial_cash,
        "final_nav": final_nav,
        "cumulative_return": final_nav / initial_nav - 1.0,
        "gross_return": float(equity["gross_nav"].iloc[-1] / initial_nav - 1.0) if not equity.empty else 0.0,
        "net_return": final_nav / initial_nav - 1.0,
        "max_drawdown": max_drawdown,
        "trade_count": len(executable),
        "fill_count": len(executable),
        "intent_count": len(intents),
        "filled_quantity": float(sum(fill.filled_qty for fill in executable)),
        "requested_quantity": float(sum(fill.requested_qty for fill in fills)),
        "partial_count": sum(fill.status == "partial" for fill in fills),
        "rejected_count": sum(fill.status == "rejected" for fill in fills),
        "cancelled_count": sum(fill.status == "cancelled" for fill in fills),
        "fees": fees,
        "slippage_cost": slippage_cost,
        "spread_cost": spread_cost,
        "total_cost": fees + slippage_cost + spread_cost,
        "cost_drag": (fees + slippage_cost + spread_cost) / initial_nav,
    }


__all__ = [
    "ExecutionConfig",
    "ExecutionResult",
    "apply_fills",
    "execute_intents",
    "execution_defaults",
    "run_execution_backtest",
    "to_jsonable",
]
