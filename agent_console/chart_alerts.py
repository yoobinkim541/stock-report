from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dashboard import chart_conditions


_PRICE_OPERATORS = {
    "crossing",
    "crossing_up",
    "crossing_down",
    "greater_than",
    "less_than",
}


def evaluate_chart_alert(
    rule: dict[str, Any],
    *,
    previous_price: float | int | None,
    current_price: float | int | None,
    previous_values: dict[str, Any] | None = None,
    current_values: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Evaluate a legacy or canonical alert through the shared condition DSL."""
    if str((rule or {}).get("frequency") or "").strip().lower() == "once":
        last_state = (rule or {}).get("last_state") or {}
        if isinstance(last_state, dict) and bool(last_state.get("triggered")):
            return {"triggered": False, "reason": "already_triggered"}

    condition = (rule or {}).get("condition")
    if not isinstance(condition, dict) or not condition:
        return {"triggered": False, "reason": "missing_condition"}
    if any(key in condition and isinstance(condition.get(key), list) and not condition.get(key)
           for key in ("all", "any", "none")):
        return {"triggered": False, "reason": "missing_condition"}

    symbol = str((rule or {}).get("symbol") or "").upper().strip()
    timeframe = str((rule or {}).get("timeframe") or "1d").lower().strip() or "1d"
    previous = dict(previous_values or {})
    current = dict(current_values or {})
    previous.update({"close": previous_price, "price": previous_price})
    current.update({"close": current_price, "price": current_price})
    contexts = {(symbol, timeframe): {
        "previous": previous,
        "current": current,
        "events": current.get("events") if isinstance(current.get("events"), list) else [],
        "as_of": as_of or current.get("time"),
    }}
    evaluation = chart_conditions.evaluate_condition(condition, contexts, now=as_of)
    if not evaluation.get("matched"):
        reason = str(evaluation.get("reason") or "")
        if evaluation.get("status") == "unknown" and (
            "missing" in reason or "not confirmed" in reason or "session mismatch" in reason
        ):
            reason = "missing_price"
        elif evaluation.get("status") == "false":
            reason = "condition_not_met"
        return {"triggered": False, "reason": reason or "condition_not_met"}

    leaves = [row for row in evaluation.get("trace") or [] if row.get("node") == "leaf"]
    matches = [{
        "condition_type": row.get("condition_type"),
        "field": "price" if row.get("condition_type") == "price" else row.get("field"),
        "operator": row.get("operator"),
        "threshold": row.get("threshold"),
        "previous_value": row.get("previous_value"),
        "current_value": row.get("current_value"),
        "as_of": row.get("as_of") or as_of,
        "source": row.get("source") or "",
        "status": row.get("status"),
    } for row in leaves]

    event = _base_event(rule, as_of=as_of)
    root = chart_conditions.normalize_condition(condition)
    event.update({
        "operator": root.get("op") if "op" in root else matches[0].get("operator"),
        "threshold": matches[0].get("threshold") if matches else None,
        "previous_price": _float_or_none(previous_price),
        "current_price": _float_or_none(current_price),
        "condition_count": len(matches),
        "matched_conditions": [_condition_label(item) for item in matches],
        "conditions": matches,
        "evaluation_trace": copy_trace(evaluation.get("trace") or []),
    })
    return {"triggered": True, "event": event}


def copy_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep event payloads JSON-like without exposing evaluator-owned containers."""
    return [dict(row) for row in trace if isinstance(row, dict)]


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

    event = _base_event(rule, as_of=as_of)
    event.update({
        "symbol": str(event.get("symbol") or condition.get("symbol") or "").upper().strip(),
        "operator": operator,
        "threshold": threshold,
        "previous_price": prev,
        "current_price": cur,
        "condition_type": "price",
    })
    return {"triggered": True, "event": event}


def _base_event(rule: dict[str, Any], *, as_of: str | None) -> dict[str, Any]:
    return {
        "alert_id": str((rule or {}).get("id") or ""),
        "name": str((rule or {}).get("name") or ""),
        "symbol": str((rule or {}).get("symbol") or "").upper().strip(),
        "as_of": as_of or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "message": str((rule or {}).get("message") or ""),
    }


def _condition_label(event: dict[str, Any]) -> str:
    ctype = str(event.get("condition_type") or "")
    field = str(event.get("field") or "")
    operator = str(event.get("operator") or "")
    if ctype == "price":
        return f"price:{operator}"
    return f"{ctype}:{field}:{operator}"


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
