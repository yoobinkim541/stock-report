"""Look-ahead-safe replay cursor and long-only simulated broker."""
from __future__ import annotations

import copy
import math
import uuid
from collections.abc import Mapping
from typing import Any

import pandas as pd

from ohlc_utils import normalize_ohlc_frame


_ORDER_TYPES = frozenset({"market", "limit", "stop"})
_SIDES = frozenset({"buy", "sell"})


def _finite(value: Any, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {value}") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"invalid number: {value}")
    return result


def _settings(raw: Mapping[str, Any] | None) -> dict[str, float]:
    value = dict(raw or {})
    out = {
        "fees_bps": _finite(value.get("fees_bps", 0)),
        "slippage_bps": _finite(value.get("slippage_bps", 0)),
        "max_leverage": _finite(value.get("max_leverage", 1), positive=True),
    }
    if out["fees_bps"] < 0 or out["slippage_bps"] < 0:
        raise ValueError("cost settings cannot be negative")
    return out


def new_session(*, symbol: str, timeframe: str, cursor: int, initial_cash: float,
                settings: Mapping[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
    cash = _finite(initial_cash, positive=True)
    cursor = int(cursor)
    if cursor < 0:
        raise ValueError("cursor must be nonnegative")
    session_id = str(session_id or uuid.uuid4().hex)
    return {
        "version": 1,
        "id": session_id,
        "parent_id": None,
        "symbol": str(symbol).strip().upper(),
        "timeframe": str(timeframe).strip().lower(),
        "cursor": cursor,
        "initial_cash": cash,
        "cash": cash,
        "settings": _settings(settings),
        "orders": [],
        "fills": [],
        "positions": {},
        "realized_pnl": 0.0,
        "events": [{"type": "session_created", "cursor": cursor}],
        "metrics": {"nav": cash, "peak_nav": cash, "gross_exposure": 0.0, "drawdown": 0.0, "max_drawdown": 0.0},
    }


def _normalized_order(raw: Mapping[str, Any], *, cursor: int, symbol: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("order must be an object")
    order = copy.deepcopy(dict(raw))
    order_id = str(order.get("id") or uuid.uuid4().hex).strip()
    order_type = str(order.get("type") or "market").strip().lower()
    side = str(order.get("side") or "").strip().lower()
    qty = int(order.get("qty") or 0)
    if not order_id or order_type not in _ORDER_TYPES or side not in _SIDES or qty <= 0:
        raise ValueError("invalid order")
    price = None
    if order_type in {"limit", "stop"}:
        price = _finite(order.get("price"), positive=True)
    bracket = order.get("bracket")
    if bracket is not None:
        if side != "buy" or not isinstance(bracket, Mapping):
            raise ValueError("bracket requires a long entry")
        stop = _finite(bracket.get("stop"), positive=True)
        target = _finite(bracket.get("target"), positive=True)
        if stop >= target:
            raise ValueError("bracket stop must be below target")
        bracket = {"stop": stop, "target": target}
    return {
        "id": order_id,
        "symbol": str(order.get("symbol") or symbol).strip().upper(),
        "type": order_type,
        "side": side,
        "qty": qty,
        "price": price,
        "bracket": bracket,
        "status": "pending",
        "submitted_cursor": cursor,
        "active_after_cursor": cursor,
        "parent_id": order.get("parent_id"),
        "oco_group": order.get("oco_group"),
        "role": order.get("role"),
        "reason": None,
    }


def submit_order(session: Mapping[str, Any], order: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(session))
    normalized = _normalized_order(order, cursor=int(out["cursor"]), symbol=str(out["symbol"]))
    if any(row.get("id") == normalized["id"] for row in out.get("orders") or []):
        raise ValueError(f"duplicate order id: {normalized['id']}")
    out["orders"].append(normalized)
    out["events"].append({"type": "order_submitted", "order_id": normalized["id"], "cursor": out["cursor"]})
    return out


def preview_order_price_patch(
    session: Mapping[str, Any],
    order_id: str,
    price: Any,
) -> dict[str, Any]:
    order_id = str(order_id or "").strip()
    order = next((row for row in session.get("orders") or [] if str(row.get("id")) == order_id), None)
    if order is None:
        raise ValueError("order not found")
    if order.get("status") != "pending" or order.get("type") not in {"limit", "stop"}:
        raise ValueError("order price is not editable")
    after = _finite(price, positive=True)
    before = _finite(order.get("price"), positive=True)
    parent_id = order.get("parent_id")
    siblings = [
        row for row in session.get("orders") or []
        if row.get("parent_id") == parent_id and row.get("status") == "pending"
    ] if parent_id else []
    if order.get("role") == "stop":
        target = next((row for row in siblings if row.get("role") == "target"), None)
        if target and after >= float(target["price"]):
            raise ValueError("bracket stop must remain below target")
    elif order.get("role") == "target":
        stop = next((row for row in siblings if row.get("role") == "stop"), None)
        if stop and after <= float(stop["price"]):
            raise ValueError("bracket target must remain above stop")
    return {
        "order_id": order_id,
        "role": order.get("role"),
        "before": before,
        "after": after,
    }


def apply_order_price_patch(
    session: Mapping[str, Any],
    order_id: str,
    price: Any,
) -> dict[str, Any]:
    preview = preview_order_price_patch(session, order_id, price)
    out = copy.deepcopy(dict(session))
    order = next(row for row in out["orders"] if str(row.get("id")) == preview["order_id"])
    order["price"] = preview["after"]
    out["events"].append({
        "type": "order_price_patched",
        "cursor": int(out.get("cursor") or 0),
        **preview,
    })
    return out


def _order_price(order: Mapping[str, Any], row: Mapping[str, Any], *, slippage_bps: float) -> float | None:
    order_type, side = order["type"], order["side"]
    opening, high, low = (float(row[key]) for key in ("Open", "High", "Low"))
    if order_type == "market":
        base = opening
    elif order_type == "limit":
        limit = float(order["price"])
        if (side == "buy" and low > limit) or (side == "sell" and high < limit):
            return None
        base = limit
    else:
        stop = float(order["price"])
        if (side == "buy" and high < stop) or (side == "sell" and low > stop):
            return None
        base = max(opening, stop) if side == "buy" else min(opening, stop)
    direction = 1.0 if side == "buy" else -1.0
    return base * (1.0 + direction * slippage_bps / 10_000.0)


def _reject(order: dict[str, Any], reason: str, session: dict[str, Any], cursor: int) -> None:
    order.update({"status": "rejected", "reason": reason, "resolved_cursor": cursor})
    session["events"].append({"type": "order_rejected", "order_id": order["id"], "reason": reason, "cursor": cursor})


def _create_bracket_children(session: dict[str, Any], order: Mapping[str, Any], cursor: int) -> None:
    bracket = order.get("bracket")
    if not bracket:
        return
    group = f"oco:{order['id']}"
    for role, order_type, price in (("stop", "stop", bracket["stop"]), ("target", "limit", bracket["target"])):
        child = _normalized_order(
            {"id": f"{order['id']}:{role}", "type": order_type, "side": "sell", "qty": order["qty"],
             "price": price, "parent_id": order["id"], "oco_group": group, "role": role},
            cursor=cursor,
            symbol=str(order["symbol"]),
        )
        child["active_after_cursor"] = cursor
        session["orders"].append(child)


def _cancel_oco_siblings(session: dict[str, Any], filled: Mapping[str, Any], cursor: int) -> None:
    group = filled.get("oco_group")
    if not group:
        return
    for order in session["orders"]:
        if order.get("oco_group") == group and order.get("id") != filled.get("id") and order.get("status") == "pending":
            order.update({"status": "cancelled", "reason": "oco", "resolved_cursor": cursor})
            session["events"].append({"type": "order_cancelled", "order_id": order["id"], "reason": "oco", "cursor": cursor})


def _reconcile_bracket_quantities(session: dict[str, Any], *, symbol: str, cursor: int) -> None:
    remaining = int((session["positions"].get(symbol) or {}).get("qty") or 0)
    for order in session["orders"]:
        if (
            order.get("status") != "pending"
            or order.get("side") != "sell"
            or order.get("symbol") != symbol
            or not order.get("parent_id")
        ):
            continue
        if remaining <= 0:
            order.update({"status": "cancelled", "reason": "position_flat", "resolved_cursor": cursor})
            session["events"].append({
                "type": "order_cancelled", "order_id": order["id"],
                "reason": "position_flat", "cursor": cursor,
            })
        elif int(order["qty"]) > remaining:
            previous_qty = int(order["qty"])
            order["qty"] = remaining
            session["events"].append({
                "type": "order_resized", "order_id": order["id"],
                "previous_qty": previous_qty, "qty": remaining, "cursor": cursor,
            })


def _execute(session: dict[str, Any], order: dict[str, Any], price: float,
             timestamp: Any, cursor: int, mark_price: float) -> None:
    qty, side, symbol = int(order["qty"]), str(order["side"]), str(order["symbol"])
    fee = price * qty * float(session["settings"]["fees_bps"]) / 10_000.0
    position = session["positions"].get(symbol) or {"qty": 0, "avg_price": 0.0}
    held = int(position["qty"])
    if side == "sell" and held < qty:
        _reject(order, "shares", session, cursor)
        return
    if side == "buy":
        existing_exposure = sum(float(item["qty"]) * mark_price for item in session["positions"].values())
        current_nav = float(session["cash"]) + existing_exposure
        next_exposure = existing_exposure + qty * price
        if next_exposure > max(current_nav, 0.0) * float(session["settings"]["max_leverage"]) + 1e-9:
            _reject(order, "leverage", session, cursor)
            return
        total_qty = held + qty
        avg = (held * float(position["avg_price"]) + qty * price) / total_qty
        session["cash"] -= qty * price + fee
        session["positions"][symbol] = {"qty": total_qty, "avg_price": avg}
    else:
        session["cash"] += qty * price - fee
        session["realized_pnl"] += (price - float(position["avg_price"])) * qty - fee
        remaining = held - qty
        if remaining:
            session["positions"][symbol] = {"qty": remaining, "avg_price": float(position["avg_price"])}
        else:
            session["positions"].pop(symbol, None)
    order.update({"status": "filled", "resolved_cursor": cursor, "reason": None})
    fill = {
        "order_id": order["id"], "symbol": symbol, "side": side, "qty": qty,
        "price": price, "fee": fee, "cursor": cursor, "timestamp": pd.Timestamp(timestamp).isoformat(),
    }
    session["fills"].append(fill)
    session["events"].append({"type": "fill", **fill})
    _cancel_oco_siblings(session, order, cursor)
    if side == "sell":
        _reconcile_bracket_quantities(session, symbol=symbol, cursor=cursor)
    _create_bracket_children(session, order, cursor)


def _mark_metrics(session: dict[str, Any], close: float) -> None:
    exposure = sum(float(item["qty"]) * close for item in session["positions"].values())
    nav = float(session["cash"]) + exposure
    previous = session.get("metrics") or {}
    peak = max(float(previous.get("peak_nav") or session["initial_cash"]), nav)
    drawdown = nav / peak - 1.0 if peak > 0 else 0.0
    session["metrics"] = {
        "nav": nav,
        "peak_nav": peak,
        "gross_exposure": exposure,
        "drawdown": drawdown,
        "max_drawdown": min(float(previous.get("max_drawdown") or 0.0), drawdown),
    }


def advance(session: Mapping[str, Any], bars, *, steps: int = 1) -> dict[str, Any]:
    out = copy.deepcopy(dict(session))
    frame = normalize_ohlc_frame(bars)
    if frame is None or frame.empty:
        return out
    steps = int(steps)
    if steps < 0:
        raise ValueError("rewind requires branch_session")
    target = min(len(frame) - 1, int(out["cursor"]) + steps)
    while int(out["cursor"]) < target:
        cursor = int(out["cursor"]) + 1
        row = frame.iloc[cursor]
        # Stop children precede targets so a same-bar bracket collision is conservative.
        pending = sorted(
            [order for order in out["orders"] if order.get("status") == "pending"],
            key=lambda order: (0 if order.get("role") == "stop" else 1, int(order.get("submitted_cursor") or 0)),
        )
        for order in pending:
            if order.get("status") != "pending" or cursor <= int(order.get("active_after_cursor") or 0):
                continue
            price = _order_price(order, row, slippage_bps=float(out["settings"]["slippage_bps"]))
            if price is not None:
                _execute(out, order, price, frame.index[cursor], cursor, float(row["Close"]))
        out["cursor"] = cursor
        _mark_metrics(out, float(row["Close"]))
        out["events"].append({"type": "cursor_advanced", "cursor": cursor, "timestamp": pd.Timestamp(frame.index[cursor]).isoformat()})
    return out


def set_cursor(session: Mapping[str, Any], cursor: int) -> dict[str, Any]:
    if int(cursor) < int(session.get("cursor") or 0):
        raise ValueError("rewind requires a new branch")
    out = copy.deepcopy(dict(session))
    out["cursor"] = int(cursor)
    return out


def _rebuild_account(session: dict[str, Any], *, mark_price: float | None = None) -> None:
    cash = float(session["initial_cash"])
    positions: dict[str, dict[str, Any]] = {}
    realized = 0.0
    for fill in sorted(session.get("fills") or [], key=lambda item: int(item.get("cursor") or 0)):
        symbol, side, qty = str(fill["symbol"]), str(fill["side"]), int(fill["qty"])
        price, fee = float(fill["price"]), float(fill.get("fee") or 0.0)
        position = positions.get(symbol) or {"qty": 0, "avg_price": 0.0}
        held = int(position["qty"])
        if side == "buy":
            total = held + qty
            avg = (held * float(position["avg_price"]) + qty * price) / total
            positions[symbol] = {"qty": total, "avg_price": avg}
            cash -= qty * price + fee
        else:
            if held < qty:
                raise ValueError("branch fill history contains a negative position")
            realized += (price - float(position["avg_price"])) * qty - fee
            cash += qty * price - fee
            if held == qty:
                positions.pop(symbol, None)
            else:
                positions[symbol] = {"qty": held - qty, "avg_price": float(position["avg_price"])}
    session["cash"] = cash
    session["positions"] = positions
    session["realized_pnl"] = realized
    if mark_price is None:
        exposure = sum(float(item["qty"]) * float(item["avg_price"]) for item in positions.values())
        nav = cash + exposure
        session["metrics"] = {
            "nav": nav, "peak_nav": max(float(session["initial_cash"]), nav),
            "gross_exposure": exposure, "drawdown": 0.0, "max_drawdown": 0.0,
        }
    else:
        session["metrics"] = {
            "nav": float(session["initial_cash"]), "peak_nav": float(session["initial_cash"]),
            "gross_exposure": 0.0, "drawdown": 0.0, "max_drawdown": 0.0,
        }
        _mark_metrics(session, float(mark_price))


def branch_session(session: Mapping[str, Any], *, cursor: int, session_id: str | None = None,
                   bars=None) -> dict[str, Any]:
    cursor = int(cursor)
    if cursor < 0 or cursor > int(session.get("cursor") or 0):
        raise ValueError("branch cursor is outside session history")
    out = copy.deepcopy(dict(session))
    out["id"] = str(session_id or uuid.uuid4().hex)
    out["parent_id"] = str(session.get("id") or "")
    out["cursor"] = cursor
    out["events"] = [copy.deepcopy(event) for event in session.get("events") or [] if int(event.get("cursor") or 0) <= cursor]
    out["events"].append({"type": "session_branched", "cursor": cursor, "parent_id": out["parent_id"]})
    out["orders"] = [copy.deepcopy(order) for order in session.get("orders") or [] if int(order.get("submitted_cursor") or 0) <= cursor]
    for order in out["orders"]:
        if int(order.get("resolved_cursor") or cursor + 1) > cursor:
            order["status"] = "pending"
            order["reason"] = None
            order.pop("resolved_cursor", None)
    out["fills"] = [copy.deepcopy(fill) for fill in session.get("fills") or [] if int(fill.get("cursor") or 0) <= cursor]
    mark_price = None
    if bars is not None:
        frame = normalize_ohlc_frame(bars)
        if frame is not None and not frame.empty and cursor < len(frame):
            mark_price = float(frame.iloc[cursor]["Close"])
    _rebuild_account(out, mark_price=mark_price)
    return out
