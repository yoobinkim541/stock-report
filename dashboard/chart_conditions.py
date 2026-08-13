"""Shared, auditable condition tree for charts, scans, backtests, and trading rules."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from numbers import Real
from typing import Any


_GROUP_OPS = frozenset({"all", "any", "none"})
_LEAF_TYPES = frozenset({
    "price", "indicator", "fundamental", "relative_performance",
    "drawing_line", "event", "portfolio",
})
_OPERATORS = frozenset({
    "crossing", "crossing_up", "crossing_down",
    "greater_than", "less_than", "greater_or_equal", "less_or_equal",
    "equal", "not_equal", "between", "outside",
    "change_greater_than", "change_less_than", "happened_within",
})
_SESSION_POLICIES = frozenset({"regular", "extended", "all"})
_CONFIRMATIONS = frozenset({"bar_close", "intrabar"})
_UNIT_ALIASES = {
    "minutes": "minute", "minute": "minute", "min": "minute", "m": "minute",
    "hours": "hour", "hour": "hour", "h": "hour",
    "days": "day", "day": "day", "d": "day",
    "bars": "bar", "bar": "bar",
    "percent": "percent", "%": "percent", "absolute": "absolute",
}


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normalize_leaf(raw: Mapping[str, Any]) -> dict[str, Any]:
    leaf = copy.deepcopy(dict(raw))
    leaf["type"] = str(leaf.get("type") or "price").strip().lower()
    leaf["operator"] = str(leaf.get("operator") or "").strip().lower()
    if leaf["operator"] == "in_range":
        leaf["operator"] = "between"
    if leaf["operator"] == "out_of_range":
        leaf["operator"] = "outside"
    symbol = str(leaf.get("symbol") or "").strip().upper()
    timeframe = str(leaf.get("timeframe") or "").strip().lower()
    if symbol:
        leaf["symbol"] = symbol
    else:
        leaf.pop("symbol", None)
    if timeframe:
        leaf["timeframe"] = timeframe
    else:
        leaf.pop("timeframe", None)
    if not leaf.get("field"):
        leaf["field"] = "close" if leaf["type"] in {"price", "drawing_line"} else ""
    else:
        leaf["field"] = str(leaf["field"]).strip()
    leaf["session"] = str(leaf.get("session") or "regular").strip().lower()
    leaf["confirmation"] = str(leaf.get("confirmation") or "bar_close").strip().lower()
    if "unit" in leaf:
        unit = str(leaf.get("unit") or "").strip().lower()
        leaf["unit"] = _UNIT_ALIASES.get(unit, unit)
    if isinstance(leaf.get("value"), Mapping):
        operand = copy.deepcopy(dict(leaf["value"]))
        if operand.get("symbol"):
            operand["symbol"] = str(operand["symbol"]).strip().upper()
        if operand.get("timeframe"):
            operand["timeframe"] = str(operand["timeframe"]).strip().lower()
        leaf["value"] = operand
    return leaf


def normalize_condition(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate legacy boolean maps into one canonical group/leaf tree."""
    if not isinstance(raw, Mapping):
        raise ValueError("condition must be an object")
    for legacy_op in ("all", "any", "none"):
        if legacy_op in raw:
            children = raw.get(legacy_op)
            if not isinstance(children, list):
                raise ValueError(f"condition.{legacy_op} must be a list")
            return {"op": legacy_op, "children": [normalize_condition(child) for child in children]}
    if "op" in raw or "children" in raw:
        op = str(raw.get("op") or "").strip().lower()
        children = raw.get("children")
        if not isinstance(children, list):
            raise ValueError("condition.children must be a list")
        return {"op": op, "children": [normalize_condition(child) for child in children]}
    return _normalize_leaf(raw)


