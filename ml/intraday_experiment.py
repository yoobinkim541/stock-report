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
        bad_count = sum(1 for label in labels if label.quality_label == "bad")
        if bad_count >= self.max_bad_labels:
            reasons.append("loss_cluster")
        if len(decisions) > self.max_turnover:
            reasons.append("excess_turnover")
        if not reasons:
            return {"action": "allow", "reasons": []}
        return {"action": "size_down", "reasons": reasons}
