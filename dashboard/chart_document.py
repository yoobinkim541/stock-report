"""Versioned, renderer-neutral chart state and legacy panel adapters."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

import ticker_names


CHART_DOCUMENT_VERSION = 1
CHART_TYPES: frozenset[str] = frozenset({
    "line", "area", "baseline", "candlestick", "hollow_candle",
    "heikin_ashi", "bars", "high_low", "renko", "kagi", "line_break", "range",
})
SESSION_POLICIES = frozenset({"regular", "extended", "all"})
SERIES_KINDS = frozenset({"price", "benchmark", "peer", "portfolio", "fundamental", "analyst"})
TIMEFRAMES = frozenset({"5m", "1h", "2h", "4h", "1d", "1wk", "1mo"})
PERIODS = frozenset({"3mo", "6mo", "1y", "5y", "전체"})
_PATCH_PATHS = {
    "symbol", "timeframe", "period", "chart.type", "chart.params",
    "chart.params.box_size", "chart.params.reversal", "chart.params.lines",
    "chart.params.range_size", "session.policy", "scale.type", "series",
    "studies", "events", "analysis.visible", "analysis.sections",
}
_CHART_PARAM_NAMES = frozenset({"box_size", "reversal", "lines", "range_size"})
_SCALE_TYPES = frozenset({"linear", "log"})


def _symbol(value: Any, fallback: str = "MSFT") -> str:
    normalized = ticker_names.normalize_input(str(value or "").strip())
    return normalized or fallback


def _market_defaults(symbol: str) -> tuple[str, str]:
    if symbol.endswith((".KS", ".KQ")):
        return "kr", "Asia/Seoul"
    return "us", "America/New_York"


def default_chart_document(ticker: str = "MSFT") -> dict[str, Any]:
    symbol = _symbol(ticker)
    market, timezone = _market_defaults(symbol)
    return {
        "version": CHART_DOCUMENT_VERSION,
        "symbol": symbol,
        "market": market,
        "timezone": timezone,
        "timeframe": "1d",
        "period": "6mo",
        "chart": {"type": "candlestick", "params": {}},
        "session": {"policy": "regular"},
        "source": {"name": "", "as_of": None, "freshness": "unknown", "quality": "unknown"},
        "series": [{"id": "primary", "kind": "price", "symbol": symbol, "axis": "primary", "normalization": "raw", "visible": True}],
        "studies": [],
        "drawings": [],
        "events": [],
        "alerts": [],
        "analysis": {"visible": True, "sections": ["trend", "patterns", "mtfa", "seasonality", "relative_strength", "fundamentals", "alerts", "data_quality"]},
        "replay": {"active": False, "cursor": None},
        "scale": {"type": "linear"},
        "view": {"start": None, "end": None},
        "renderer": {"preferred": "plotly"},
    }


def _merged_mapping(default: Mapping[str, Any], value: Any) -> dict[str, Any] | Any:
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    out = copy.deepcopy(dict(default))
    out.update(copy.deepcopy(dict(value)))
    return out


def _normalize_series(series: list[Any], symbol: str) -> list[Any]:
    normalized: list[Any] = []
    for index, raw in enumerate(series):
        if not isinstance(raw, Mapping):
            normalized.append(copy.deepcopy(raw))
            continue
        value = copy.deepcopy(dict(raw))
        value.setdefault("id", "primary" if index == 0 else f"series-{index + 1}")
        value["id"] = str(value["id"] or f"series-{index + 1}")
        value.setdefault("kind", "price" if index == 0 else "benchmark")
        value["kind"] = str(value["kind"] or "price")
        value.setdefault("symbol", symbol)
        value["symbol"] = _symbol(value["symbol"], symbol)
        value.setdefault("axis", "primary")
        value.setdefault("normalization", "raw")
        value.setdefault("visible", True)
        normalized.append(value)
    return normalized


def normalize_chart_document(raw: Mapping[str, Any] | None, *, ticker: str = "MSFT") -> dict[str, Any]:
    """Deep-copy and migrate a document without replacing requested settings."""
    if not isinstance(raw, Mapping):
        return default_chart_document(ticker)

    source = copy.deepcopy(dict(raw))
    symbol = _symbol(source.get("symbol"), _symbol(ticker))
    out = default_chart_document(symbol)
    version = source.get("version")
    out["version"] = CHART_DOCUMENT_VERSION if version in (None, 0, "0", "1", 1) else copy.deepcopy(version)
    out["symbol"] = symbol

    for key in ("market", "timezone", "timeframe", "period"):
        if key in source and source[key] is not None:
            out[key] = copy.deepcopy(source[key])
    for key in ("chart", "session", "source", "analysis", "replay", "scale", "view", "renderer"):
        if key in source:
            out[key] = _merged_mapping(out[key], source[key])
    for key in ("series", "studies", "drawings", "events", "alerts"):
        if key in source:
            out[key] = copy.deepcopy(source[key])

    if isinstance(out["chart"], dict):
        chart_type = out["chart"].get("type")
        out["chart"]["type"] = "candlestick" if chart_type == "candle" else chart_type
        if "params" not in out["chart"]:
            out["chart"]["params"] = {}
    if isinstance(out["series"], list):
        out["series"] = _normalize_series(out["series"], symbol)
    return out


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _chart_parameter_errors(params: Any) -> list[str]:
    if not isinstance(params, Mapping):
        return ["chart.params must be an object"]
    errors: list[str] = []
    for name, value in params.items():
        if name not in _CHART_PARAM_NAMES:
            errors.append(f"unsupported chart parameter: {name}")
            continue
        if name == "lines":
            valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
        else:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
        if not valid:
            errors.append(f"invalid chart parameter {name}: {value}")
    return errors


def validate_chart_document(document: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(document, Mapping):
        return ["document must be an object"], warnings

    if document.get("version") != CHART_DOCUMENT_VERSION:
        errors.append(f"unsupported document version: {document.get('version')}")
    if not isinstance(document.get("symbol"), str) or not document.get("symbol", "").strip():
        errors.append("symbol is required")
    timeframe = document.get("timeframe")
    if timeframe not in TIMEFRAMES:
        errors.append(f"unsupported timeframe: {timeframe}")
    if document.get("period") not in PERIODS:
        errors.append(f"unsupported period: {document.get('period')}")

    chart = document.get("chart")
    if not isinstance(chart, Mapping):
        errors.append("chart must be an object")
    else:
        if chart.get("type") not in CHART_TYPES:
            errors.append(f"unsupported chart type: {chart.get('type')}")
        errors.extend(_chart_parameter_errors(chart.get("params")))
    session = document.get("session")
    if not isinstance(session, Mapping) or session.get("policy") not in SESSION_POLICIES:
        errors.append(f"unsupported session policy: {session.get('policy') if isinstance(session, Mapping) else None}")
    scale = document.get("scale")
    if not isinstance(scale, Mapping) or scale.get("type") not in _SCALE_TYPES:
        errors.append(f"unsupported scale type: {scale.get('type') if isinstance(scale, Mapping) else None}")

    series = document.get("series")
    if not isinstance(series, list) or not series:
        errors.append("series must contain a primary series")
    else:
        ids: set[str] = set()
        for index, item in enumerate(series):
            if not isinstance(item, Mapping):
                errors.append(f"series[{index}] must be an object")
                continue
            series_id = item.get("id")
            if not isinstance(series_id, str) or not series_id:
                errors.append(f"series[{index}] id is required")
            elif series_id in ids:
                errors.append(f"duplicate series id: {series_id}")
            else:
                ids.add(series_id)
            if item.get("kind") not in SERIES_KINDS:
                errors.append(f"unsupported series kind: {item.get('kind')}")
            if not isinstance(item.get("symbol"), str) or not item.get("symbol", "").strip():
                errors.append(f"series[{index}] symbol is required")

    for key in ("studies", "drawings", "events", "alerts"):
        if not isinstance(document.get(key), list):
            errors.append(f"{key} must be a list")
    analysis = document.get("analysis")
    if not isinstance(analysis, Mapping):
        errors.append("analysis must be an object")
    elif not isinstance(analysis.get("visible"), bool) or not isinstance(analysis.get("sections"), list):
        errors.append("analysis must contain visible and sections")
    return errors, warnings


def _validate_patch_value(path: str, value: Any) -> None:
    if not _is_json_value(value):
        raise ValueError(f"patch value for {path} must be JSON data")
    if path in {"symbol", "timeframe", "period", "chart.type", "session.policy", "scale.type"} and not isinstance(value, str):
        raise ValueError(f"patch value for {path} must be a string")
    if path == "chart.params" and not isinstance(value, Mapping):
        raise ValueError("patch value for chart.params must be an object")
    if path.startswith("chart.params"):
        parameter = path.rsplit(".", 1)[-1]
        params = value if path == "chart.params" else {parameter: value}
        errors = _chart_parameter_errors(params)
        if errors:
            raise ValueError("; ".join(errors))
    if path in {"series", "studies", "events", "analysis.sections"} and not isinstance(value, list):
        raise ValueError(f"patch value for {path} must be a list")
    if path == "analysis.visible" and not isinstance(value, bool):
        raise ValueError("patch value for analysis.visible must be a boolean")


def apply_chart_document_patch(document: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Apply only documented data paths, rejecting executable or unknown input."""
    if not isinstance(patch, Mapping):
        raise ValueError("patch must be an object")
    out = normalize_chart_document(document)
    for raw_path, value in patch.items():
        path = str(raw_path)
        if path not in _PATCH_PATHS:
            raise ValueError(f"unsupported patch path: {path}")
        _validate_patch_value(path, value)
        value = copy.deepcopy(value)
        if path in {"symbol", "timeframe", "period"}:
            out[path] = value
        elif path == "chart.type":
            out["chart"]["type"] = value
        elif path == "chart.params":
            out["chart"]["params"] = value
        elif path.startswith("chart.params."):
            out["chart"]["params"][path.rsplit(".", 1)[-1]] = value
        elif path == "session.policy":
            out["session"]["policy"] = value
        elif path == "scale.type":
            out["scale"]["type"] = value
        elif path in {"series", "studies", "events"}:
            out[path] = value
        elif path == "analysis.visible":
            out["analysis"]["visible"] = value
        elif path == "analysis.sections":
            out["analysis"]["sections"] = value

    out = normalize_chart_document(out, ticker=out.get("symbol", "MSFT"))
    errors, _warnings = validate_chart_document(out)
    if errors:
        raise ValueError("; ".join(errors))
    return out


