"""Evaluate saved chart alert rules against loaded bars.

평가(matched)와 발송(delivery)은 분리한다. 이 모듈은 평가와 "새 이벤트 후보"만 만든다.
- event ID = rule id + 조건 해시(canonical tree) + 근거 봉 timestamp. 같은 봉에서
  다시 평가해도 같은 ID 가 나와 저장소가 중복 발송을 막는다.
- once: 발송 성공 ack 뒤(state.triggered=True)에만 완료. 평가만으로는 소진하지 않는다.
- repeating: 같은 event ID 는 재발송하지 않고, 조건이 false 로 평가된 뒤 다시 true 가
  되는 전이(false→true)에서만 재무장한다. unknown(데이터 불가)은 무장 상태를 바꾸지
  않는다. 연속으로 true 인 봉(예: greater_than 이 계속 참, crossing 직후 반대 교차)은
  새 이벤트가 아니다.
- 지표 정의는 dashboard.chart_studies 공개 함수만 사용한다(자체 계산식 없음).
- 미래 봉(평가 시각 이후 시작)은 제외하고, 진행 중 봉은 confirmed=False 로 표시한다.
  bar_close 조건은 그때 마지막으로 마감된 봉 쌍(context["closed"])을 사용한다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from dashboard import chart_conditions, chart_data_policy, chart_document, chart_studies, chart_workbench


PRICE_FIELDS = frozenset({"open", "high", "low", "close", "volume"})
INDICATOR_FIELDS = frozenset({
    "rsi_14", "macd", "macd_signal", "macd_hist", "vwap", "atr_14", "volume_zscore_20",
})
FUNDAMENTAL_FIELDS = frozenset({
    "per", "forward_pe", "pbr", "psr", "roe", "net_margin", "target_upside_pct",
})
RELATIVE_FIELDS = frozenset({"relative_return_20d", "relative_return_60d", "relative_momentum_20d"})
SUPPORTED_FIELDS: dict[str, frozenset[str]] = {
    "price": PRICE_FIELDS,
    "drawing_line": PRICE_FIELDS,
    "indicator": INDICATOR_FIELDS,
    "fundamental": FUNDAMENTAL_FIELDS,
    "relative_performance": RELATIVE_FIELDS,
}
_OPERAND_FIELDS = PRICE_FIELDS | INDICATOR_FIELDS
# 상대성과의 20d/60d 는 거래일 기준이므로 자산·벤치마크 모두 일봉으로 계산한다.
RELATIVE_TIMEFRAME = "1d"
_DAILY_TIMEFRAMES = frozenset({"1d", "1wk", "1mo"})


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
    reference = _reference_time(as_of)
    events: list[dict[str, Any]] = []
    for rule in rules or []:
        if not bool((rule or {}).get("enabled", True)):
            continue
        try:
            state, event = _evaluate_rule(
                rule,
                bars_by_key,
                reference=reference,
                fundamentals_by_symbol=fundamentals_by_symbol,
                events_by_symbol=events_by_symbol,
            )
        except Exception as exc:
            state = _error_state(rule, str(exc), reference=reference)
            event = None
        if state_sink is not None:
            state_sink.append(state)
        if event is not None:
            events.append(event)
    return events


def canonical_condition(rule: dict[str, Any]) -> dict[str, Any]:
    """The one canonical tree (rule defaults bound into every leaf) shared by all consumers."""
    symbol, timeframe = _rule_symbol_timeframe(rule)
    condition = (rule or {}).get("condition")
    if not isinstance(condition, dict) or not condition:
        raise ValueError("missing_condition")
    return chart_conditions.bind_condition_defaults(
        condition, default_symbol=symbol, default_timeframe=timeframe,
    )


def unsupported_condition_errors(condition: dict[str, Any]) -> list[str]:
    """Fields the alert worker cannot compute or load; such rules must not be saved."""
    try:
        root = chart_conditions.normalize_condition(condition)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []

    def walk(node: dict[str, Any], path: str) -> None:
        if "op" in node:
            for index, child in enumerate(node.get("children") or []):
                walk(child, f"{path}.children[{index}]")
            return
        leaf_type = str(node.get("type") or "")
        supported = SUPPORTED_FIELDS.get(leaf_type)
        if supported is None:
            errors.append(f"{path}.type unsupported for chart alerts: {leaf_type}")
            return
        field = str(node.get("field") or "")
        if field not in supported:
            errors.append(f"{path}.field unsupported for chart alerts: {field}")
        operand = node.get("value")
        if isinstance(operand, dict) and str(operand.get("field") or "") not in _OPERAND_FIELDS:
            errors.append(f"{path}.value.field unsupported for chart alerts: {operand.get('field')}")

    walk(root, "condition")
    return errors


def alert_requirements(rule: dict[str, Any]) -> set[tuple[str, str]]:
    """Every symbol/timeframe bar series the rule needs, including relative-performance benchmarks."""
    canonical = canonical_condition(rule)
    requirements = chart_conditions.condition_requirements(
        canonical, default_symbol="", default_timeframe="",
    )
    for symbol, benchmark in _relative_benchmarks(canonical).items():
        requirements.add((symbol, RELATIVE_TIMEFRAME))
        requirements.add((benchmark, RELATIVE_TIMEFRAME))
    return requirements


def fundamental_symbols(rule: dict[str, Any]) -> set[str]:
    return {
        str(leaf.get("symbol") or "")
        for leaf in _leaves(canonical_condition(rule))
        if leaf.get("type") == "fundamental" and leaf.get("symbol")
    }


def build_condition_contexts(
    rule: dict[str, Any],
    bars_by_key: dict[Any, Any],
    fundamentals_by_symbol: dict[str, Any] | None = None,
    events_by_symbol: dict[str, Any] | None = None,
    *,
    as_of: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build one context per required symbol/timeframe, accepting legacy symbol keys."""
    return _build_contexts(
        canonical_condition(rule), bars_by_key, _reference_time(as_of),
        fundamentals_by_symbol=fundamentals_by_symbol, events_by_symbol=events_by_symbol,
    )


