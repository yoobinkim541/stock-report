"""Deterministic, renderer-neutral transforms for supported chart modes.

Synthetic price charts here are calculated from normalized OHLCV bars.  Their
``SourceTimestamp`` values identify the source bar that completed an element;
they never claim tick-level timing or market-microstructure information.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
from numbers import Integral, Real
from typing import Any

import numpy as np
import pandas as pd

from ohlc_utils import normalize_ohlc_frame


@dataclass(frozen=True)
class ChartTransformResult:
    frame: pd.DataFrame
    render_kind: str
    x_mode: str
    synthetic: bool
    metadata: dict[str, Any]


def _normalized_copy(hist: pd.DataFrame | None) -> pd.DataFrame:
    frame = normalize_ohlc_frame(hist)
    if frame is None:
        return pd.DataFrame()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("hist must be a pandas DataFrame")
    return frame.copy(deep=True)


def _require_columns(frame: pd.DataFrame, columns: set[str]) -> None:
    if not frame.empty and not columns <= set(frame.columns):
        raise ValueError(f"hist must contain columns: {', '.join(sorted(columns))}")


def _close_observations(frame: pd.DataFrame) -> list[tuple[Any, float]]:
    _require_columns(frame, {"Close"})
    observations: list[tuple[Any, float]] = []
    for timestamp, value in frame["Close"].items():
        try:
            close = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(close):
            observations.append((timestamp, close))
    return observations


def _sequence_frame(records: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    out = pd.DataFrame.from_records(records, columns=columns)
    out.index = pd.RangeIndex(len(out), name="Sequence")
    return out


def _metadata(chart_type: str, *, source_precision: str = "ohlcv_bar", **values: Any) -> dict[str, Any]:
    return {"chart_type": chart_type, "source_precision": source_precision, **values}


def _atr_default(frame: pd.DataFrame) -> float:
    _require_columns(frame, {"High", "Low", "Close"})
    if not frame.empty:
        high = pd.to_numeric(frame["High"], errors="coerce")
        low = pd.to_numeric(frame["Low"], errors="coerce")
        close = pd.to_numeric(frame["Close"], errors="coerce")
        previous_close = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1,
        ).max(axis=1)
        atr = true_range.ewm(alpha=1 / 14, adjust=False).mean()
        finite_atr = atr[np.isfinite(atr)]
        if not finite_atr.empty and float(finite_atr.iloc[-1]) > 0:
            return float(finite_atr.iloc[-1])
        finite_close = close[np.isfinite(close)]
        if not finite_close.empty:
            fallback = float(finite_close.iloc[-1]) * 0.01
            if math.isfinite(fallback) and fallback > 0:
                return fallback
    return 1.0


def _positive_float(params: Mapping[str, Any], name: str, default: float) -> float:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _positive_int(params: Mapping[str, Any], name: str, default: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _identity_line(frame: pd.DataFrame, chart_type: str, _params: Mapping[str, Any]) -> ChartTransformResult:
    _require_columns(frame, {"Close"})
    return ChartTransformResult(frame, "line", "time", False, _metadata(chart_type))


def _identity_candle(frame: pd.DataFrame, chart_type: str, _params: Mapping[str, Any]) -> ChartTransformResult:
    _require_columns(frame, {"Open", "High", "Low", "Close"})
    return ChartTransformResult(frame, "candlestick", "time", False, _metadata(chart_type))


def _heikin_ashi(frame: pd.DataFrame, chart_type: str, _params: Mapping[str, Any]) -> ChartTransformResult:
    _require_columns(frame, {"Open", "High", "Low", "Close"})
    if frame.empty:
        return ChartTransformResult(frame, "candlestick", "time", True, _metadata(chart_type))

    open_, high, low, close = (frame[column].to_numpy(dtype=float) for column in ("Open", "High", "Low", "Close"))
    ha_close = (open_ + high + low + close) / 4.0
    ha_open = np.empty_like(ha_close)
    ha_open[0] = (open_[0] + close[0]) / 2.0
    for index in range(1, len(ha_open)):
        ha_open[index] = (ha_open[index - 1] + ha_close[index - 1]) / 2.0
    out = pd.DataFrame(
        {
            "Open": ha_open,
            "High": np.maximum.reduce([high, ha_open, ha_close]),
            "Low": np.minimum.reduce([low, ha_open, ha_close]),
            "Close": ha_close,
        },
        index=frame.index,
    )
    if "Volume" in frame:
        out["Volume"] = frame["Volume"].to_numpy(copy=True)
    return ChartTransformResult(out, "candlestick", "time", True, _metadata(chart_type))


def _renko(frame: pd.DataFrame, chart_type: str, params: Mapping[str, Any]) -> ChartTransformResult:
    box_size = _positive_float(params, "box_size", _atr_default(frame))
    observations = _close_observations(frame)
    records: list[dict[str, Any]] = []
    if observations:
        level = observations[0][1]
        for timestamp, close in observations[1:]:
            while close >= level + box_size:
                open_ = level
                level += box_size
                records.append({"Open": open_, "High": level, "Low": open_, "Close": level, "SourceTimestamp": timestamp})
            while close <= level - box_size:
                open_ = level
                level -= box_size
                records.append({"Open": open_, "High": open_, "Low": level, "Close": level, "SourceTimestamp": timestamp})
    out = _sequence_frame(records, ["Open", "High", "Low", "Close", "SourceTimestamp"])
    return ChartTransformResult(
        out, "candlestick", "sequence", True,
        _metadata(chart_type, source_precision="ohlcv_close_path", box_size=box_size),
    )


def _kagi(frame: pd.DataFrame, chart_type: str, params: Mapping[str, Any]) -> ChartTransformResult:
    reversal = _positive_float(params, "reversal", _atr_default(frame))
    observations = _close_observations(frame)
    records: list[dict[str, Any]] = []
    if observations:
        timestamp, start = observations[0]
        records.append({"Close": start, "SourceTimestamp": timestamp})
        direction = 0
        extreme = start
        for timestamp, close in observations[1:]:
            if direction == 0:
                if close > extreme:
                    direction, extreme = 1, close
                    records.append({"Close": close, "SourceTimestamp": timestamp})
                elif close < extreme:
                    direction, extreme = -1, close
                    records.append({"Close": close, "SourceTimestamp": timestamp})
            elif direction == 1:
                if close > extreme:
                    extreme = close
                    records[-1] = {"Close": close, "SourceTimestamp": timestamp}
                elif extreme - close >= reversal:
                    direction, extreme = -1, close
                    records.append({"Close": close, "SourceTimestamp": timestamp})
            else:
                if close < extreme:
                    extreme = close
                    records[-1] = {"Close": close, "SourceTimestamp": timestamp}
                elif close - extreme >= reversal:
                    direction, extreme = 1, close
                    records.append({"Close": close, "SourceTimestamp": timestamp})
    out = _sequence_frame(records, ["Close", "SourceTimestamp"])
    return ChartTransformResult(
        out, "line", "sequence", True,
        _metadata(chart_type, source_precision="ohlcv_close_path", reversal=reversal),
    )


def _line_break(frame: pd.DataFrame, chart_type: str, params: Mapping[str, Any]) -> ChartTransformResult:
    lines = _positive_int(params, "lines", 3)
    observations = _close_observations(frame)
    records: list[dict[str, Any]] = []
    if observations:
        timestamp, start = observations[0]
        records.append({"Open": start, "High": start, "Low": start, "Close": start, "SourceTimestamp": timestamp})
        direction = 0
        for timestamp, close in observations[1:]:
            last_close = float(records[-1]["Close"])
            recent = records[-lines:]
            body_high = max(max(float(row["Open"]), float(row["Close"])) for row in recent)
            body_low = min(min(float(row["Open"]), float(row["Close"])) for row in recent)
            next_direction = 0
            if direction >= 0:
                if close > last_close:
                    next_direction = 1
                elif close < body_low:
                    next_direction = -1
            if direction <= 0 and not next_direction:
                if close < last_close:
                    next_direction = -1
                elif close > body_high:
                    next_direction = 1
            if next_direction:
                records.append(
                    {
                        "Open": last_close,
                        "High": max(last_close, close),
                        "Low": min(last_close, close),
                        "Close": close,
                        "SourceTimestamp": timestamp,
                    },
                )
                direction = next_direction
    out = _sequence_frame(records, ["Open", "High", "Low", "Close", "SourceTimestamp"])
    return ChartTransformResult(
        out, "candlestick", "sequence", True,
        _metadata(chart_type, source_precision="ohlcv_close_path", lines=lines),
    )


def _range_bars(frame: pd.DataFrame, chart_type: str, params: Mapping[str, Any]) -> ChartTransformResult:
    range_size = _positive_float(params, "range_size", _atr_default(frame))
    observations = _close_observations(frame)
    records: list[dict[str, Any]] = []
    if observations:
        level = observations[0][1]
        for timestamp, close in observations[1:]:
            while close >= level + range_size:
                open_ = level
                level += range_size
                records.append({"Open": open_, "High": level, "Low": open_, "Close": level, "SourceTimestamp": timestamp})
            while close <= level - range_size:
                open_ = level
                level -= range_size
                records.append({"Open": open_, "High": open_, "Low": level, "Close": level, "SourceTimestamp": timestamp})
    out = _sequence_frame(records, ["Open", "High", "Low", "Close", "SourceTimestamp"])
    return ChartTransformResult(
        out, "candlestick", "sequence", True,
        _metadata(chart_type, source_precision="ohlcv_close_path", range_size=range_size),
    )


_TRANSFORMS: dict[str, Callable[[pd.DataFrame, str, Mapping[str, Any]], ChartTransformResult]] = {
    "line": _identity_line,
    "area": _identity_line,
    "baseline": _identity_line,
    "candlestick": _identity_candle,
    "hollow_candle": _identity_candle,
    "heikin_ashi": _heikin_ashi,
    "bars": _identity_candle,
    "high_low": _identity_candle,
    "renko": _renko,
    "kagi": _kagi,
    "line_break": _line_break,
    "range": _range_bars,
}


def available_chart_types() -> tuple[str, ...]:
    return tuple(_TRANSFORMS)


def transform_chart(
    hist: pd.DataFrame,
    chart_type: str,
    params: Mapping[str, Any] | None = None,
) -> ChartTransformResult:
    """Transform normalized OHLCV into a deterministic chart representation."""
    if chart_type not in _TRANSFORMS:
        raise ValueError(f"unsupported chart type: {chart_type}")
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("params must be a mapping")
    frame = _normalized_copy(hist)
    return _TRANSFORMS[chart_type](frame, chart_type, dict(params or {}))
