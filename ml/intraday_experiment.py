from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DecisionSnapshot:
    id: str
    timestamp: str
    symbol: str
    session_phase: str
    features: dict[str, Any]
    signals: dict[str, Any]
    decision: str
    position_context: dict[str, Any]
    expected_edge: float
    risk_budget: float
    cost_estimate: float
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeLabel:
    decision_id: str
    horizon: str
    realized_return: float
    max_adverse_excursion: float
    max_favorable_excursion: float
    slippage: float
    fees: float
    stop_hit: bool
    take_profit_hit: bool
    quality_label: str
    pending_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleOutcomeLabel:
    decision_id: str
    realized_r: float
    partial_target_hit: bool
    partial_stop_hit: bool
    full_target_hit: bool
    full_stop_hit: bool
    time_to_first_exit_min: int | None
    time_to_final_exit_min: int | None
    max_adverse_r: float
    max_favorable_r: float
    quality_label: str
    pending_reason: str = ""
    exit_legs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _price_at(prices: list[dict], minute: int) -> float | None:
    for row in prices:
        if int(row.get("minute", -1)) == minute:
            price = _as_price(row.get("price"))
            if price is not None:
                return price
    return None


def _path_returns(entry: float, prices: list[dict], horizon: int) -> list[float]:
    rows = [row for row in prices if int(row.get("minute", -1)) <= horizon]
    returns: list[float] = []
    for row in rows:
        price = _as_price(row.get("price"))
        if price is not None:
            returns.append((price / entry) - 1)
    return returns


def _pending_labels(decision: DecisionSnapshot, horizons: tuple[int, ...],
                    reason: str) -> list[OutcomeLabel]:
    return [OutcomeLabel(
        decision_id=decision.id,
        horizon=f"{horizon}m",
        realized_return=0.0,
        max_adverse_excursion=0.0,
        max_favorable_excursion=0.0,
        slippage=0.0,
        fees=0.0,
        stop_hit=False,
        take_profit_hit=False,
        quality_label="pending",
        pending_reason=reason,
    ) for horizon in horizons]


def label_shadow_decision(decision: DecisionSnapshot, prices: list[dict], *,
                          horizons: tuple[int, ...] = (5, 15, 30)) -> list[OutcomeLabel]:
    if decision.decision not in {"long", "short"}:
        raise ValueError("decision must be 'long' or 'short'")
    entry = _as_price(decision.features.get("price")) or _price_at(prices, 0)
    if entry is None:
        return _pending_labels(decision, horizons, "missing_entry_price")
    labels: list[OutcomeLabel] = []
    side = -1.0 if decision.decision == "short" else 1.0
    for horizon in horizons:
        exit_price = _price_at(prices, horizon)
        if exit_price is None:
            labels.extend(_pending_labels(decision, (horizon,), "missing_price"))
            continue
        gross = ((float(exit_price) / entry) - 1) * side
        net = gross - float(decision.cost_estimate or 0.0)
        path = [ret * side for ret in _path_returns(entry, prices, horizon)]
        mae = min(path) if path else 0.0
        mfe = max(path) if path else 0.0
        labels.append(OutcomeLabel(
            decision_id=decision.id,
            horizon=f"{horizon}m",
            realized_return=net,
            max_adverse_excursion=mae,
            max_favorable_excursion=mfe,
            slippage=float(decision.cost_estimate or 0.0) / 2,
            fees=float(decision.cost_estimate or 0.0) / 2,
            stop_hit=mae <= -abs(float(decision.risk_budget or 0.0)),
            take_profit_hit=mfe >= abs(float(decision.expected_edge or 0.0)) * 2,
            quality_label="good" if net > 0 else "bad",
        ))
    return labels


def _bar_from_row(row: dict) -> dict | None:
    c = _as_price(row.get("price") if row.get("price") is not None else row.get("close"))
    if c is None:
        return None
    high = _as_price(row.get("high"))
    low = _as_price(row.get("low"))
    return {"h": high if high is not None else c, "l": low if low is not None else c, "c": c}


