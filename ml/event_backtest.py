"""Event-driven backtest primitives.

Small shared engine for research tests. Existing strategy scripts can migrate
incrementally without changing their current outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Order:
    ts: Any
    symbol: str
    side: str
    qty: int


@dataclass(frozen=True)
class Fill:
    ts: Any
    symbol: str
    side: str
    qty: int
    price: float
    cost: float


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestSummary:
    fills: list[Fill]
    rejected: list[dict]
    positions: dict[str, int]
    final_nav: float
    cost_paid: float
    turnover: float
    nav_curve: pd.Series


def _price_columns(prices: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(prices.columns, pd.MultiIndex):
        try:
            out = prices.xs(symbol, axis=1, level=-1)
        except (KeyError, ValueError):
            out = prices.xs(symbol, axis=1, level=0)
        return out
    return prices


def _next_open(prices: pd.DataFrame, ts) -> tuple[Any, float] | None:
    later = prices.loc[prices.index > ts]
    if later.empty or "Open" not in later.columns:
        return None
    row = later.iloc[0]
    px = float(row["Open"])
    return (later.index[0], px) if px > 0 else None


def _mark_price(prices: pd.DataFrame, symbol: str) -> float:
    panel = _price_columns(prices, symbol)
    col = "Close" if "Close" in panel.columns else "Open"
    valid = panel[col].dropna()
    return float(valid.iloc[-1]) if len(valid) else 0.0


def simulate_orders(prices: pd.DataFrame, orders: list[Order], *, initial_cash: float,
                    market: str = "US", fill_mode: str = "next_open") -> BacktestSummary:
    if fill_mode != "next_open":
        raise ValueError("only fill_mode='next_open' is supported")
    from ml.adaptive import costs

    state = PortfolioState(cash=float(initial_cash))
    fills: list[Fill] = []
    rejected: list[dict] = []
    cost_paid = 0.0
    traded_notional = 0.0

    for order in sorted(orders, key=lambda item: item.ts):
        qty = int(order.qty)
        side = str(order.side).lower()
        if qty <= 0 or side not in {"buy", "sell"}:
            rejected.append({"order": order, "reason": "invalid_order"})
            continue
        panel = _price_columns(prices, order.symbol)
        nxt = _next_open(panel, order.ts)
        if nxt is None:
            rejected.append({"order": order, "reason": "no_next_bar"})
            continue
        fill_ts, price = nxt
        notional = price * qty
        fee = costs.order_cost(notional, side, market)
        held = int(state.positions.get(order.symbol, 0))
        if side == "buy":
            if state.cash < notional + fee:
                rejected.append({"order": order, "reason": "cash"})
                continue
            state.cash -= notional + fee
            state.positions[order.symbol] = held + qty
        else:
            if held < qty:
                rejected.append({"order": order, "reason": "shares"})
                continue
            state.cash += notional - fee
            state.positions[order.symbol] = held - qty
        cost_paid += fee
        traded_notional += notional
        fills.append(Fill(ts=fill_ts, symbol=order.symbol, side=side, qty=qty,
                          price=price, cost=fee))

    nav = state.cash + sum(qty * _mark_price(prices, sym) for sym, qty in state.positions.items())
    nav_curve = pd.Series([float(initial_cash), float(nav)], index=[prices.index[0], prices.index[-1]])
    turnover = traded_notional / max(float(initial_cash), 1e-9)
    return BacktestSummary(
        fills=fills,
        rejected=rejected,
        positions={k: v for k, v in state.positions.items() if v},
        final_nav=float(nav),
        cost_paid=float(cost_paid),
        turnover=float(turnover),
        nav_curve=nav_curve,
    )