def document_from_panel(panel: Mapping[str, Any], *, workspace_id: str = "") -> dict[str, Any]:
    source = copy.deepcopy(dict(panel or {}))
    document = default_chart_document(_symbol(source.get("ticker")))
    for key in ("timeframe", "period"):
        if source.get(key) is not None:
            document[key] = copy.deepcopy(source[key])
    chart_kind = source.get("chart_kind")
    if chart_kind:
        document["chart"]["type"] = "candlestick" if chart_kind == "candle" else str(chart_kind)
    document["source"]["workspace_id"] = str(workspace_id or "")
    document["source"]["panel_id"] = str(source.get("id") or "")

    studies: list[dict[str, Any]] = []
    for pane, key in (("top", "top_indicators"), ("bottom", "bottom_indicators")):
        values = source.get(key)
        if isinstance(values, list):
            for index, name in enumerate(values):
                studies.append({"id": f"{pane}-{index + 1}", "kind": "indicator", "name": copy.deepcopy(name), "pane": pane, "visible": True})
    document["studies"] = studies

    compare = source.get("compare")
    if isinstance(compare, list):
        for index, value in enumerate(compare, start=1):
            symbol = _symbol(value, "")
            if symbol:
                document["series"].append({"id": f"compare-{index}", "kind": "benchmark", "symbol": symbol, "axis": "primary", "normalization": "raw", "visible": True})
    document["scale"]["type"] = "log" if source.get("log_scale") else "linear"
    return normalize_chart_document(document, ticker=document["symbol"])


