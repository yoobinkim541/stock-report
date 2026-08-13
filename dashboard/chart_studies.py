"""Safe, module-registered chart studies and strategy preview conversion."""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Any

import pandas as pd

from ohlc_utils import normalize_ohlc_frame


@dataclass(frozen=True)
class StudyDefinition:
    id: str
    label: str
    placement: str
    parameters: dict[str, dict[str, Any]]
    compute: Callable[[pd.DataFrame, Mapping[str, Any]], "StudyOutput"]


@dataclass(frozen=True)
class StudyOutput:
    series: dict[str, pd.Series]
    placement: str
    events: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


def _definition(study_id: str, label: str, placement: str, parameters, compute) -> StudyDefinition:
    return StudyDefinition(study_id, label, placement, copy.deepcopy(parameters), compute)


def _period(default: int, *, minimum: int = 1, maximum: int = 500) -> dict[str, Any]:
    return {"type": "int", "min": minimum, "max": maximum, "default": default}


def _number(default: float, *, minimum: float, maximum: float) -> dict[str, Any]:
    return {"type": "float", "min": minimum, "max": maximum, "default": default}


def _frame(hist) -> pd.DataFrame:
    frame = normalize_ohlc_frame(hist)
    if frame is None or not isinstance(frame, pd.DataFrame):
        return pd.DataFrame()
    return frame.copy(deep=True)


def _require(frame: pd.DataFrame, *columns: str) -> None:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError(f"study requires columns: {', '.join(missing)}")


def _output(study_id: str, placement: str, params: Mapping[str, Any], **series: pd.Series) -> StudyOutput:
    return StudyOutput(
        series={name: value.copy(deep=True) for name, value in series.items()},
        placement=placement,
        events=(),
        metadata={"study_id": study_id, "parameters": copy.deepcopy(dict(params))},
    )


def _sma(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "Close")
    period = params["period"]
    return _output("sma", "overlay", params, **{f"SMA {period}": frame["Close"].rolling(period).mean()})


def _ema(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "Close")
    period = params["period"]
    return _output("ema", "overlay", params, **{f"EMA {period}": frame["Close"].ewm(span=period, adjust=False).mean()})


def _bollinger(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "Close")
    period, deviations = params["period"], params["deviations"]
    middle = frame["Close"].rolling(period).mean()
    width = frame["Close"].rolling(period).std() * deviations
    return _output(
        "bollinger", "overlay", params,
        **{f"BB upper {period}": middle + width, f"BB middle {period}": middle, f"BB lower {period}": middle - width},
    )


def _rsi(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "Close")
    period = params["period"]
    delta = frame["Close"].diff()
    gains = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    relative = gains / losses.replace(0, float("nan"))
    values = (100 - 100 / (1 + relative)).where(losses != 0, 100.0)
    values = values.where(gains != 0, 0.0)
    return _output("rsi", "bottom", params, **{f"RSI {period}": values})


def _macd(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "Close")
    fast, slow, signal_period = params["fast"], params["slow"], params["signal"]
    line = frame["Close"].ewm(span=fast, adjust=False).mean() - frame["Close"].ewm(span=slow, adjust=False).mean()
    signal = line.ewm(span=signal_period, adjust=False).mean()
    return _output(
        "macd", "bottom", params,
        **{"MACD": line, "MACD signal": signal, "MACD histogram": line - signal},
    )


def _volume(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "Volume")
    return _output("volume", "bottom", params, Volume=pd.to_numeric(frame["Volume"], errors="coerce"))


def _vwap(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "High", "Low", "Close", "Volume")
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    volume = pd.to_numeric(frame["Volume"], errors="coerce")
    values = (typical * volume).cumsum() / volume.cumsum().replace(0, float("nan"))
    return _output("vwap", "overlay", params, VWAP=values)


