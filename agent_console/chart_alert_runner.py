from __future__ import annotations

from typing import Any

import pandas as pd

from .chart_alerts import evaluate_chart_alert


def evaluate_alert_rules(
    rules: list[dict[str, Any]],
    bars_by_symbol: dict[str, Any],
    *,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate saved chart alert rules against current OHLC bars."""
    events: list[dict[str, Any]] = []
    for rule in rules or []:
        if not bool((rule or {}).get("enabled", True)):
            continue
        symbol = str((rule or {}).get("symbol") or "").upper().strip()
        if not symbol:
            continue
        bars = _bars_frame((bars_by_symbol or {}).get(symbol))
        if len(bars) < 2 or "close" not in bars.columns:
            continue

        previous = bars.iloc[-2]
        current = bars.iloc[-1]
        previous_price = _float_or_none(previous.get("close"))
        current_price = _float_or_none(current.get("close"))
        previous_values = _indicator_values(bars.iloc[:-1], previous.name)
        current_values = _indicator_values(bars, current.name)
        current_as_of = as_of or current_values.get("time")

        result = evaluate_chart_alert(
            rule,
            previous_price=previous_price,
            current_price=current_price,
            previous_values=previous_values,
            current_values=current_values,
            as_of=current_as_of,
        )
        if result.get("triggered"):
            event = dict(result.get("event") or {})
            event["indicator_values"] = {
                key: value for key, value in current_values.items() if key != "time"
            }
            events.append(event)
    return events


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