def panel_from_document(document: Mapping[str, Any], panel: Mapping[str, Any] | None = None) -> dict[str, Any]:
    doc = normalize_chart_document(document)
    out = copy.deepcopy(dict(panel or {}))
    out["ticker"] = doc["symbol"]
    out["timeframe"] = doc["timeframe"]
    out["period"] = doc["period"]
    chart = doc.get("chart")
    if isinstance(chart, Mapping):
        out["chart_kind"] = chart.get("type")
    scale = doc.get("scale")
    out["log_scale"] = isinstance(scale, Mapping) and scale.get("type") == "log"

    top: list[Any] = []
    bottom: list[Any] = []
    for study in doc.get("studies") if isinstance(doc.get("studies"), list) else []:
        if not isinstance(study, Mapping) or "name" not in study:
            continue
        if study.get("pane") == "top":
            top.append(copy.deepcopy(study["name"]))
        elif study.get("pane") == "bottom":
            bottom.append(copy.deepcopy(study["name"]))
    out["top_indicators"] = top
    out["bottom_indicators"] = bottom

    compare: list[str] = []
    for series in doc.get("series") if isinstance(doc.get("series"), list) else []:
        if not isinstance(series, Mapping) or series.get("id") == "primary":
            continue
        symbol = series.get("symbol")
        if isinstance(symbol, str) and symbol:
            compare.append(symbol)
    out["compare"] = compare
    return out
