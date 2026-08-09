"""Renderer adapter from versioned ChartDocument state to Plotly figures."""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from dashboard import chart_document, chart_transforms, charts, theme


@dataclass(frozen=True)
class RenderedChart:
    figure: Any
    frame: pd.DataFrame
    document: dict[str, Any]
    transform: chart_transforms.ChartTransformResult
    warnings: tuple[str, ...]


_PLOTLY_KIND = {
    "line": "line",
    "area": "line",
    "baseline": "line",
    "kagi": "line",
    "candlestick": "candle",
    "hollow_candle": "candle",
    "heikin_ashi": "candle",
    "bars": "ohlc",
    "high_low": "ohlc",
    "renko": "candle",
    "line_break": "candle",
    "range": "candle",
}
_PRICE_SEQUENCE_TYPES = frozenset({"renko", "kagi", "line_break", "range"})


def _replay_trade_records(session: Mapping[str, Any], symbol: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for fill in session.get("fills") or []:
        if str(fill.get("symbol") or "").upper() != symbol:
            continue
        records.append({
            "event_id": fill.get("order_id"),
            "side": fill.get("side"),
            "qty": fill.get("qty"),
            "price": fill.get("price"),
            "timestamp": fill.get("timestamp"),
            "avg_price": fill.get("price"),
            "account": "Replay",
            "source": "chart_replay",
            "note": f"cursor {fill.get('cursor')}",
            "currency": "USD",
        })
    return records


def _add_replay_price_lines(figure, session: Mapping[str, Any], symbol: str) -> None:
    definitions = {
        "stop": (charts._RED, "손절"),
        "target": (charts._GREEN, "목표"),
        None: ("#2f81f7", "주문"),
    }
    for order in session.get("orders") or []:
        if (
            str(order.get("symbol") or "").upper() != symbol
            or order.get("status") != "pending"
            or order.get("type") not in {"limit", "stop"}
        ):
            continue
        price = _positive_price(order.get("price"))
        if price is None:
            continue
        color, label = definitions.get(order.get("role"), definitions[None])
        figure.add_shape(
            type="line", name=f"replay-order:{order['id']}",
            xref="x domain", x0=0, x1=1, yref="y", y0=price, y1=price,
            editable=True, layer="above",
            line=dict(color=color, width=1.4, dash="dash"),
        )
        figure.add_annotation(
            name=f"replay-order-label:{order['id']}",
            xref="x domain", x=1, xanchor="left", yref="y", y=price,
            showarrow=False, text=f"{label} {price:,.2f} · {int(order['qty'])}주",
            font=dict(color=color, size=10), bgcolor="rgba(19,23,34,.82)", borderpad=3,
        )
    position = (session.get("positions") or {}).get(symbol)
    if isinstance(position, Mapping) and int(position.get("qty") or 0) > 0:
        avg_price = _positive_price(position.get("avg_price"))
        if avg_price is not None:
            figure.add_shape(
                type="line", name=f"replay-position:{symbol}",
                xref="x domain", x0=0, x1=1, yref="y", y0=avg_price, y1=avg_price,
                editable=False, layer="below",
                line=dict(color=theme.AXIS_TEXT, width=1.2, dash="dot"),
            )
            figure.add_annotation(
                name=f"replay-position-label:{symbol}",
                xref="x domain", x=0, xanchor="left", yref="y", y=avg_price,
                showarrow=False, text=f"포지션 {int(position['qty'])}주 · {avg_price:,.2f}",
                font=dict(color=theme.AXIS_TEXT, size=10), bgcolor="rgba(19,23,34,.72)", borderpad=3,
            )


def _positive_price(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _source_position(frame: pd.DataFrame, value: Any) -> int | None:
    if frame.empty or "SourceTimestamp" not in frame or value is None:
        return None
    source = pd.to_datetime(frame["SourceTimestamp"], errors="coerce", utc=True)
    try:
        target = pd.to_datetime(value, errors="raise", utc=True)
    except Exception:
        return None
    valid = source.notna()
    if not bool(valid.any()):
        return None
    distance = (source[valid] - target).abs()
    nearest = distance[distance == distance.min()].index[-1]
    try:
        return int(frame.index.get_loc(nearest))
    except (KeyError, TypeError, ValueError):
        return None


def _marker_payload(frame: pd.DataFrame, position: int, record: Mapping[str, Any]) -> dict[str, Any]:
    source = frame.iloc[position].get("SourceTimestamp")
    return {
        "source_timestamp": pd.Timestamp(source).isoformat() if source is not None else None,
        "input_timestamp": str(record.get("timestamp") or record.get("date") or ""),
        "record": copy.deepcopy(dict(record)),
    }


def _add_sequence_trade_markers(figure, frame: pd.DataFrame, records: Sequence[Mapping[str, Any]]) -> None:
    go = charts._go()
    definitions = {
        "buy": (charts._GREEN, "triangle-up", "Buy"),
        "sell": (charts._RED, "triangle-down", "Sell"),
        "alert": ("#f59e0b", "diamond", "Alert"),
    }
    for side, (color, symbol, name) in definitions.items():
        xs: list[Any] = []
        ys: list[float] = []
        custom: list[dict[str, Any]] = []
        for record in records:
            if str(record.get("side") or "").lower() != side:
                continue
            position = _source_position(frame, record.get("timestamp") or record.get("date"))
            if position is None:
                continue
            price = _positive_price(record.get("price"))
            if price is None:
                price = _positive_price(frame.iloc[position].get("Close"))
            if price is None:
                continue
            xs.append(frame.index[position])
            ys.append(price)
            custom.append(_marker_payload(frame, position, record))
        if xs:
            figure.add_trace(go.Scatter(
                x=xs,
                y=ys,
                mode="markers",
                name=name,
                showlegend=False,
                customdata=custom,
                marker=dict(symbol=symbol, size=13, color=color, line=dict(color="#ffffff", width=0.8)),
                hovertemplate="%{customdata.source_timestamp}<extra>" + name + "</extra>",
            ))


def _add_sequence_event_markers(figure, frame: pd.DataFrame, records: Sequence[Mapping[str, Any]]) -> None:
    go = charts._go()
    groups: dict[tuple[str, str], dict[str, list[Any]]] = {}
    for record in records:
        position = _source_position(frame, record.get("date") or record.get("timestamp"))
        if position is None:
            continue
        row = frame.iloc[position]
        price = _positive_price(row.get("Low")) or _positive_price(row.get("Close"))
        if price is None:
            continue
        marker = str(record.get("marker") or "E")[:1]
        color = str(record.get("color") or theme.AXIS_TEXT)
        group = groups.setdefault((marker, color), {"x": [], "y": [], "custom": []})
        group["x"].append(frame.index[position])
        group["y"].append(price * 0.985)
        group["custom"].append(_marker_payload(frame, position, record))
    for (marker, color), group in groups.items():
        figure.add_trace(go.Scatter(
            x=group["x"],
            y=group["y"],
            mode="markers+text",
            name=f"이벤트 {marker}",
            showlegend=False,
            text=[marker] * len(group["x"]),
            customdata=group["custom"],
            textfont=dict(size=8, color="#ffffff"),
            marker=dict(symbol="circle", size=11, color=color, opacity=0.9),
            hovertemplate="%{customdata.source_timestamp}<extra></extra>",
        ))


def _render_kwargs(document: Mapping[str, Any], chart_type: str,
                   chart_kwargs: Mapping[str, Any] | None) -> dict[str, Any]:
    kwargs = copy.deepcopy(dict(chart_kwargs or {}))
    kwargs.pop("kind", None)
    kwargs.pop("ticker", None)
    kwargs["kind"] = _PLOTLY_KIND[chart_type]
    kwargs.setdefault("log_scale", document.get("scale", {}).get("type") == "log")
    if chart_type == "area":
        kwargs["line_fill"] = "tozeroy"
    elif chart_type == "baseline":
        kwargs.setdefault("baseline", None)
    elif chart_type == "hollow_candle":
        kwargs["hollow"] = True
    return kwargs


def render_plotly_chart(document, hist, *, chart_kwargs: Mapping[str, Any] | None = None) -> RenderedChart:
    """Validate, transform, and render a ChartDocument without mutating callers."""
    if chart_kwargs is not None and not isinstance(chart_kwargs, Mapping):
        raise ValueError("chart_kwargs must be a mapping")
    normalized = chart_document.normalize_chart_document(document)
    errors, document_warnings = chart_document.validate_chart_document(normalized)
    if errors:
        raise ValueError("; ".join(errors))

    requested_type = str(normalized["chart"]["type"])
    params = normalized["chart"].get("params") or {}
    kwargs = copy.deepcopy(dict(chart_kwargs or {}))
    compare = kwargs.get("compare") or {}
    replay_session = kwargs.pop("replay_session", None)
    if replay_session is not None and not isinstance(replay_session, Mapping):
        raise ValueError("replay_session must be a mapping")
    warnings = list(document_warnings)
    render_type = requested_type
    render_params = params
    if compare and requested_type in _PRICE_SEQUENCE_TYPES:
        render_type = "line"
        render_params = {}
        warnings.append(
            "Compare mode does not support sequence-based synthetic prices; normalized line comparison was used.",
        )
    if compare and replay_session:
        warnings.append("Replay order and position overlays are hidden in normalized compare mode.")
        replay_session = None
    if replay_session:
        kwargs["trades"] = [
            *(kwargs.get("trades") or []),
            *_replay_trade_records(replay_session, str(normalized["symbol"])),
        ]

    transform = chart_transforms.transform_chart(hist, render_type, render_params)
    frame = transform.frame.copy(deep=True)
    render_frame = frame.copy(deep=True)
    sequence = transform.x_mode == "sequence"
    trades = kwargs.pop("trades", None) if sequence else None
    events = kwargs.pop("events", None) if sequence else None
    if sequence:
        kwargs["view_days"] = None

    kwargs = _render_kwargs(normalized, render_type, kwargs)
    if requested_type == "baseline" and kwargs.get("baseline") is None and not frame.empty:
        close = pd.to_numeric(frame.get("Close"), errors="coerce").dropna()
        kwargs["baseline"] = float(close.iloc[0]) if not close.empty else None
    figure = charts.price_chart(render_frame, normalized["symbol"], **kwargs)

    if replay_session:
        _add_replay_price_lines(figure, replay_session, str(normalized["symbol"]))

    if sequence:
        figure.update_xaxes(
            type="category",
            categoryorder="array",
            categoryarray=list(frame.index),
            rangebreaks=[],
        )
        if trades:
            _add_sequence_trade_markers(figure, frame, trades)
        if events:
            _add_sequence_event_markers(figure, frame, events)

    precision = str(transform.metadata.get("source_precision") or "")
    if precision == "ohlcv_close_path":
        warnings.append(
            "Synthetic prices use the OHLCV close path; SourceTimestamp is an anchor, not tick-level precision.",
        )
    elif transform.synthetic:
        warnings.append("Synthetic prices are reconstructed from OHLCV bars and are not executable prices.")

    return RenderedChart(
        figure=figure,
        frame=frame,
        document=normalized,
        transform=transform,
        warnings=tuple(warnings),
    )