def _atr(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "High", "Low", "Close")
    period = params["period"]
    previous = frame["Close"].shift(1)
    true_range = pd.concat(
        [frame["High"] - frame["Low"], (frame["High"] - previous).abs(), (frame["Low"] - previous).abs()],
        axis=1,
    ).max(axis=1)
    values = true_range.ewm(alpha=1 / period, adjust=False).mean()
    return _output("atr", "bottom", params, **{f"ATR {period}": values})


def _stochastic(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "High", "Low", "Close")
    period, smooth = params["period"], params["smooth"]
    low = frame["Low"].rolling(period).min()
    high = frame["High"].rolling(period).max()
    k = ((frame["Close"] - low) / (high - low).replace(0, float("nan")) * 100).rolling(smooth).mean()
    return _output("stochastic", "bottom", params, **{"Stochastic %K": k, "Stochastic %D": k.rolling(smooth).mean()})


def _rolling_zscore(frame: pd.DataFrame, params: Mapping[str, Any]) -> StudyOutput:
    _require(frame, "Close")
    period = params["period"]
    mean = frame["Close"].rolling(period).mean()
    deviation = frame["Close"].rolling(period).std().replace(0, float("nan"))
    return _output("rolling_zscore", "bottom", params, **{f"Z-score {period}": (frame["Close"] - mean) / deviation})


_CATALOG = (
    _definition("sma", "Simple Moving Average", "overlay", {"period": _period(20)}, _sma),
    _definition("ema", "Exponential Moving Average", "overlay", {"period": _period(20)}, _ema),
    _definition("bollinger", "Bollinger Bands", "overlay", {"period": _period(20), "deviations": _number(2.0, minimum=0.1, maximum=10.0)}, _bollinger),
    _definition("rsi", "Relative Strength Index", "bottom", {"period": _period(14)}, _rsi),
    _definition("macd", "MACD", "bottom", {"fast": _period(12), "slow": _period(26), "signal": _period(9)}, _macd),
    _definition("volume", "Volume", "bottom", {}, _volume),
    _definition("vwap", "Session VWAP", "overlay", {}, _vwap),
    _definition("atr", "Average True Range", "bottom", {"period": _period(14)}, _atr),
    _definition("stochastic", "Stochastic", "bottom", {"period": _period(14), "smooth": _period(3)}, _stochastic),
    _definition("rolling_zscore", "Rolling Z-score", "bottom", {"period": _period(20)}, _rolling_zscore),
)
_REGISTRY = {definition.id: definition for definition in _CATALOG}


def study_catalog() -> tuple[StudyDefinition, ...]:
    return _CATALOG


def _validated_params(definition: StudyDefinition, params: Mapping[str, Any] | None) -> dict[str, Any]:
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("study parameters must be an object")
    supplied = dict(params or {})
    unknown = set(supplied) - set(definition.parameters)
    if unknown:
        raise ValueError(f"unknown parameter for {definition.id}: {sorted(unknown)[0]}")
    out: dict[str, Any] = {}
    for name, spec in definition.parameters.items():
        value = supplied.get(name, spec["default"])
        expected = spec["type"]
        valid_type = (
            isinstance(value, Integral) and not isinstance(value, bool)
            if expected == "int"
            else isinstance(value, Real) and not isinstance(value, bool)
        )
        if not valid_type:
            raise ValueError(f"invalid {name}: expected {expected}")
        value = int(value) if expected == "int" else float(value)
        if not math.isfinite(float(value)) or value < spec["min"] or value > spec["max"]:
            raise ValueError(f"invalid {name}: expected {spec['min']}..{spec['max']}")
        out[name] = value
    if definition.id == "macd" and out["fast"] >= out["slow"]:
        raise ValueError("fast period must be less than slow period")
    return out


def run_study(study_id: str, hist, params: Mapping[str, Any] | None = None) -> StudyOutput:
    definition = _REGISTRY.get(str(study_id or "").strip().lower())
    if definition is None:
        raise ValueError(f"unknown study: {study_id}")
    validated = _validated_params(definition, params)
    return definition.compute(_frame(hist), validated)


