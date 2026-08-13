"""Pure analysis snapshot builder shared by ticker and workspace chart surfaces."""
from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from dashboard import chart_analysis, chart_document, trendlines


def benchmark_symbol(document: Mapping[str, Any]) -> str:
    """Return an explicit visible comparison symbol or a market default."""
    normalized = chart_document.normalize_chart_document(document)
    primary = str(normalized["symbol"])
    for series in normalized.get("series") or []:
        if not isinstance(series, Mapping) or not bool(series.get("visible", True)):
            continue
        if series.get("id") == "primary" or series.get("kind") == "price":
            continue
        symbol = str(series.get("symbol") or "").strip().upper()
        if symbol and symbol != primary:
            return symbol
    return "^KS11" if normalized.get("market") == "kr" else "QQQ"


def _trend_summary(hist) -> dict[str, Any]:
    rows = trendlines.detect_trendlines(hist)
    counts = Counter(str(row.get("kind") or "unknown") for row in rows)
    by_kind = {kind: int(counts.get(kind, 0)) for kind in ("support", "resistance", "channel")}
    ranked = sorted(
        rows,
        key=lambda row: float((row.get("meta") or {}).get("score") or 0.0),
        reverse=True,
    )
    return {
        "count": len(rows),
        "by_kind": by_kind,
        "leading": copy.deepcopy(ranked[0]) if ranked else None,
        "items": copy.deepcopy(rows),
    }

def _optional(name: str, loader: Callable[[str], Any], symbol: str,
              fallback: Any, errors: dict[str, str]) -> Any:
    try:
        value = loader(symbol)
    except Exception as exc:
        errors[name] = str(exc)
        return copy.deepcopy(fallback)
    return copy.deepcopy(fallback if value is None else value)


def build_analysis_snapshot(
    document,
    hist,
    *,
    ohlc_loader: Callable[[str, str], Any],
    fundamental_loader: Callable[[str], Any],
    alert_loader: Callable[[str], Any],
    orderflow_loader: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Build one failure-isolated, renderer-neutral analysis payload.

    Primary history is supplied by the controller so the chart and analysis use
    the same bars. Optional providers are isolated; unavailable fundamentals or
    alerts never suppress deterministic price analysis.
    """
    normalized = chart_document.normalize_chart_document(document)
    symbol = str(normalized["symbol"])
    benchmark = benchmark_symbol(normalized)
    errors: dict[str, str] = {}

    try:
        benchmark_hist = ohlc_loader(benchmark, "1d")
        relative_strength = chart_analysis.relative_strength_summary(hist, benchmark_hist)
    except Exception as exc:
        errors["relative_strength"] = str(exc)
        relative_strength = {"ok": False, "reason": "provider_unavailable"}

    try:
        trend = _trend_summary(hist)
    except Exception as exc:
        errors["trend"] = str(exc)
        trend = {"count": 0, "by_kind": {"support": 0, "resistance": 0, "channel": 0}, "leading": None, "items": []}

    try:
        patterns = chart_analysis.pattern_candidates(hist)
    except Exception as exc:
        errors["patterns"] = str(exc)
        patterns = []
    try:
        seasonality = chart_analysis.seasonality_summary(hist)
    except Exception as exc:
        errors["seasonality"] = str(exc)
        seasonality = {"ok": False, "reason": "analysis_error", "months": []}

    multi_timeframe = chart_analysis.multi_timeframe_summary(ohlc_loader, symbol)
    fundamentals = _optional("fundamentals", fundamental_loader, symbol, {}, errors)
    alerts = _optional("alerts", alert_loader, symbol, [], errors)
    orderflow = (
        _optional("orderflow", orderflow_loader, symbol,
                  {"ok": False, "reason": "provider_unavailable"}, errors)
        if orderflow_loader is not None
        else {"ok": False, "reason": "capture_not_configured"}
    )
    if not isinstance(fundamentals, Mapping):
        errors["fundamentals"] = "provider returned a non-object payload"
        fundamentals = {}
    if not isinstance(alerts, list):
        errors["alerts"] = "provider returned a non-list payload"
        alerts = []
    if not isinstance(orderflow, Mapping):
        errors["orderflow"] = "provider returned a non-object payload"
        orderflow = {"ok": False, "reason": "invalid_provider_payload"}

    source = normalized.get("source") or {}
    data_quality = {
        "source": str(source.get("name") or "unknown"),
        "as_of": source.get("as_of"),
        "freshness": str(source.get("freshness") or "unknown"),
        "quality": str(source.get("quality") or "unknown"),
        "partial": bool(errors) or not bool(multi_timeframe.get("ok")),
    }
    return {
        "symbol": symbol,
        "benchmark": benchmark,
        "trend": trend,
        "patterns": patterns,
        "multi_timeframe": multi_timeframe,
        "seasonality": seasonality,
        "relative_strength": relative_strength,
        "fundamentals": dict(fundamentals),
        "alerts": alerts,
        "orderflow": dict(orderflow),
        "data_quality": data_quality,
        "errors": errors,
    }
