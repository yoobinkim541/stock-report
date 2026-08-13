"""Pure order-flow coverage, analytics, and Plotly figure adapters."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import plotly.graph_objects as go

from providers import orderflow_store
from providers.intraday_bars import base_symbol


def _levels(value: Any) -> list[list[float]]:
    out: list[list[float]] = []
    for row in value or []:
        try:
            price, quantity = float(row[0]), float(row[1])
        except (IndexError, TypeError, ValueError):
            continue
        if price > 0 and quantity >= 0:
            out.append([price, quantity])
    return out


def build_snapshot(symbol: str, events: Iterable[Mapping[str, Any]], *, now: float | None = None) -> dict[str, Any]:
    target = base_symbol(symbol)
    rows = [dict(row) for row in events if base_symbol(str(row.get("symbol") or "")) == target]
    coverage = orderflow_store.coverage(rows)
    books = [row for row in rows if row.get("event_type") == "book"]
    latest_book = max(books, key=lambda row: float(row.get("received_at") or 0), default=None)
    book = None
    if latest_book:
        bids, asks = _levels(latest_book.get("bids")), _levels(latest_book.get("asks"))
        bid_total = sum(row[1] for row in bids)
        ask_total = sum(row[1] for row in asks)
        denominator = bid_total + ask_total
        best_bid = latest_book.get("best_bid") or (bids[0][0] if bids else None)
        best_ask = latest_book.get("best_ask") or (asks[0][0] if asks else None)
        book = {
            "bids": bids,
            "asks": asks,
            "best_bid": float(best_bid) if best_bid is not None else None,
            "best_ask": float(best_ask) if best_ask is not None else None,
            "spread": float(best_ask) - float(best_bid) if best_bid is not None and best_ask is not None else None,
            "bid_quantity": bid_total,
            "ask_quantity": ask_total,
            "imbalance": (bid_total - ask_total) / denominator if denominator else None,
            "received_at": latest_book.get("received_at"),
            "age_seconds": max(0.0, float(now) - float(latest_book.get("received_at") or 0)) if now is not None else None,
        }
    volume_by_price: dict[float, float] = defaultdict(float)
    for row in rows:
        if row.get("event_type") != "trade":
            continue
        if row.get("volume_anomaly") and row.get("volume_method") != "provider_trade_size":
            continue
        try:
            price, size = float(row.get("price")), float(row.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0 and size > 0:
            volume_by_price[price] += size
    profile = [{"price": price, "volume": volume_by_price[price]} for price in sorted(volume_by_price)]
    blocked = {}
    if not coverage["capabilities"]["footprint"]:
        blocked["footprint"] = "authoritative_aggressor_side_unavailable"
        blocked["bid_ask_delta"] = "authoritative_aggressor_side_unavailable"
    return {
        "ok": bool(rows),
        "reason": None if rows else "capture_empty",
        "symbol": target,
        "coverage": coverage,
        "book": book,
        "volume_profile": profile,
        "blocked": blocked,
    }


def load_snapshot(
    symbol: str,
    *,
    date_utc: str | None = None,
    base_dir: Path | str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    date = date_utc or orderflow_store.current_session_date(symbol, now=timestamp)
    events, storage_window = orderflow_store.load_event_window(symbol, date, base_dir=base_dir)
    snapshot = build_snapshot(symbol, events, now=timestamp)
    snapshot["coverage"]["storage_window"] = storage_window
    return snapshot


def depth_figure(snapshot: Mapping[str, Any]) -> go.Figure:
    book = snapshot.get("book") or {}
    bids = sorted(_levels(book.get("bids")), key=lambda row: row[0])
    asks = sorted(_levels(book.get("asks")), key=lambda row: row[0])
    figure = go.Figure()
    figure.add_bar(
        name="매수 잔량",
        x=[row[1] for row in bids],
        y=[row[0] for row in bids],
        orientation="h",
        marker_color="#22c55e",
    )
    figure.add_bar(
        name="매도 잔량",
        x=[-row[1] for row in asks],
        y=[row[0] for row in asks],
        orientation="h",
        marker_color="#ef4444",
    )
    figure.update_layout(
        barmode="relative",
        height=310,
        margin=dict(l=8, r=8, t=24, b=8),
        xaxis_title="잔량 (매도는 음수 방향)",
        yaxis_title="가격",
        legend_orientation="h",
        template="plotly_dark",
    )
    return figure


def volume_profile_figure(snapshot: Mapping[str, Any]) -> go.Figure:
    rows = list(snapshot.get("volume_profile") or [])
    figure = go.Figure()
    figure.add_bar(
        name="체결량",
        x=[float(row.get("volume") or 0) for row in rows],
        y=[float(row.get("price")) for row in rows],
        orientation="h",
        marker_color="#38bdf8",
    )
    figure.update_layout(
        height=310,
        margin=dict(l=8, r=8, t=24, b=8),
        xaxis_title="누적 체결량",
        yaxis_title="가격",
        showlegend=False,
        template="plotly_dark",
    )
    return figure
