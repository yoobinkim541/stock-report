"""Deterministic bar-based execution simulator and position ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .contracts import FillEvent, OrderIntent, PositionState
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

    @property
    def ledger(self) -> list[FillEvent]:
        """Alias used by callers that refer to the fill stream as a ledger."""

        return self.fills

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "intents": [asdict(intent) for intent in self.intents],
            "fills": [asdict(fill) for fill in self.fills],
            "positions": {symbol: asdict(position) for symbol, position in self.positions.items()},
            "equity": self.equity.copy(),
            "trades": [dict(trade) for trade in self.trades],
            "summary": dict(self.summary),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "ok": self.ok,
        }


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
    ordered = sorted(
        enumerate(intents),
        key=lambda item: (_timestamp(item[1].decision_at), item[1].symbol, item[0]),
    )
    fills: list[FillEvent] = []
    for sequence, (_, intent) in enumerate(ordered):
        frame = normalized.get(intent.symbol.upper().strip())
        decision_at = _timestamp_text(intent.decision_at)
        submitted_at = _timestamp_text(intent.submitted_at or intent.decision_at)
        run_id = intent.run_id or f"{config.run_id}-{sequence:06d}"
        if _is_cancelled(intent):
            fills.append(_event(intent, run_id=run_id, status="cancelled", reason="cancelled_by_strategy", submitted_at=submitted_at))
            continue
        if frame is None:
            fills.append(_event(intent, run_id=run_id, status="rejected", reason="missing_bars", submitted_at=submitted_at))
            continue
        if intent.quantity <= config.min_order_qty:
            fills.append(_event(intent, run_id=run_id, status="rejected", reason="quantity_below_minimum", submitted_at=submitted_at))
            continue
        eligible = _eligible_bar(frame, intent.decision_at, config.latency_bars)
        if eligible is None:
            fills.append(_event(intent, run_id=run_id, status="rejected", reason="no_eligible_bar", submitted_at=submitted_at))
            continue
        bar_time, row = eligible
        base_price, reason = _eligible_price(intent, row, config.cancel_unfilled)
        accepted_at = _timestamp_text(bar_time)
        if base_price is None:
            status = "cancelled" if config.cancel_unfilled else "rejected"
            fills.append(
                _event(
                    intent, run_id=run_id, status=status, reason=reason,
                    submitted_at=submitted_at, accepted_at=accepted_at,
                )
            )
            continue

        cap = _liquidity_cap(row["volume"], config.max_participation_rate)
        if cap <= 0.0:
            fills.append(
                _event(
                    intent, run_id=run_id, status="rejected", reason="insufficient_liquidity",
                    submitted_at=submitted_at, accepted_at=accepted_at,
                )
            )
            continue
        if intent.quantity > cap and not config.partial_fill:
            fills.append(
                _event(
                    intent, run_id=run_id, status="rejected", reason="insufficient_liquidity",
                    submitted_at=submitted_at, accepted_at=accepted_at,
                )
            )
            continue

        filled_qty = min(float(intent.quantity), cap)
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
        })
        fills.append(
            _event(
                intent, run_id=run_id, status=status, reason=reason,
                filled_at=_timestamp_text(bar_time), submitted_at=submitted_at,
                accepted_at=accepted_at, filled_qty=filled_qty, fill_price=fill_price,
                fee=fee, slippage=slippage_per_unit, metadata=metadata,
            )
        )
    return fills


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
    intents, warnings = _intents_from_targets(targets, normalized, config)
    fills = execute_intents(intents, normalized, config)
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


def _intents_from_targets(
    targets: pd.DataFrame,
    bars: dict[str, pd.DataFrame],
    config: ExecutionConfig,
) -> tuple[list[OrderIntent], list[str]]:
    desired = {symbol: 0.0 for symbol in targets.columns}
    warnings: list[str] = []
    intents: list[OrderIntent] = []
    for timestamp, row in targets.iterrows():
        for symbol in targets.columns:
            frame = bars.get(symbol)
            if frame is None or frame.empty:
                warnings.append(f"target skipped for {symbol}: missing bars")
                continue
            price = _decision_price(frame, timestamp)
            if price is None:
                warnings.append(f"target skipped for {symbol} at {_timestamp_text(timestamp)}: missing decision price")
                continue
            try:
                weight = float(row.get(symbol, 0.0))
            except (TypeError, ValueError):
                warnings.append(f"target skipped for {symbol} at {_timestamp_text(timestamp)}: invalid weight")
                continue
            if not isfinite(weight):
                warnings.append(f"target skipped for {symbol} at {_timestamp_text(timestamp)}: invalid weight")
                continue
            if not config.allow_short and weight < 0.0:
                warnings.append(f"negative target clipped for {symbol} at {_timestamp_text(timestamp)}")
                weight = 0.0
            target_quantity = weight * config.initial_cash / price
            delta = target_quantity - desired[symbol]
            if abs(delta) <= config.min_order_qty:
                desired[symbol] = target_quantity
                continue
            side = "buy" if delta > 0.0 else "sell"
            quantity = abs(delta)
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    decision_at=timestamp,
                    decision_price=price,
                    run_id=config.run_id,
                    reason="target_weight",
                    metadata={"target_weight": weight, "target_quantity": target_quantity},
                )
            )
            desired[symbol] = target_quantity
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
                try:
                    positions[fill.symbol] = apply_fills(positions[fill.symbol], [fill])
                except ValueError as exc:
                    warnings.append(str(exc))

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
]
