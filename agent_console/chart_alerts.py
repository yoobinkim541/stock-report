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


def evaluate_chart_alert(
    rule: dict[str, Any],
    *,
    previous_price: float | int | None,
    current_price: float | int | None,
    previous_values: dict[str, Any] | None = None,
    current_values: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Evaluate price, indicator, drawing-line, or all-of chart alert conditions."""
    if str((rule or {}).get("frequency") or "").strip().lower() == "once":
        last_state = (rule or {}).get("last_state") or {}
        if isinstance(last_state, dict) and bool(last_state.get("triggered")):
            return {"triggered": False, "reason": "already_triggered"}

    condition = dict((rule or {}).get("condition") or {})
    leaves = _condition_leaves(condition)
    if not leaves:
        return {"triggered": False, "reason": "missing_condition"}

    matches: list[dict[str, Any]] = []
    for leaf in leaves:
        outcome = _evaluate_condition_leaf(
            leaf,
            previous_price=previous_price,
            current_price=current_price,
            previous_values=previous_values or {},
            current_values=current_values or {},
            as_of=as_of,
        )
        if not outcome.get("triggered"):
            return {"triggered": False, "reason": outcome.get("reason") or "condition_not_met"}
        matches.append(outcome["event"])

    event = _base_event(rule, as_of=as_of)
    event.update({
        "operator": matches[0].get("operator") if len(matches) == 1 else "all",
        "threshold": matches[0].get("threshold"),
        "previous_price": _float_or_none(previous_price),
        "current_price": _float_or_none(current_price),
        "condition_count": len(matches),
        "matched_conditions": [_condition_label(item) for item in matches],
        "conditions": matches,
    })
    return {"triggered": True, "event": event}


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


def _evaluate_condition_leaf(
    condition: dict[str, Any],
    *,
    previous_price: float | int | None,
    current_price: float | int | None,
    previous_values: dict[str, Any],
    current_values: dict[str, Any],
    as_of: str | None,
) -> dict[str, Any]:
    ctype = str(condition.get("type") or "price").strip().lower()
    operator = str(condition.get("operator") or "").strip().lower()
    if ctype == "price":
        return _evaluate_threshold_condition(
            condition,
            condition_type="price",
            field="price",
            previous_value=previous_price,
            current_value=current_price,
            threshold=condition.get("value"),
            operator=operator,
            as_of=as_of,
        )
    if ctype == "indicator":
        field = str(condition.get("field") or "").strip()
        return _evaluate_threshold_condition(
            condition,
            condition_type="indicator",
            field=field,
            previous_value=previous_values.get(field),
            current_value=current_values.get(field),
            threshold=condition.get("value"),
            operator=operator,
            as_of=as_of,
        )
    if ctype == "drawing_line":
        threshold = _interpolated_line_threshold(condition, current_values.get("time") or as_of)
        return _evaluate_threshold_condition(
            condition,
            condition_type="drawing_line",
            field="price",
            previous_value=previous_price,
            current_value=current_price,
            threshold=threshold,
            operator=operator,
            as_of=as_of,
        )
    return {"triggered": False, "reason": "unsupported_condition"}


def _evaluate_threshold_condition(
    condition: dict[str, Any],
    *,
    condition_type: str,
    field: str,
    previous_value: Any,
    current_value: Any,
    threshold: Any,
    operator: str,
    as_of: str | None,
) -> dict[str, Any]:
    if operator not in _PRICE_OPERATORS:
        return {"triggered": False, "reason": "unsupported_operator"}
    prev = _float_or_none(previous_value)
    cur = _float_or_none(current_value)
    val = _float_or_none(threshold)
    if prev is None or cur is None or val is None:
        return {"triggered": False, "reason": "missing_price"}
    if not _price_condition_met(operator, prev, cur, val):
        return {"triggered": False, "reason": "condition_not_met"}
    return {
        "triggered": True,
        "event": {
            "condition_type": condition_type,
            "field": field,
            "operator": operator,
            "threshold": val,
            "previous_value": prev,
            "current_value": cur,
            "as_of": as_of or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": condition.get("source") or condition.get("id") or "",
        },
    }


def _condition_leaves(condition: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(condition, dict):
        return []
    if isinstance(condition.get("all"), list):
        return [dict(item) for item in condition.get("all") or [] if isinstance(item, dict)]
    return [condition]


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


def _interpolated_line_threshold(condition: dict[str, Any], current_time: Any) -> float | None:
    t = _timestamp_seconds(current_time)
    x0 = _timestamp_seconds(condition.get("x0"))
    x1 = _timestamp_seconds(condition.get("x1"))
    y0 = _float_or_none(condition.get("y0"))
    y1 = _float_or_none(condition.get("y1"))
    if t is None or x0 is None or x1 is None or y0 is None or y1 is None:
        return None
    if x0 == x1:
        return y1
    ratio = (t - x0) / (x1 - x0)
    return y0 + (y1 - y0) * ratio


def _timestamp_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


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