_CODE_RE = re.compile(r"(?:\b(?:import|exec|eval|lambda)\b|__|\bopen\s*\()", re.IGNORECASE)


def _reject_code_strings(value: Any) -> None:
    if isinstance(value, str) and _CODE_RE.search(value):
        raise ValueError("strategy preview cannot contain source code")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_code_strings(key)
            _reject_code_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_code_strings(item)


def study_output_from_strategy_preview(preview: Mapping[str, Any]) -> StudyOutput:
    """Convert data-only strategy plots/events; never import or execute preview code."""
    if not isinstance(preview, Mapping):
        raise ValueError("strategy preview must be an object")
    unknown = set(preview) - {"plots", "events", "metadata"}
    if unknown:
        raise ValueError(f"unknown strategy preview key: {sorted(unknown)[0]}")
    _reject_code_strings(preview)

    plots = preview.get("plots") or []
    events = preview.get("events") or []
    metadata = preview.get("metadata") or {}
    if not isinstance(plots, list) or not isinstance(events, list) or not isinstance(metadata, Mapping):
        raise ValueError("plots, events, and metadata must use data-only containers")

    out_series: dict[str, pd.Series] = {}
    placements: set[str] = set()
    for index, plot in enumerate(plots):
        if not isinstance(plot, Mapping):
            raise ValueError(f"plots[{index}] must be an object")
        extra = set(plot) - {"name", "dates", "values", "placement"}
        if extra:
            raise ValueError(f"unknown plot key: {sorted(extra)[0]}")
        name = str(plot.get("name") or "").strip()
        dates, values = plot.get("dates"), plot.get("values")
        placement = str(plot.get("placement") or "overlay")
        if not name or name in out_series:
            raise ValueError("plot names must be present and unique")
        if placement not in {"overlay", "bottom"}:
            raise ValueError(f"unsupported plot placement: {placement}")
        if not isinstance(dates, list) or not isinstance(values, list) or len(dates) != len(values):
            raise ValueError(f"plot {name} dates and values must have equal lengths")
        numeric: list[float] = []
        timestamps: list[pd.Timestamp] = []
        for date, value in zip(dates, values):
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
                raise ValueError(f"plot {name} contains a non-finite value")
            try:
                timestamp = pd.Timestamp(date)
            except Exception as exc:
                raise ValueError(f"plot {name} contains an invalid date") from exc
            if pd.isna(timestamp):
                raise ValueError(f"plot {name} contains an invalid date")
            timestamps.append(timestamp)
            numeric.append(float(value))
        out_series[name] = pd.Series(numeric, index=pd.DatetimeIndex(timestamps), name=name)
        placements.add(placement)

    clean_events: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"events[{index}] must be an object")
        extra = set(event) - {"date", "kind", "price", "label"}
        if extra:
            raise ValueError(f"unknown event key: {sorted(extra)[0]}")
        try:
            date = pd.Timestamp(event.get("date"))
        except Exception as exc:
            raise ValueError(f"events[{index}] contains an invalid date") from exc
        if pd.isna(date):
            raise ValueError(f"events[{index}] contains an invalid date")
        price = event.get("price")
        if price is not None and (
            isinstance(price, bool) or not isinstance(price, Real) or not math.isfinite(float(price))
        ):
            raise ValueError(f"events[{index}] contains an invalid price")
        clean_events.append({
            "date": date.isoformat(),
            "kind": str(event.get("kind") or "event"),
            "price": float(price) if price is not None else None,
            "label": str(event.get("label") or ""),
        })

    clean_metadata: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, (str, bool, int, float, type(None))):
            raise ValueError("strategy preview metadata must contain scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("strategy preview metadata contains a non-finite value")
        clean_metadata[key] = copy.deepcopy(value)
    placement = next(iter(placements)) if len(placements) == 1 else ("mixed" if placements else "overlay")
    return StudyOutput(out_series, placement, tuple(clean_events), clean_metadata)