def label_lifecycle_decision(decision: DecisionSnapshot, prices: list[dict], *,
                             cfg: dict | None = None) -> LifecycleOutcomeLabel:
    """Label a decision using the live intraday lifecycle policy.

    This is the meta-label target for "would this signal survive our actual
    partial-exit policy?", not a plain horizon return.
    """
    if decision.decision != "long":
        raise ValueError("lifecycle labels currently support long decisions only")
    entry = _as_price(decision.features.get("price")) or _price_at(prices, 0)
    if entry is None:
        return LifecycleOutcomeLabel(
            decision_id=decision.id,
            realized_r=0.0,
            partial_target_hit=False,
            partial_stop_hit=False,
            full_target_hit=False,
            full_stop_hit=False,
            time_to_first_exit_min=None,
            time_to_final_exit_min=None,
            max_adverse_r=0.0,
            max_favorable_r=0.0,
            quality_label="pending",
            pending_reason="missing_entry_price",
        )
    rows = sorted(prices, key=lambda row: int(row.get("minute", 0)))
    risk = abs(float(decision.risk_budget or 0.0)) or abs(float(decision.expected_edge or 0.0)) or entry * 0.01
    qty = int((decision.position_context or {}).get("qty") or 10)
    pos = {
        "ticker": decision.symbol,
        "qty": qty,
        "initial_qty": qty,
        "entry_price": entry,
        "risk_per_share": risk,
        "stop": entry - risk,
        "target": entry + 2.0 * risk,
        "entry_min": 0,
    }
    from ml.intraday_lifecycle import apply_filled_leg, evaluate_exit_plan, initialize_lifecycle

    initialize_lifecycle(pos, cfg or {})
    legs: list[dict[str, Any]] = []
    first_exit: int | None = None
    final_exit: int | None = None
    mae_r = 0.0
    mfe_r = 0.0
    for row in rows:
        minute = int(row.get("minute", 0) or 0)
        if minute <= 0:
            continue
        bar = _bar_from_row(row)
        if bar is None:
            continue
        mae_r = min(mae_r, (float(bar["l"]) - entry) / risk)
        mfe_r = max(mfe_r, (float(bar["h"]) - entry) / risk)
        for leg in evaluate_exit_plan(pos, bar, None, minute, 10_000, cfg or {}):
            leg = {**leg, "minute": minute}
            legs.append(leg)
            first_exit = minute if first_exit is None else first_exit
            if leg.get("final"):
                final_exit = minute
                break
            apply_filled_leg(pos, leg, cfg or {})
        if final_exit is not None:
            break
    if not legs:
        return LifecycleOutcomeLabel(
            decision_id=decision.id,
            realized_r=0.0,
            partial_target_hit=False,
            partial_stop_hit=False,
            full_target_hit=False,
            full_stop_hit=False,
            time_to_first_exit_min=None,
            time_to_final_exit_min=None,
            max_adverse_r=round(mae_r, 4),
            max_favorable_r=round(mfe_r, 4),
            quality_label="pending",
            pending_reason="no_exit",
        )
    realized_r = 0.0
    for leg in legs:
        sign = -1.0 if leg["reason"] in {"stop", "partial_stop", "signal_collapse", "timestop"} else 1.0
        leg_qty = int(leg.get("qty") or 0)
        realized_r += sign * leg_qty / max(qty, 1) * (
            1.0 if leg["reason"] in {"partial_stop", "stop"} else
            1.0 if leg["reason"] == "partial_target" else
            2.0 if leg["reason"] == "target" else 0.0
        )
    return LifecycleOutcomeLabel(
        decision_id=decision.id,
        realized_r=round(realized_r - float(decision.cost_estimate or 0.0), 4),
        partial_target_hit=any(leg["reason"] == "partial_target" for leg in legs),
        partial_stop_hit=any(leg["reason"] == "partial_stop" for leg in legs),
        full_target_hit=any(leg["reason"] == "target" for leg in legs),
        full_stop_hit=any(leg["reason"] == "stop" for leg in legs),
        time_to_first_exit_min=first_exit,
        time_to_final_exit_min=final_exit,
        max_adverse_r=round(mae_r, 4),
        max_favorable_r=round(mfe_r, 4),
        quality_label="good" if realized_r > 0 else "bad",
        exit_legs=legs,
    )


class RiskGovernor:
    def __init__(self, *, max_bad_labels: int = 3, max_turnover: int = 20) -> None:
        self.max_bad_labels = int(max_bad_labels)
        self.max_turnover = int(max_turnover)

    def assess(self, decisions: list[DecisionSnapshot], labels: list[OutcomeLabel], *,
               data_fresh: bool = True) -> dict:
        reasons: list[str] = []
        if not data_fresh:
            reasons.append("stale_data")
            return {"action": "shadow_only", "reasons": reasons}
        latest_labels: dict[str, tuple[int, OutcomeLabel]] = {}
        for label in labels:
            horizon = str(label.horizon or "").lower().removesuffix("m")
            try:
                horizon_order = int(horizon)
            except ValueError:
                horizon_order = -1
            current = latest_labels.get(label.decision_id)
            if current is None or horizon_order >= current[0]:
                latest_labels[label.decision_id] = (horizon_order, label)

        ordered_decisions: list[DecisionSnapshot] = []
        seen_decision_ids: set[str] = set()
        for decision in sorted(decisions, key=lambda item: item.timestamp):
            if decision.id in seen_decision_ids:
                continue
            seen_decision_ids.add(decision.id)
            ordered_decisions.append(decision)

        consecutive_losses = 0
        for decision in reversed(ordered_decisions):
            latest = latest_labels.get(decision.id)
            if latest is None or latest[1].quality_label != "bad":
                break
            consecutive_losses += 1
        if consecutive_losses >= self.max_bad_labels:
            reasons.append("loss_cluster")
        if len(decisions) > self.max_turnover:
            reasons.append("excess_turnover")
        if not reasons:
            return {"action": "allow", "reasons": []}
        return {"action": "size_down", "reasons": reasons}