def _build_contexts(canonical, bars_by_key, reference, *, fundamentals_by_symbol, events_by_symbol):
    requirements = chart_conditions.condition_requirements(canonical, default_symbol="", default_timeframe="")
    relative = _relative_values(canonical, bars_by_key, reference)
    contexts: dict[tuple[str, str], dict[str, Any]] = {}
    for key in sorted(requirements):
        context = _context_for_key(
            key,
            _bars_for_key(bars_by_key, key),
            reference,
            fundamentals=_fundamental_values((fundamentals_by_symbol or {}).get(key[0])),
            events=(events_by_symbol or {}).get(key[0]) or [],
            relative=relative.get(key[0]),
        )
        if context is not None:
            contexts[key] = context
    return contexts


def _context_for_key(key, raw, reference, *, fundamentals, events, relative):
    symbol, timeframe = key
    market, zone = _market_timezone(symbol)
    bars = _bars_frame(raw, zone)
    if bars.empty or "close" not in bars.columns:
        return None
    future = int((bars.index > reference).sum())
    bars = bars[bars.index <= reference]
    if len(bars) < 2:
        return None
    indicators = _indicator_frame(bars, zone)
    sessions = _session_labels(bars, market, timeframe)
    event_rows = [dict(event) for event in events if isinstance(event, dict)]

    def pair(position: int) -> dict[str, Any]:
        previous = _bar_values(bars, indicators, position - 1)
        current = _bar_values(bars, indicators, position)
        for values in (previous, current):
            values.update(fundamentals)
            values.update(_relative_at(relative, values["time"], zone))
        return {
            "previous": previous,
            "current": current,
            "events": event_rows,
            "as_of": current["time"],
            "session": sessions[position] if sessions is not None else None,
        }

    context = pair(len(bars) - 1)
    confirmed = bar_close_time(symbol, timeframe, bars.index[-1]) <= reference
    context["confirmed"] = confirmed
    if not confirmed and len(bars) >= 3:
        context["closed"] = {**pair(len(bars) - 2), "confirmed": True}
    context["source"] = {
        "bar_count": len(bars),
        "first": _timestamp_iso(bars.index[0]),
        "future_bars_excluded": future,
        "market": market,
        "timezone": zone,
    }
    return context


