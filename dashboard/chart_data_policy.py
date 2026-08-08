"""Session filtering and provenance metadata for chart OHLCV data."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ohlc_utils import normalize_ohlc_frame


@dataclass(frozen=True)
class SessionResult:
    frame: pd.DataFrame
    metadata: dict[str, Any]


_MARKET_DEFAULTS = {
    "us": ("America/New_York", time(9, 30), time(16, 0)),
    "kr": ("Asia/Seoul", time(9, 0), time(15, 30)),
}
_DAILY_TIMEFRAMES = frozenset({"1d", "1wk", "1mo"})
_SESSION_POLICIES = frozenset({"regular", "extended", "all"})
_EXPLICIT_FRESHNESS = frozenset({"realtime", "delayed", "stale", "unknown"})


def _normalized_market(market: str) -> str:
    value = str(market or "us").lower().strip()
    if value not in _MARKET_DEFAULTS:
        raise ValueError(f"unsupported market: {market}")
    return value


def _normalized_timeframe(timeframe: str) -> str:
    return str(timeframe or "1d").lower().strip() or "1d"


def _is_intraday(timeframe: str) -> bool:
    return _normalized_timeframe(timeframe) not in _DAILY_TIMEFRAMES


def _exchange_timezone(market: str, value: str | None = None) -> ZoneInfo:
    market = _normalized_market(market)
    try:
        return ZoneInfo(value or _MARKET_DEFAULTS[market][0])
    except Exception as exc:
        raise ValueError(f"invalid timezone: {value}") from exc


def _provider_timezone(hist: pd.DataFrame) -> ZoneInfo | None:
    attrs = getattr(hist, "attrs", {}) or {}
    for key in ("provider_timezone", "source_timezone", "timezone"):
        value = attrs.get(key)
        if not value:
            continue
        try:
            return ZoneInfo(str(value))
        except Exception:
            continue
    return None


def _localized_index(hist: pd.DataFrame, exchange_timezone: ZoneInfo) -> tuple[pd.DatetimeIndex, bool, str | None]:
    index = pd.DatetimeIndex(hist.index)
    if index.tz is not None:
        return index.tz_convert(exchange_timezone), False, str(index.tz)
    provider_timezone = _provider_timezone(hist)
    if provider_timezone is not None:
        return index.tz_localize(provider_timezone).tz_convert(exchange_timezone), False, str(provider_timezone)
    return index.tz_localize(exchange_timezone), True, None


def _session_metadata(*, market: str, timeframe: str, policy: str, timezone_value: ZoneInfo,
                      input_bars: int, excluded_bars: int, timezone_assumption: bool,
                      decision: str, provider_timezone: str | None) -> dict[str, Any]:
    _default_timezone, opening, closing = _MARKET_DEFAULTS[market]
    return {
        "market": market,
        "timeframe": timeframe,
        "policy": policy,
        "timezone": timezone_value.key,
        "provider_timezone": provider_timezone,
        "timezone_assumption": timezone_assumption,
        "decision": decision,
        "exchange_session": {"open": opening.strftime("%H:%M"), "close": closing.strftime("%H:%M")},
        "input_bars": input_bars,
        "excluded_bars": excluded_bars,
        "included_bars": input_bars - excluded_bars,
        "provider_coverage": "regular_only" if policy == "regular" else "may_be_incomplete",
        # A calendar is intentionally not guessed from weekdays alone.
        "holiday_calendar": "not_configured",
    }


def apply_session_policy(hist, *, market: str, timeframe: str, policy: str,
                         timezone: str | None = None) -> SessionResult:
    """Return a copied, exchange-timezone frame filtered by the requested session policy."""
    market = _normalized_market(market)
    timeframe = _normalized_timeframe(timeframe)
    policy = str(policy or "regular").lower().strip() or "regular"
    if policy not in _SESSION_POLICIES:
        raise ValueError(f"unsupported session policy: {policy}")
    exchange_timezone = _exchange_timezone(market, timezone)

    if hist is None:
        empty = pd.DataFrame()
        return SessionResult(empty, _session_metadata(
            market=market, timeframe=timeframe, policy=policy, timezone_value=exchange_timezone,
            input_bars=0, excluded_bars=0, timezone_assumption=False,
            decision="no_data", provider_timezone=None,
        ))

    frame = normalize_ohlc_frame(hist)
    frame = frame.copy(deep=True)
    input_bars = len(frame)
    if not _is_intraday(timeframe):
        return SessionResult(frame, _session_metadata(
            market=market, timeframe=timeframe, policy=policy, timezone_value=exchange_timezone,
            input_bars=input_bars, excluded_bars=0, timezone_assumption=False,
            decision="timeframe_bypass", provider_timezone=None,
        ))

    index, timezone_assumption, provider_timezone = _localized_index(frame, exchange_timezone)
    if policy != "regular":
        return SessionResult(frame, _session_metadata(
            market=market, timeframe=timeframe, policy=policy, timezone_value=exchange_timezone,
            input_bars=input_bars, excluded_bars=0, timezone_assumption=timezone_assumption,
            decision="provider_bars_retained", provider_timezone=provider_timezone,
        ))

    frame.index = index
    _default_timezone, opening, closing = _MARKET_DEFAULTS[market]
    local_times = frame.index.time
    weekdays = frame.index.dayofweek < 5
    within_session = (local_times >= opening) & (local_times <= closing)
    mask = weekdays & within_session
    filtered = frame.loc[mask].copy(deep=True)
    return SessionResult(filtered, _session_metadata(
        market=market, timeframe=timeframe, policy=policy, timezone_value=exchange_timezone,
        input_bars=input_bars, excluded_bars=input_bars - len(filtered),
        timezone_assumption=timezone_assumption, decision="regular_session_filtered",
        provider_timezone=provider_timezone,
    ))


def _bar_seconds(timeframe: str) -> int | None:
    value = _normalized_timeframe(timeframe)
    if value in _DAILY_TIMEFRAMES:
        return None
    match = re.fullmatch(r"(\d+)(m|h)", value)
    if not match:
        return None
    amount = int(match.group(1))
    return amount * (60 if match.group(2) == "m" else 3600)


def _source_details(source: str | Mapping[str, Any]) -> tuple[str, str]:
    if isinstance(source, Mapping):
        name = str(source.get("name") or source.get("source") or "unknown")
        explicit = str(source.get("freshness") or source.get("class") or "").lower().strip()
    else:
        name = str(source or "unknown")
        explicit = name.lower().strip()
    if explicit in _EXPLICIT_FRESHNESS:
        return name, explicit
    lowered = name.lower()
    if "realtime" in lowered or "real-time" in lowered or lowered == "live":
        return name, "realtime"
    if "delay" in lowered:
        return name, "delayed"
    return name, "unknown"


def _market_from_frame(hist: pd.DataFrame, timestamp: pd.Timestamp | None) -> tuple[str, ZoneInfo]:
    attrs = getattr(hist, "attrs", {}) or {}
    market = str(attrs.get("market") or "").lower().strip()
    timezone_value = attrs.get("timezone") or attrs.get("exchange_timezone")
    if market not in _MARKET_DEFAULTS:
        zone_name = getattr(getattr(timestamp, "tz", None), "key", "") if timestamp is not None else ""
        market = "kr" if zone_name == "Asia/Seoul" else "us"
    return market, _exchange_timezone(market, str(timezone_value) if timezone_value else None)


def _as_of_timestamp(hist) -> tuple[pd.Timestamp | None, bool]:
    if hist is None or getattr(hist, "empty", True):
        return None, True
    try:
        index = pd.DatetimeIndex(hist.index).dropna()
    except Exception:
        return None, True
    if len(index) == 0:
        return None, True
    if index.tz is None:
        provider_timezone = _provider_timezone(hist)
        if provider_timezone is None:
            return None, True
        index = index.tz_localize(provider_timezone)
    return pd.Timestamp(index.max()), False


def _market_is_open(now: datetime, market: str, exchange_timezone: ZoneInfo) -> bool:
    local = now.astimezone(exchange_timezone)
    _default_timezone, opening, closing = _MARKET_DEFAULTS[market]
    return local.weekday() < 5 and opening <= local.timetz().replace(tzinfo=None) <= closing


def chart_data_status(hist, *, requested_timeframe: str, actual_timeframe: str,
                      source: str, now: datetime | None = None) -> dict[str, Any]:
    """Classify source freshness without changing the supplied OHLCV bars."""
    requested = _normalized_timeframe(requested_timeframe)
    actual = _normalized_timeframe(actual_timeframe)
    if requested != actual:
        raise ValueError(f"requested timeframe {requested} does not match actual timeframe {actual}")

    name, explicit_freshness = _source_details(source)
    as_of, timezone_assumption = _as_of_timestamp(hist)
    market, exchange_timezone = _market_from_frame(hist, as_of)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=exchange_timezone)
        timezone_assumption = True

    status: dict[str, Any] = {
        "name": name,
        "source": name,
        "requested_timeframe": requested,
        "actual_timeframe": actual,
        "as_of": as_of.isoformat() if as_of is not None else None,
        "freshness": "unknown",
        "market": market,
        "timezone": exchange_timezone.key,
        "timezone_assumption": timezone_assumption,
        "market_closed": False,
    }
    if as_of is None:
        return status

    age_seconds = max(0.0, (current.astimezone(timezone.utc) - as_of.astimezone(timezone.utc)).total_seconds())
    status["age_seconds"] = age_seconds
    if not _is_intraday(requested):
        if explicit_freshness != "unknown":
            status["freshness"] = explicit_freshness
        elif age_seconds > 4 * 24 * 60 * 60:
            status["freshness"] = "stale"
        return status

    market_open = _market_is_open(current, market, exchange_timezone)
    status["market_closed"] = not market_open
    if not market_open:
        status["freshness"] = explicit_freshness
        return status

    bar_seconds = _bar_seconds(requested)
    if bar_seconds is None:
        return status
    realtime_limit = max(90, 2 * bar_seconds)
    delayed_limit = max(1_200, 2 * bar_seconds)
    if explicit_freshness == "realtime" and age_seconds <= realtime_limit:
        status["freshness"] = "realtime"
    elif age_seconds <= delayed_limit:
        status["freshness"] = "delayed"
    else:
        status["freshness"] = "stale"
    return status


def exportable_bars(hist, metadata: Mapping[str, Any]) -> pd.DataFrame:
    """Copy bars into a CSV/JSON-safe table with an explicit ISO timestamp column."""
    if hist is None:
        out = pd.DataFrame(columns=["Timestamp"])
    else:
        frame = normalize_ohlc_frame(hist)
        out = frame.copy(deep=True)
        if "Timestamp" in out.columns:
            replacement = "SourceTimestamp"
            suffix = 2
            while replacement in out.columns:
                replacement = f"SourceTimestamp{suffix}"
                suffix += 1
            out = out.rename(columns={"Timestamp": replacement})
        timestamps = [
            value.isoformat() if not pd.isna(value) else None
            for value in pd.to_datetime(out.index, errors="coerce")
        ]
        out.insert(0, "Timestamp", timestamps)
        out = out.reset_index(drop=True)
    out.attrs["chart_data"] = copy.deepcopy(dict(metadata))
    return out