def validate_condition(condition: Mapping[str, Any]) -> list[str]:
    try:
        root = normalize_condition(condition)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []

    def walk(node: Mapping[str, Any], path: str) -> None:
        if "op" in node:
            op = node.get("op")
            children = node.get("children")
            if op not in _GROUP_OPS:
                errors.append(f"{path}.op unsupported: {op}")
            if not isinstance(children, list) or not children:
                errors.append(f"{path}.children must not be empty")
                return
            for index, child in enumerate(children):
                walk(child, f"{path}.children[{index}]")
            return

        leaf_type = node.get("type")
        operator = node.get("operator")
        if leaf_type not in _LEAF_TYPES:
            errors.append(f"{path}.type unsupported: {leaf_type}")
        if operator not in _OPERATORS:
            errors.append(f"{path}.operator unsupported: {operator}")
        if node.get("session") not in _SESSION_POLICIES:
            errors.append(f"{path}.session unsupported: {node.get('session')}")
        if node.get("confirmation") not in _CONFIRMATIONS:
            errors.append(f"{path}.confirmation unsupported: {node.get('confirmation')}")
        if leaf_type not in {"drawing_line", "event"} and not str(node.get("field") or ""):
            errors.append(f"{path}.field is required")
        if operator in {"between", "outside"}:
            value = node.get("value")
            if not isinstance(value, (list, tuple)) or len(value) != 2 or any(_number(item) is None for item in value):
                errors.append(f"{path}.value must contain two finite bounds")
        elif operator == "happened_within":
            if _number(node.get("window")) is None or float(node.get("window") or 0) <= 0:
                errors.append(f"{path}.window must be positive")
            if node.get("unit") not in {"minute", "hour", "day", "bar"}:
                errors.append(f"{path}.unit unsupported: {node.get('unit')}")
        elif leaf_type == "drawing_line":
            for key in ("x0", "x1"):
                if _timestamp(node.get(key)) is None:
                    errors.append(f"{path}.{key} must be a timestamp")
            for key in ("y0", "y1"):
                if _number(node.get(key)) is None:
                    errors.append(f"{path}.{key} must be finite")
        else:
            value = node.get("value")
            if isinstance(value, Mapping):
                if not str(value.get("field") or ""):
                    errors.append(f"{path}.value.field is required")
            elif _number(value) is None:
                errors.append(f"{path}.value must be finite")
        if node.get("expires_at") not in (None, "") and _timestamp(node.get("expires_at")) is None:
            errors.append(f"{path}.expires_at must be a timestamp")

    walk(root, "condition")
    return errors


def condition_requirements(condition: Mapping[str, Any], *, default_symbol: str,
                           default_timeframe: str) -> set[tuple[str, str]]:
    root = normalize_condition(condition)
    requirements: set[tuple[str, str]] = set()
    default_symbol = str(default_symbol or "").upper().strip()
    default_timeframe = str(default_timeframe or "1d").lower().strip() or "1d"

    def add_operand(operand: Mapping[str, Any]) -> None:
        requirements.add((
            str(operand.get("symbol") or default_symbol).upper().strip(),
            str(operand.get("timeframe") or default_timeframe).lower().strip(),
        ))

    def walk(node: Mapping[str, Any]) -> None:
        if "op" in node:
            for child in node.get("children") or []:
                walk(child)
            return
        add_operand(node)
        if isinstance(node.get("value"), Mapping):
            add_operand(node["value"])

    walk(root)
    return requirements


def _leaf_explanation(leaf: Mapping[str, Any]) -> str:
    symbol = str(leaf.get("symbol") or "DEFAULT")
    timeframe = str(leaf.get("timeframe") or "default")
    field = str(leaf.get("field") or leaf.get("type") or "value")
    operator = str(leaf.get("operator") or "")
    if operator == "happened_within":
        target = f"{leaf.get('window')} {leaf.get('unit')}"
    elif leaf.get("type") == "drawing_line":
        target = "drawing line"
    elif isinstance(leaf.get("value"), Mapping):
        value = leaf["value"]
        target = f"{value.get('symbol') or 'DEFAULT'} {value.get('timeframe') or 'default'} {value.get('field')}"
    else:
        target = str(leaf.get("value"))
    return f"{symbol} {timeframe} {field} {operator} {target}"


def explain_condition(condition: Mapping[str, Any]) -> str:
    root = normalize_condition(condition)

    def explain(node: Mapping[str, Any]) -> str:
        if "op" not in node:
            return _leaf_explanation(node)
        children = "; ".join(explain(child) for child in node.get("children") or [])
        return f"{str(node.get('op')).upper()}({children})"

    return explain(root)


