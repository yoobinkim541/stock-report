"""Pure analysis snapshot builder shared by ticker and workspace chart surfaces."""
from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd

from dashboard import chart_analysis, chart_data_policy, chart_document, trendlines


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


def _replay_horizon(normalized: Mapping[str, Any]) -> pd.Timestamp | None:
    """Information boundary of an active replay: the close of the cursor bar (UTC)."""
    replay = normalized.get("replay") or {}
    cursor = replay.get("cursor") if replay.get("active") else None
    if cursor in (None, ""):
        return None
    return chart_data_policy.bar_close_time(
        cursor, market=str(normalized["market"]), timeframe=str(normalized.get("timeframe") or "1d"),
    )


def _replay_bounded_loader(loader: Callable[[str, str], Any], horizon: pd.Timestamp,
                           market: str) -> Callable[[str, str], Any]:
    """Every auxiliary series sees only bars already closed at the replay boundary.

    A daily bar of the cursor's own day is excluded until that day's exchange close,
    so an intraday cursor never reads the day's final close.
    """
    def load(symbol: str, timeframe: str):
        frame = loader(symbol, timeframe)
        if frame is None or getattr(frame, "empty", True):
            return frame
        closes = chart_data_policy.bar_close_times(frame.index, market=market, timeframe=timeframe)
        return frame[closes <= horizon]

    return load


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
    alerts never suppress deterministic price analysis. With an active replay
    (``document["replay"]``) every series fetched through ``ohlc_loader`` is cut at
    the cursor bar's close (relative strength, multi-timeframe) and live orderflow
    is isolated (``replay_isolated``), so the rail never shows data after the replay
    position. Bars of other symbols are closed by the chart's market session (the
    benchmark is chosen per market).
    """
    normalized = chart_document.normalize_chart_document(document)
    symbol = str(normalized["symbol"])
    benchmark = benchmark_symbol(normalized)
    errors: dict[str, str] = {}
    horizon = _replay_horizon(normalized)
    if horizon is not None:
        ohlc_loader = _replay_bounded_loader(ohlc_loader, horizon, str(normalized["market"]))

    try:
        benchmark_hist = ohlc_loader(benchmark, "1d")
        # Relative strength is a trading-day metric: use daily bars on both sides
        # even when the chart shows intraday or weekly bars.
        asset_daily = hist if normalized.get("timeframe") == "1d" else ohlc_loader(symbol, "1d")
        relative_strength = chart_analysis.relative_strength_summary(asset_daily, benchmark_hist)
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
    if horizon is not None:
        # 오더플로는 지금 이 순간의 체결 캡처라 커서 시점으로 되돌릴 수 없다 — 리플레이 중에는 격리한다.
        orderflow = {"ok": False, "reason": "replay_isolated"}
    elif orderflow_loader is not None:
        orderflow = _optional("orderflow", orderflow_loader, symbol,
                              {"ok": False, "reason": "provider_unavailable"}, errors)
    else:
        orderflow = {"ok": False, "reason": "capture_not_configured"}
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
    replay = normalized.get("replay") or {}
    return {
        "symbol": symbol,
        "benchmark": benchmark,
        "context": {
            "timeframe": str(normalized.get("timeframe") or ""),
            "session": str((normalized.get("session") or {}).get("policy") or ""),
            "timezone": str(normalized.get("timezone") or ""),
            "replay_cursor": replay.get("cursor") if replay.get("active") else None,
        },
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
