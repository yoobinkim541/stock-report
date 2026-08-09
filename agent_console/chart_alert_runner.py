from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

import pandas as pd

from dashboard import chart_conditions


def evaluate_alert_rules(
    rules: list[dict[str, Any]],
    bars_by_key: dict[Any, Any],
    *,
    as_of: str | None = None,
    fundamentals_by_symbol: dict[str, Any] | None = None,
    events_by_symbol: dict[str, Any] | None = None,
    state_sink: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate rules independently and optionally collect complete audit states."""
    events: list[dict[str, Any]] = []
    for rule in rules or []:
        if not bool((rule or {}).get("enabled", True)):
            continue
        try:
            state, event = _evaluate_rule(
                rule,
                bars_by_key,
                as_of=as_of,
                fundamentals_by_symbol=fundamentals_by_symbol,
                events_by_symbol=events_by_symbol,
            )
        except Exception as exc:
            state = _error_state(rule, str(exc), as_of=as_of)
            event = None
        if state_sink is not None:
            state_sink.append(state)
        if event is not None:
            events.append(event)
    return events


def build_condition_contexts(
    rule: dict[str, Any],
    bars_by_key: dict[Any, Any],
    fundamentals_by_symbol: dict[str, Any] | None = None,
    events_by_symbol: dict[str, Any] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build one context per required symbol/timeframe, accepting legacy symbol keys."""
    symbol = str((rule or {}).get("symbol") or "").upper().strip()
    timeframe = str((rule or {}).get("timeframe") or "1d").lower().strip() or "1d"
    condition = (rule or {}).get("condition") if isinstance((rule or {}).get("condition"), dict) else {}
    requirements = chart_conditions.condition_requirements(
        condition, default_symbol=symbol, default_timeframe=timeframe,
    )
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    for key in sorted(requirements):
        raw = _bars_for_key(bars_by_key, key)
        bars = _bars_frame(raw)
        if len(bars) < 2 or "close" not in bars.columns:
            continue
        previous = bars.iloc[-2]
        current = bars.iloc[-1]
        previous_values = _indicator_values(bars.iloc[:-1], previous.name)
        current_values = _indicator_values(bars, current.name)
        previous_values.update(_row_values(previous))
        current_values.update(_row_values(current))
        fundamental_values = _fundamental_values((fundamentals_by_symbol or {}).get(key[0]))
        previous_values.update(fundamental_values)
        current_values.update(fundamental_values)
        symbol_events = (events_by_symbol or {}).get(key[0]) or []
        contexts[key] = {
            "previous": previous_values,
            "current": current_values,
            "events": [dict(event) for event in symbol_events if isinstance(event, dict)],
            "as_of": current_values.get("time"),
            "confirmed": True,
            "source": {"bar_count": len(bars), "first": _timestamp_iso(bars.index[0])},
        }
    return contexts


def _evaluate_rule(rule, bars_by_key, *, as_of, fundamentals_by_symbol, events_by_symbol):
    rule_id = str((rule or {}).get("id") or "")
    symbol = str((rule or {}).get("symbol") or "").upper().strip()
    timeframe = str((rule or {}).get("timeframe") or "1d").lower().strip() or "1d"
    condition = (rule or {}).get("condition")
    if not symbol or not isinstance(condition, dict) or not condition:
        return _error_state(rule, "missing_condition", as_of=as_of), None
    requirements = chart_conditions.condition_requirements(
        condition, default_symbol=symbol, default_timeframe=timeframe,
    )
    contexts = build_condition_contexts(
        rule,
        bars_by_key,
        fundamentals_by_symbol=fundamentals_by_symbol,
        events_by_symbol=events_by_symbol,
    )
    missing = [
        {"symbol": required_symbol, "timeframe": required_timeframe}
        for required_symbol, required_timeframe in sorted(requirements - set(contexts))
    ]
    timestamps = {
        f"{required_symbol}:{required_timeframe}": context.get("as_of")
        for (required_symbol, required_timeframe), context in sorted(contexts.items())
    }
    checked_at = as_of or _latest_timestamp(timestamps.values()) or datetime.now(timezone.utc).isoformat(timespec="seconds")
    previous_state = (rule or {}).get("last_state") if isinstance((rule or {}).get("last_state"), dict) else {}
    if str((rule or {}).get("frequency") or "").lower().strip() == "once" and bool(previous_state.get("triggered")):
        return {
            "rule_id": rule_id, "last_checked_at": checked_at, "matched": False,
            "reason": "already_triggered", "trace": [], "missing_contexts": missing,
            "source_timestamps": timestamps, "triggered": True,
        }, None

    evaluation = chart_conditions.evaluate_condition(condition, contexts, now=as_of)
    state = {
        "rule_id": rule_id,
        "last_checked_at": checked_at,
        "matched": bool(evaluation.get("matched")),
        "reason": str(evaluation.get("reason") or ""),
        "trace": [dict(row) for row in evaluation.get("trace") or [] if isinstance(row, dict)],
        "missing_contexts": missing,
        "source_timestamps": timestamps,
        "triggered": bool(evaluation.get("matched")),
    }
    if not evaluation.get("matched"):
        return state, None
    event = _event_payload(rule, condition, evaluation, contexts, checked_at)
    state["event"] = event
    return state, event


def _event_payload(rule, condition, evaluation, contexts, checked_at):
    symbol = str((rule or {}).get("symbol") or "").upper().strip()
    timeframe = str((rule or {}).get("timeframe") or "1d").lower().strip() or "1d"
    primary = contexts.get((symbol, timeframe), {})
    previous = primary.get("previous") if isinstance(primary.get("previous"), dict) else {}
    current = primary.get("current") if isinstance(primary.get("current"), dict) else {}
    leaves = [row for row in evaluation.get("trace") or [] if row.get("node") == "leaf"]
    conditions = [{
        "condition_type": row.get("condition_type"), "field": row.get("field"),
        "operator": row.get("operator"), "threshold": row.get("threshold"),
        "previous_value": row.get("previous_value"), "current_value": row.get("current_value"),
        "status": row.get("status"), "symbol": row.get("symbol"), "timeframe": row.get("timeframe"),
    } for row in leaves]
    root = chart_conditions.normalize_condition(condition)
    return {
        "alert_id": str((rule or {}).get("id") or ""),
        "name": str((rule or {}).get("name") or ""),
        "symbol": symbol,
        "as_of": checked_at,
        "message": str((rule or {}).get("message") or ""),
        "operator": root.get("op") if "op" in root else (conditions[0].get("operator") if conditions else ""),
        "threshold": conditions[0].get("threshold") if conditions else None,
        "previous_price": _float_or_none(previous.get("close")),
        "current_price": _float_or_none(current.get("close")),
        "condition_count": len(conditions),
        "matched_conditions": [_condition_label(row) for row in conditions],
        "conditions": conditions,
        "indicator_values": {
            key: value for key, value in current.items()
            if key not in {"time", "open", "high", "low", "close", "volume"}
        },
    }


def _condition_label(row: dict[str, Any]) -> str:
    condition_type = str(row.get("condition_type") or "")
    operator = str(row.get("operator") or "")
    if condition_type == "price":
        return f"price:{operator}"
    return f"{condition_type}:{row.get('field') or ''}:{operator}"


def _bars_for_key(bars_by_key: dict[Any, Any], key: tuple[str, str]):
    source = bars_by_key or {}
    if key in source:
        return source[key]
    if key[0] in source:
        return source[key[0]]
    joined = f"{key[0]}:{key[1]}"
    return source.get(joined)


def _row_values(row: pd.Series) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in row.items():
        number = _float_or_none(value)
        if number is not None:
            values[str(key).lower()] = number
    return values


def _fundamental_values(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    values: dict[str, Any] = {}
    for key, value in payload.items():
        number = _float_or_none(value)
        if number is not None:
            values[str(key)] = number
        elif isinstance(value, dict):
            for nested_key, nested_value in value.items():
                nested_number = _float_or_none(nested_value)
                if nested_number is not None:
                    values[str(nested_key)] = nested_number
    rows = payload.get("quarterly") or payload.get("history") or []
    if isinstance(rows, list) and rows:
        latest = next((row for row in reversed(rows) if isinstance(row, dict)), None)
        if latest:
            for key, value in latest.items():
                number = _float_or_none(value)
                if number is not None:
                    values[str(key)] = number
    return values


def _latest_timestamp(values) -> str | None:
    timestamps = [pd.Timestamp(value) for value in values if value]
    return max(timestamps).isoformat() if timestamps else None


def _error_state(rule, reason: str, *, as_of: str | None) -> dict[str, Any]:
    return {
        "rule_id": str((rule or {}).get("id") or ""),
        "last_checked_at": as_of or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "matched": False,
        "reason": reason,
        "trace": [],
        "missing_contexts": [],
        "source_timestamps": {},
        "triggered": bool(((rule or {}).get("last_state") or {}).get("triggered")),
    }


def _bars_frame(raw: Any) -> pd.DataFrame:
    if isinstance(raw, pd.DataFrame):
        frame = raw.copy()
    elif isinstance(raw, list):
        frame = pd.DataFrame([row for row in raw if isinstance(row, dict)])
    elif isinstance(raw, dict):
        rows = raw.get("rows")
        frame = pd.DataFrame(rows if isinstance(rows, list) else [])
    else:
        return pd.DataFrame()
    if frame.empty:
        return pd.DataFrame()

    frame.columns = [str(col).lower().strip().replace(" ", "_") for col in frame.columns]
    time_col = next((name for name in ("time", "timestamp", "date", "datetime") if name in frame.columns), None)
    if time_col:
        frame.index = pd.to_datetime(frame.pop(time_col), errors="coerce", utc=True)
    else:
        frame.index = pd.to_datetime(frame.index, errors="coerce", utc=True)
    frame = frame[frame.index.notna()].sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["close"]) if "close" in frame.columns else frame


def _indicator_values(bars: pd.DataFrame, timestamp: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"time": _timestamp_iso(timestamp)}
    if bars is None or bars.empty or "close" not in bars.columns:
        return values
    close = pd.to_numeric(bars["close"], errors="coerce")
    rsi = _rsi(close, 14)
    if not rsi.empty and pd.notna(rsi.iloc[-1]):
        values["rsi_14"] = float(rsi.iloc[-1])
    macd, signal, hist = _macd(close)
    if not macd.empty and pd.notna(macd.iloc[-1]):
        values["macd"] = float(macd.iloc[-1])
    if not signal.empty and pd.notna(signal.iloc[-1]):
        values["macd_signal"] = float(signal.iloc[-1])
    if not hist.empty and pd.notna(hist.iloc[-1]):
        values["macd_hist"] = float(hist.iloc[-1])
    vwap = _vwap(bars)
    if not vwap.empty and pd.notna(vwap.iloc[-1]):
        values["vwap"] = float(vwap.iloc[-1])
    vol_z = _volume_zscore(bars, 20)
    if not vol_z.empty and pd.notna(vol_z.iloc[-1]):
        values["volume_zscore_20"] = float(vol_z.iloc[-1])
    return values


def _macd(series: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    series = pd.to_numeric(series, errors="coerce")
    fast = series.ewm(span=12, adjust=False, min_periods=12).mean()
    slow = series.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = fast - slow
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    return macd, signal, macd - signal


def _vwap(bars: pd.DataFrame) -> pd.Series:
    if not {"high", "low", "close", "volume"} <= set(bars.columns):
        return pd.Series(dtype="float64")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    close = pd.to_numeric(bars["close"], errors="coerce")
    volume = pd.to_numeric(bars["volume"], errors="coerce")
    typical = (high + low + close) / 3.0
    return (typical * volume).cumsum() / volume.cumsum().replace(0, pd.NA)


def _volume_zscore(bars: pd.DataFrame, period: int) -> pd.Series:
    if "volume" not in bars.columns:
        return pd.Series(dtype="float64")
    volume = pd.to_numeric(bars["volume"], errors="coerce")
    mean = volume.rolling(period, min_periods=period).mean()
    std = volume.rolling(period, min_periods=period).std().replace(0, pd.NA)
    return (volume - mean) / std


def _rsi(series: pd.Series, period: int) -> pd.Series:
    series = pd.to_numeric(series, errors="coerce")
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def _timestamp_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.isoformat()
    except Exception:
        return str(value)


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