def _evaluate_rule(rule, bars_by_key, *, reference, fundamentals_by_symbol, events_by_symbol):
    rule_id = str((rule or {}).get("id") or "")
    symbol, _timeframe = _rule_symbol_timeframe(rule)
    condition = (rule or {}).get("condition")
    if not symbol or not isinstance(condition, dict) or not condition:
        return _error_state(rule, "missing_condition", reference=reference), None
    checked_at = reference.isoformat()
    previous_state = (rule or {}).get("last_state") if isinstance((rule or {}).get("last_state"), dict) else {}
    once = _frequency(rule) == "once"
    if once and bool(previous_state.get("triggered")):
        return {
            **_base_state(rule_id, checked_at, previous_state),
            "status": "suppressed", "reason": "already_triggered", "armed": False,
        }, None

    canonical = canonical_condition(rule)
    unsupported = unsupported_condition_errors(canonical)
    if unsupported:
        return {
            **_base_state(rule_id, checked_at, previous_state),
            "status": "unsupported", "reason": unsupported[0],
            "armed": previous_armed(previous_state),
        }, None

    requirements = alert_requirements(rule)
    contexts = _build_contexts(
        canonical, bars_by_key, reference,
        fundamentals_by_symbol=fundamentals_by_symbol, events_by_symbol=events_by_symbol,
    )
    relative_keys = requirements - chart_conditions.condition_requirements(
        canonical, default_symbol="", default_timeframe="",
    )
    missing = [
        {"symbol": required_symbol, "timeframe": required_timeframe}
        for required_symbol, required_timeframe in sorted(requirements)
        if (required_symbol, required_timeframe) not in contexts
        and not ((required_symbol, required_timeframe) in relative_keys
                 and _has_bars(bars_by_key, (required_symbol, required_timeframe), reference))
    ]
    timestamps = {
        f"{required_symbol}:{required_timeframe}": context.get("as_of")
        for (required_symbol, required_timeframe), context in sorted(contexts.items())
    }
    evaluation = chart_conditions.evaluate_condition(canonical, contexts, now=checked_at)
    status = str(evaluation.get("status") or "unknown")
    matched = status == "true"
    armed_previous = previous_armed(previous_state)
    if status == "true":
        armed = False
    elif status == "false":
        armed = True
    else:
        armed = armed_previous
    condition_hash = _condition_hash(canonical)
    state = {
        **_base_state(rule_id, checked_at, previous_state),
        "matched": matched,
        "status": status,
        "reason": str(evaluation.get("reason") or ""),
        "trace": [dict(row) for row in evaluation.get("trace") or [] if isinstance(row, dict)],
        "missing_contexts": missing,
        "source_timestamps": timestamps,
        "condition_hash": condition_hash,
        "explanation": chart_conditions.explain_condition(canonical),
        "armed": armed,
    }
    if not matched:
        return state, None
    if not once and not armed_previous:
        state["suppressed"] = "awaiting_rearm"
        return state, None
    event = _event_payload(rule, canonical, evaluation, contexts, checked_at)
    event["event_id"] = _event_id(rule_id, condition_hash, state["trace"])
    event["condition_hash"] = condition_hash
    event["frequency"] = "once" if once else "repeating"
    state["event"] = event
    return state, event


def _base_state(rule_id: str, checked_at: str, previous_state: dict[str, Any]) -> dict[str, Any]:
    state = {
        "rule_id": rule_id,
        "last_checked_at": checked_at,
        "matched": False,
        "reason": "",
        "trace": [],
        "missing_contexts": [],
        "source_timestamps": {},
        # once 완료 여부. 발송 성공 ack 뒤 worker 만 True 로 바꾼다.
        "triggered": bool(previous_state.get("triggered")),
    }
    for key in ("pending_event_id", "delivered_event_id"):
        if previous_state.get(key):
            state[key] = previous_state[key]
    return state


def previous_armed(previous_state: dict[str, Any]) -> bool:
    if "armed" in previous_state:
        return bool(previous_state.get("armed"))
    # 무장 필드가 없던 이전 상태: 직전 평가가 matched 였다면 같은 연속 구간으로 본다.
    return not bool(previous_state.get("matched"))


