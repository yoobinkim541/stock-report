from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_PRICE_OPERATORS = {
    "crossing",
    "crossing_up",
    "crossing_down",
    "greater_than",
    "less_than",
}


def evaluate_price_alert(
    rule: dict[str, Any],
    *,
    previous_price: float | int | None,
    current_price: float | int | None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Evaluate a persisted chart price alert against previous/current prices."""
    condition = dict((rule or {}).get("condition") or {})
    operator = str(condition.get("operator") or "").strip().lower()
    if operator not in _PRICE_OPERATORS:
        return {"triggered": False, "reason": "unsupported_operator"}
    if str((rule or {}).get("frequency") or "").strip().lower() == "once":
        last_state = (rule or {}).get("last_state") or {}
        if isinstance(last_state, dict) and bool(last_state.get("triggered")):
            return {"triggered": False, "reason": "already_triggered"}

    threshold = _float_or_none(condition.get("value"))
    prev = _float_or_none(previous_price)
    cur = _float_or_none(current_price)
    if threshold is None or prev is None or cur is None:
        return {"triggered": False, "reason": "missing_price"}

    triggered = _price_condition_met(operator, prev, cur, threshold)
    if not triggered:
        return {"triggered": False, "reason": "condition_not_met"}

    event = {
        "alert_id": str((rule or {}).get("id") or ""),
        "name": str((rule or {}).get("name") or ""),
        "symbol": str((rule or {}).get("symbol") or condition.get("symbol") or "").upper().strip(),
        "operator": operator,
        "threshold": threshold,
        "previous_price": prev,
        "current_price": cur,
        "as_of": as_of or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message": str((rule or {}).get("message") or ""),
    }
    return {"triggered": True, "event": event}


def _price_condition_met(operator: str, previous_price: float, current_price: float, threshold: float) -> bool:
    if operator == "crossing":
        return (previous_price < threshold <= current_price) or (previous_price > threshold >= current_price)
    if operator == "crossing_up":
        return previous_price < threshold <= current_price
    if operator == "crossing_down":
        return previous_price > threshold >= current_price
    if operator == "greater_than":
        return current_price > threshold
    if operator == "less_than":
        return current_price < threshold
    return False


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