def _normalized_contexts(contexts: Mapping[Any, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    normalized: dict[tuple[str, str], Mapping[str, Any]] = {}
    for key, context in dict(contexts or {}).items():
        if not isinstance(key, tuple) or len(key) != 2 or not isinstance(context, Mapping):
            continue
        normalized[(str(key[0]).upper().strip(), str(key[1]).lower().strip())] = context
    return normalized


def _context_for(leaf: Mapping[str, Any], contexts: Mapping[tuple[str, str], Mapping[str, Any]]):
    symbol = str(leaf.get("symbol") or "").upper().strip()
    timeframe = str(leaf.get("timeframe") or "").lower().strip()
    if symbol and timeframe:
        return (symbol, timeframe), contexts.get((symbol, timeframe))
    if len(contexts) == 1:
        key = next(iter(contexts))
        if symbol and symbol != key[0]:
            return (symbol, timeframe or key[1]), None
        if timeframe and timeframe != key[1]:
            return (symbol or key[0], timeframe), None
        return key, contexts[key]
    candidates = [
        (key, context) for key, context in contexts.items()
        if (not symbol or key[0] == symbol) and (not timeframe or key[1] == timeframe)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return (symbol, timeframe), None


def _threshold_values(value: Any, contexts, current_context) -> tuple[Any, Any, str | None]:
    if not isinstance(value, Mapping):
        return value, value, None
    key, context = _context_for(value, contexts)
    if context is None:
        return None, None, f"missing context: {key[0]} {key[1]}"
    field = str(value.get("field") or "")
    previous = context.get("previous") if isinstance(context.get("previous"), Mapping) else {}
    current = context.get("current") if isinstance(context.get("current"), Mapping) else {}
    return previous.get(field), current.get(field), None


def _drawing_threshold(leaf: Mapping[str, Any], current_time: Any) -> float | None:
    target = _timestamp(current_time)
    x0, x1 = _timestamp(leaf.get("x0")), _timestamp(leaf.get("x1"))
    y0, y1 = _number(leaf.get("y0")), _number(leaf.get("y1"))
    if target is None or x0 is None or x1 is None or y0 is None or y1 is None:
        return None
    denominator = (x1 - x0).total_seconds()
    if denominator == 0:
        return y1
    ratio = (target - x0).total_seconds() / denominator
    return y0 + (y1 - y0) * ratio


def _event_status(leaf: Mapping[str, Any], context: Mapping[str, Any], now_value: Any):
    events = context.get("events") or []
    if not isinstance(events, list):
        return "unknown", "missing events", None
    reference = _timestamp(now_value or context.get("as_of"))
    if reference is None:
        return "unknown", "missing as_of", None
    field = str(leaf.get("field") or "").strip().lower()
    window = _number(leaf.get("window"))
    unit = str(leaf.get("unit") or "")
    if window is None:
        return "unknown", "missing event window", None
    if unit == "bar":
        matched = any(
            (not field or str(event.get("kind") or "").lower() == field)
            and _number(event.get("bars_ago")) is not None
            and float(event.get("bars_ago")) <= window
            for event in events if isinstance(event, Mapping)
        )
    else:
        multiplier = {"minute": 60, "hour": 3600, "day": 86400}.get(unit)
        if multiplier is None:
            return "unknown", "unsupported event unit", None
        start = reference - timedelta(seconds=window * multiplier)
        matched = False
        for event in events:
            if not isinstance(event, Mapping):
                continue
            if field and str(event.get("kind") or "").lower() != field:
                continue
            event_time = _timestamp(event.get("date") or event.get("timestamp"))
            if event_time is not None and start <= event_time <= reference:
                matched = True
                break
    return ("true", "event happened within window", True) if matched else ("false", "event not in window", False)


def _comparison_status(operator: str, previous: Any, current: Any,
                       threshold_previous: Any, threshold_current: Any, unit: str):
    prev, cur = _number(previous), _number(current)
    threshold_prev, threshold_cur = _number(threshold_previous), _number(threshold_current)
    if operator in {"between", "outside"}:
        if cur is None or not isinstance(threshold_current, (list, tuple)) or len(threshold_current) != 2:
            return "unknown", "missing value", None
        low, high = _number(threshold_current[0]), _number(threshold_current[1])
        if low is None or high is None:
            return "unknown", "missing value", None
        inside = min(low, high) <= cur <= max(low, high)
        matched = inside if operator == "between" else not inside
        return ("true" if matched else "false"), ("range matched" if matched else "range not matched"), max(low, high)
    if cur is None or threshold_cur is None:
        return "unknown", "missing value", threshold_cur
    if operator.startswith("crossing") and (prev is None or threshold_prev is None):
        return "unknown", "missing previous value", threshold_cur
    if operator == "crossing":
        matched = (prev < threshold_prev and cur >= threshold_cur) or (prev > threshold_prev and cur <= threshold_cur)
    elif operator == "crossing_up":
        matched = prev < threshold_prev and cur >= threshold_cur
    elif operator == "crossing_down":
        matched = prev > threshold_prev and cur <= threshold_cur
    elif operator == "greater_than":
        matched = cur > threshold_cur
    elif operator == "less_than":
        matched = cur < threshold_cur
    elif operator == "greater_or_equal":
        matched = cur >= threshold_cur
    elif operator == "less_or_equal":
        matched = cur <= threshold_cur
    elif operator == "equal":
        matched = cur == threshold_cur
    elif operator == "not_equal":
        matched = cur != threshold_cur
    elif operator in {"change_greater_than", "change_less_than"}:
        if prev is None:
            return "unknown", "missing previous value", threshold_cur
        change = cur - prev
        if unit == "percent":
            if prev == 0:
                return "unknown", "zero previous value", threshold_cur
            change = change / abs(prev) * 100.0
        matched = change > threshold_cur if operator == "change_greater_than" else change < threshold_cur
    else:
        return "unknown", f"unsupported operator: {operator}", threshold_cur
    return ("true" if matched else "false"), ("condition matched" if matched else "condition not met"), threshold_cur


def evaluate_condition(condition, contexts, *, now: str | None = None) -> dict[str, Any]:
    """Evaluate a normalized tree with three-valued logic and a per-node trace."""
    try:
        root = normalize_condition(condition)
    except ValueError as exc:
        return {"matched": False, "status": "unknown", "reason": str(exc), "trace": []}
    errors = validate_condition(root)
    if errors:
        return {"matched": False, "status": "unknown", "reason": errors[0], "trace": []}
    normalized_contexts = _normalized_contexts(contexts)
    trace: list[dict[str, Any]] = []

    def leaf_result(leaf: Mapping[str, Any], path: str) -> tuple[str, str]:
        key, context = _context_for(leaf, normalized_contexts)
        base = {
            "node": "leaf", "path": path, "condition_type": leaf.get("type"),
            "symbol": key[0], "timeframe": key[1], "field": leaf.get("field"),
            "operator": leaf.get("operator"),
        }
        if context is None:
            reason = f"missing context: {key[0]} {key[1]}"
            trace.append({**base, "status": "unknown", "matched": False, "reason": reason})
            return "unknown", reason
        expires = _timestamp(leaf.get("expires_at"))
        reference = _timestamp(now or context.get("as_of")) or datetime.now(timezone.utc)
        if expires is not None and reference > expires:
            trace.append({**base, "status": "false", "matched": False, "reason": "condition expired"})
            return "false", "condition expired"
        if leaf.get("confirmation") == "bar_close" and context.get("confirmed") is False:
            trace.append({**base, "status": "unknown", "matched": False, "reason": "bar not confirmed"})
            return "unknown", "bar not confirmed"
        if context.get("session") and leaf.get("session") != "all" and context.get("session") != leaf.get("session"):
            trace.append({**base, "status": "unknown", "matched": False, "reason": "session mismatch"})
            return "unknown", "session mismatch"
        if leaf.get("operator") == "happened_within":
            status, reason, matched = _event_status(leaf, context, now)
            trace.append({**base, "status": status, "matched": bool(matched), "reason": reason, "as_of": context.get("as_of")})
            return status, reason

        previous_values = context.get("previous") if isinstance(context.get("previous"), Mapping) else {}
        current_values = context.get("current") if isinstance(context.get("current"), Mapping) else {}
        field = str(leaf.get("field") or "close")
        previous, current = previous_values.get(field), current_values.get(field)
        if leaf.get("type") == "drawing_line":
            threshold = _drawing_threshold(leaf, current_values.get("time") or context.get("as_of") or now)
            threshold_previous = threshold
            threshold_current = threshold
        else:
            threshold_previous, threshold_current, error = _threshold_values(
                leaf.get("value"), normalized_contexts, context,
            )
            if error:
                trace.append({**base, "status": "unknown", "matched": False, "reason": error})
                return "unknown", error
        status, reason, threshold = _comparison_status(
            str(leaf.get("operator")), previous, current,
            threshold_previous, threshold_current, str(leaf.get("unit") or "absolute"),
        )
        trace.append({
            **base, "status": status, "matched": status == "true", "reason": reason,
            "previous_value": _number(previous), "current_value": _number(current),
            "threshold": threshold, "as_of": context.get("as_of"),
            "source": leaf.get("source") or leaf.get("id") or "",
        })
        return status, reason

    def walk(node: Mapping[str, Any], path: str) -> tuple[str, str]:
        if "op" not in node:
            return leaf_result(node, path)
        outcomes = [walk(child, f"{path}.children[{index}]") for index, child in enumerate(node["children"])]
        statuses = [status for status, _reason in outcomes]
        op = str(node["op"])
        if op == "all":
            status = "false" if "false" in statuses else ("unknown" if "unknown" in statuses else "true")
        elif op == "any":
            status = "true" if "true" in statuses else ("unknown" if "unknown" in statuses else "false")
        else:
            status = "false" if "true" in statuses else ("unknown" if "unknown" in statuses else "true")
        if status == "unknown":
            reason = next((reason for child_status, reason in outcomes if child_status == "unknown"), "unknown child")
        else:
            reason = f"{op} group {'matched' if status == 'true' else 'not matched'}"
        trace.append({
            "node": "group", "path": path, "op": op, "status": status,
            "matched": status == "true", "reason": reason, "child_statuses": statuses,
        })
        return status, reason

    status, reason = walk(root, "condition")
    return {"matched": status == "true", "status": status, "reason": reason, "trace": trace}