def _event_id(rule_id: str, condition_hash: str, trace: list[dict[str, Any]]) -> str:
    basis = sorted({
        f"{row.get('symbol')}:{row.get('timeframe')}@{row.get('as_of')}|{row.get('operand_as_of') or ''}"
        for row in trace if row.get("node") == "leaf"
    })
    raw = json.dumps({"rule": rule_id, "condition": condition_hash, "bars": basis}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _condition_hash(canonical: dict[str, Any]) -> str:
    raw = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _event_payload(rule, canonical, evaluation, contexts, checked_at):
    symbol, timeframe = _rule_symbol_timeframe(rule)
    primary = contexts.get((symbol, timeframe), {})
    previous = primary.get("previous") if isinstance(primary.get("previous"), dict) else {}
    current = primary.get("current") if isinstance(primary.get("current"), dict) else {}
    leaves = [row for row in evaluation.get("trace") or [] if row.get("node") == "leaf"]
    conditions = [{
        "condition_type": row.get("condition_type"), "field": row.get("field"),
        "operator": row.get("operator"), "threshold": row.get("threshold"),
        "previous_value": row.get("previous_value"), "current_value": row.get("current_value"),
        "status": row.get("status"), "symbol": row.get("symbol"), "timeframe": row.get("timeframe"),
        "as_of": row.get("as_of"),
    } for row in leaves]
    return {
        "alert_id": str((rule or {}).get("id") or ""),
        "name": str((rule or {}).get("name") or ""),
        "symbol": symbol,
        "as_of": checked_at,
        "bar_time": current.get("time"),
        "message": str((rule or {}).get("message") or ""),
        "operator": canonical.get("op") if "op" in canonical else (conditions[0].get("operator") if conditions else ""),
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


def _leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    if "op" in node:
        return [leaf for child in node.get("children") or [] for leaf in _leaves(child)]
    return [node]


def _relative_benchmarks(canonical: dict[str, Any]) -> dict[str, str]:
    benchmarks: dict[str, str] = {}
    for leaf in _leaves(canonical):
        if leaf.get("type") != "relative_performance" or not leaf.get("symbol"):
            continue
        symbol = str(leaf["symbol"])
        benchmark = str(leaf.get("benchmark") or "").upper().strip() or chart_workbench.benchmark_symbol({"symbol": symbol})
        if benchmarks.setdefault(symbol, benchmark) != benchmark:
            raise ValueError(f"conflicting relative_performance benchmarks for {symbol}")
    return benchmarks


def _relative_values(canonical, bars_by_key, reference) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol, benchmark in _relative_benchmarks(canonical).items():
        _market, zone = _market_timezone(symbol)
        asset = _daily_close(bars_by_key, (symbol, RELATIVE_TIMEFRAME), reference, zone)
        bench = _daily_close(bars_by_key, (benchmark, RELATIVE_TIMEFRAME), reference, zone)
        if asset is None or bench is None:
            continue
        out[symbol] = chart_studies.relative_performance(asset, bench)
    return out


def _daily_close(bars_by_key, key, reference, zone) -> pd.Series | None:
    frame = _bars_frame(_bars_for_key(bars_by_key, key), zone)
    if frame.empty or "close" not in frame.columns:
        return None
    frame = frame[frame.index <= reference]
    if frame.empty:
        return None
    close = frame["close"].copy()
    close.index = close.index.tz_convert(zone)
    return close


def _relative_at(relative: pd.DataFrame | None, time_text: str | None, zone: str) -> dict[str, float]:
    if relative is None or relative.empty or not time_text:
        return {}
    day = pd.Timestamp(time_text).tz_convert(zone).tz_localize(None).normalize()
    rows = relative.loc[relative.index <= day]
    if rows.empty:
        return {}
    latest = rows.iloc[-1]
    return {str(name): float(value) for name, value in latest.items() if _float_or_none(value) is not None}


def _has_bars(bars_by_key, key, reference) -> bool:
    _market, zone = _market_timezone(key[0])
    frame = _bars_frame(_bars_for_key(bars_by_key, key), zone)
    return not frame.empty and bool((frame.index <= reference).any())


def _bars_for_key(bars_by_key: dict[Any, Any], key: tuple[str, str]):
    source = bars_by_key or {}
    if key in source:
        return source[key]
    if key[0] in source:
        return source[key[0]]
    joined = f"{key[0]}:{key[1]}"
    return source.get(joined)


def _bar_values(bars: pd.DataFrame, indicators: pd.DataFrame, position: int) -> dict[str, Any]:
    values: dict[str, Any] = {"time": _timestamp_iso(bars.index[position])}
    for frame in (indicators, bars):
        for key, value in frame.iloc[position].items():
            number = _float_or_none(value)
            if number is not None:
                values[str(key).lower()] = number
    return values


def _indicator_frame(bars: pd.DataFrame, zone: str) -> pd.DataFrame:
    """Causal indicator columns from chart_studies; a row only depends on bars up to it."""
    out = pd.DataFrame(index=bars.index)
    close = bars["close"]
    out["rsi_14"] = chart_studies.rsi(close, 14)
    line, signal, histogram = chart_studies.macd(close)
    out["macd"], out["macd_signal"], out["macd_hist"] = line, signal, histogram
    columns = set(bars.columns)
    if {"high", "low", "close", "volume"} <= columns:
        out["vwap"] = chart_studies.session_vwap(bars, zone)
    if {"high", "low", "close"} <= columns:
        out["atr_14"] = chart_studies.atr(bars, 14)
    if "volume" in columns:
        out["volume_zscore_20"] = chart_studies.volume_zscore(bars["volume"], 20)
    return out


def _session_labels(bars: pd.DataFrame, market: str, timeframe: str) -> list[str] | None:
    """Regular/extended label per intraday bar from the shared session policy; None for daily bars."""
    if timeframe in _DAILY_TIMEFRAMES:
        return None
    result = chart_data_policy.apply_session_policy(bars, market=market, timeframe=timeframe, policy="regular")
    if result.metadata.get("decision") != "regular_session_filtered":
        return None
    regular = set(pd.DatetimeIndex(result.frame.index).tz_convert("UTC"))
    return ["regular" if ts in regular else "extended" for ts in bars.index]


def bar_close_time(symbol: str, timeframe: str, start: Any) -> pd.Timestamp:
    """UTC time at which the bar starting at ``start`` is closed (intraday: start + bar length,
    daily: exchange close of that local date, weekly/monthly: start of the next period)."""
    market, zone = _market_timezone(symbol)
    start = pd.Timestamp(start)
    # tz 없는 시각은 _bars_frame 과 같이 거래소 현지 벽시계로 본다.
    start = start.tz_localize(zone) if start.tzinfo is None else start
    seconds = chart_data_policy.bar_seconds(timeframe)
    if seconds:
        return (start + pd.Timedelta(seconds=seconds)).tz_convert("UTC")
    day = start.tz_convert(zone).normalize()
    if timeframe == "1wk":
        end = day + pd.Timedelta(days=7)
    elif timeframe == "1mo":
        end = day + pd.offsets.MonthBegin(1)
    else:
        closing = chart_data_policy.apply_session_policy(
            None, market=market, timeframe="1d", policy="regular",
        ).metadata["exchange_session"]["close"]
        hours, minutes = (int(part) for part in closing.split(":"))
        end = day + pd.Timedelta(hours=hours, minutes=minutes)
    return end.tz_convert("UTC")


def _market_timezone(symbol: str) -> tuple[str, str]:
    document = chart_document.default_chart_document(symbol)
    return str(document["market"]), str(document["timezone"])


def _rule_symbol_timeframe(rule: dict[str, Any]) -> tuple[str, str]:
    symbol = str((rule or {}).get("symbol") or "").upper().strip()
    timeframe = str((rule or {}).get("timeframe") or "1d").lower().strip() or "1d"
    return symbol, timeframe


def _frequency(rule: dict[str, Any]) -> str:
    return str((rule or {}).get("frequency") or "").lower().strip()


def _fundamental_values(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    values: dict[str, Any] = {}
    for key, value in payload.items():
        number = _float_or_none(value)
        if number is not None:
            values[str(key)] = number
    return values


def _reference_time(as_of: Any) -> pd.Timestamp:
    if as_of in (None, ""):
        return pd.Timestamp(datetime.now(timezone.utc)).floor("s")
    ts = pd.Timestamp(as_of)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _error_state(rule, reason: str, *, reference: pd.Timestamp) -> dict[str, Any]:
    previous_state = (rule or {}).get("last_state") if isinstance((rule or {}).get("last_state"), dict) else {}
    return {
        **_base_state(str((rule or {}).get("id") or ""), reference.isoformat(), previous_state),
        "status": "error",
        "reason": reason,
        "armed": previous_armed(previous_state),
    }


def _bars_frame(raw: Any, zone: str) -> pd.DataFrame:
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
    values = frame.pop(time_col) if time_col else frame.index
    try:
        index = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce"))
    except (TypeError, ValueError):
        # 서로 다른 UTC offset 이 섞인 문자열은 UTC 로 통일해 해석한다.
        index = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce", utc=True))
    # tz 없는 시각은 거래소 현지 벽시계로 본다(chart_data_policy 와 같은 가정).
    if index.tz is None:
        index = index.tz_localize(zone, ambiguous="NaT", nonexistent="NaT")
    frame.index = index.tz_convert("UTC")
    frame = frame[frame.index.notna()].sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    for col in ("open", "high", "low", "close", "volume"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["close"]) if "close" in frame.columns else frame


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
    if isinstance(value, bool):
        return None
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None
