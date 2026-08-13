"""Typed chart series loading, normalization, and export helpers."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

import ticker_names
from dashboard import chart_document


_AXES = frozenset({"primary", "secondary"})
_NORMALIZATIONS = frozenset({"raw", "visible_start", "percent", "indexed"})
_FUNDAMENTAL_METRICS = frozenset({
    "revenue", "net_income", "margin", "eps_actual", "eps_est",
    "pe", "per", "forward_pe", "pbr", "psr", "ev_ebitda", "peg",
    "target_mean", "target_low", "target_high", "target_upside_pct",
    "recomm_mean", "revision_momentum", "n_analysts",
})


def _symbol(value: Any, fallback: str) -> str:
    normalized = ticker_names.normalize_input(str(value or "").strip())
    return normalized or fallback


def normalize_series_specs(raw, *, primary_symbol: str) -> list[dict[str, Any]]:
    """Normalize declarative series specs and guarantee one primary price series."""
    symbol = _symbol(primary_symbol, "MSFT")
    if raw is None:
        raw = []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("series specs must be a list")

    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    primary_count = 0
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"series[{index}] must be an object")
        spec = copy.deepcopy(dict(item))
        series_id = str(spec.get("id") or f"series-{index + 1}").strip()
        if not series_id:
            raise ValueError(f"series[{index}] id is required")
        if series_id in ids:
            raise ValueError(f"duplicate series id: {series_id}")
        ids.add(series_id)
        kind = str(spec.get("kind") or ("price" if series_id == "primary" else "benchmark")).strip().lower()
        if kind not in chart_document.SERIES_KINDS:
            raise ValueError(f"unsupported series kind: {kind}")
        if series_id == "primary" or kind == "price":
            if series_id != "primary" or kind != "price":
                raise ValueError("the price series must use id=primary and kind=price")
            primary_count += 1
            spec["symbol"] = symbol
        else:
            spec["symbol"] = _symbol(spec.get("symbol"), symbol)
        axis = str(spec.get("axis") or "primary")
        normalization = str(spec.get("normalization") or "raw")
        if axis not in _AXES:
            raise ValueError(f"unsupported series axis: {axis}")
        if normalization not in _NORMALIZATIONS:
            raise ValueError(f"unsupported series normalization: {normalization}")
        spec.update({
            "id": series_id,
            "kind": kind,
            "axis": axis,
            "normalization": normalization,
            "visible": bool(spec.get("visible", True)),
        })
        normalized.append(spec)
    if primary_count > 1:
        raise ValueError("series must contain exactly one primary price series")
    if primary_count == 0:
        normalized.insert(0, {
            "id": "primary", "kind": "price", "symbol": symbol,
            "axis": "primary", "normalization": "raw", "visible": True,
        })
    else:
        normalized.sort(key=lambda item: item["id"] != "primary")
    return normalized


def _clean_series(value: Any, *, preferred: Sequence[str] = ()) -> pd.Series | None:
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        column = next((name for name in preferred if name in value.columns), None)
        if column is None and len(value.columns) == 1:
            column = value.columns[0]
        if column is None:
            return None
        value = value[column]
    if not isinstance(value, pd.Series):
        return None
    out = pd.to_numeric(value, errors="coerce").dropna().copy(deep=True)
    if out.empty:
        return None
    try:
        out.index = pd.DatetimeIndex(out.index)
        if not out.index.is_unique:
            out = out.groupby(level=0, sort=False).last()
        out = out.sort_index(kind="mergesort")
    except Exception:
        pass
    return out


def _fundamental_series(payload: Any, metric: str) -> pd.Series | None:
    if metric not in _FUNDAMENTAL_METRICS:
        raise ValueError(f"unsupported fundamental metric: {metric}")
    if isinstance(payload, Mapping):
        rows = payload.get("quarterly") or payload.get("history") or payload.get("rows") or []
    else:
        rows = payload or []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return None
    dates: list[Any] = []
    values: list[float] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get(metric) is None:
            continue
        date = row.get("date") or row.get("period") or row.get("end_date") or row.get("as_of")
        try:
            value = float(row[metric])
            timestamp = pd.Timestamp(date)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(value) or pd.isna(timestamp):
            continue
        dates.append(timestamp)
        values.append(value)
    if not values:
        return None
    out = pd.Series(values, index=pd.DatetimeIndex(dates), name=metric, dtype=float)
    if not out.index.is_unique:
        out = out.groupby(level=0, sort=False).last()
    out = out.sort_index(kind="mergesort")
    out.attrs.update({"kind": "fundamental", "metric": metric})
    return out


def load_series(spec, *, price_loader, fundamental_loader, nav_loader) -> pd.Series | None:
    """Load one optional series; provider failures degrade to ``None``."""
    if not isinstance(spec, Mapping):
        raise ValueError("series spec must be an object")
    if not bool(spec.get("visible", True)):
        return None
    kind = str(spec.get("kind") or "benchmark").lower()
    symbol = str(spec.get("symbol") or "").strip()
    if kind not in chart_document.SERIES_KINDS:
        raise ValueError(f"unsupported series kind: {kind}")
    metric = str(spec.get("metric") or "").strip()
    if kind in {"fundamental", "analyst"}:
        if not metric:
            raise ValueError("fundamental series metric is required")
        if metric not in _FUNDAMENTAL_METRICS:
            raise ValueError(f"unsupported fundamental metric: {metric}")
    try:
        if kind in {"price", "benchmark", "peer"}:
            return _clean_series(price_loader(symbol), preferred=("Close", "close", "price"))
        if kind in {"fundamental", "analyst"}:
            return _fundamental_series(fundamental_loader(symbol), metric)
        if kind == "portfolio":
            return _clean_series(nav_loader(copy.deepcopy(dict(spec))), preferred=("NAV", "nav", "value", "Close"))
    except Exception:
        return None
    return None


def _visible_slice(series: pd.Series, cutoff: Any | None) -> pd.Series:
    out = _clean_series(series)
    if out is None:
        return pd.Series(dtype=float)
    if cutoff is not None:
        try:
            out = out.loc[out.index >= cutoff]
        except Exception:
            pass
    return out


def normalize_visible_series(primary, secondary, *, view_days: int | None) -> dict[str, pd.Series]:
    """Normalize all available series to zero at one common visible anchor."""
    primary_series = _clean_series(primary)
    if primary_series is None:
        return {"primary": pd.Series(dtype=float)}
    cutoff = None
    if view_days:
        try:
            cutoff = primary_series.index[-1] - pd.Timedelta(days=int(view_days))
        except Exception:
            cutoff = None
    available: dict[str, pd.Series] = {"primary": _visible_slice(primary_series, cutoff)}
    for name, value in dict(secondary or {}).items():
        series = _visible_slice(value, cutoff)
        if not series.empty:
            available[str(name)] = series

    common = None
    for series in available.values():
        index = series.dropna().index
        common = index if common is None else common.intersection(index)
    if common is not None and len(common):
        anchor = common.sort_values()[0]
    else:
        starts = [series.dropna().index[0] for series in available.values() if not series.dropna().empty]
        anchor = max(starts) if starts else None

    normalized: dict[str, pd.Series] = {}
    for name, series in available.items():
        candidates = series.loc[series.index >= anchor].dropna() if anchor is not None else series.dropna()
        if candidates.empty or float(candidates.iloc[0]) == 0:
            normalized[name] = series * float("nan")
            continue
        base = float(candidates.iloc[0])
        normalized[name] = (series / base - 1.0) * 100.0
        normalized[name].name = name
    return normalized


def series_export_frame(primary, secondary) -> pd.DataFrame:
    """Outer-join raw series for export without forward-filling sparse data."""
    columns: dict[str, pd.Series] = {}
    primary_series = _clean_series(primary)
    if primary_series is not None:
        columns["primary"] = primary_series
    for name, value in dict(secondary or {}).items():
        series = _clean_series(value)
        if series is not None:
            columns[str(name)] = series
    if not columns:
        return pd.DataFrame()
    return pd.concat(columns, axis=1).sort_index(kind="mergesort")
