"""Pure intraday position lifecycle helpers.

The engine owns IO and order placement; this module only decides which exit
legs should happen for a position on the latest confirmed bar.
"""
from __future__ import annotations


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _half_qty(qty: int) -> int:
    if qty <= 1:
        return qty
    return max(1, qty // 2)


def initialize_lifecycle(pos: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    entry = _as_float(pos.get("entry_price"))
    risk = max(_as_float(pos.get("risk_per_share")), 1e-9)
    initial_qty = _as_int(pos.get("initial_qty") or pos.get("qty"))
    lifecycle = pos.setdefault("lifecycle", {})
    lifecycle.setdefault("initial_qty", initial_qty)
    lifecycle.setdefault("partial_qty", _half_qty(initial_qty))
    lifecycle.setdefault("partial_target_taken", False)
    lifecycle.setdefault("partial_stop_taken", False)
    lifecycle.setdefault("partial_target_r", _as_float(cfg.get("partial_target_r"), 1.0))
    lifecycle.setdefault("full_target_r", _as_float(cfg.get("full_target_r"), 2.0))
    lifecycle.setdefault("partial_stop_r", _as_float(cfg.get("partial_stop_r"), 0.5))
    lifecycle.setdefault("full_stop_r", _as_float(cfg.get("full_stop_r"), 1.0))
    lifecycle.setdefault("breakeven_stop_r", _as_float(cfg.get("breakeven_stop_r"), 0.0))
    lifecycle.setdefault("partial_target_price", entry + lifecycle["partial_target_r"] * risk)
    lifecycle.setdefault("full_target_price", entry + lifecycle["full_target_r"] * risk)
    lifecycle.setdefault("partial_stop_price", entry - lifecycle["partial_stop_r"] * risk)
    lifecycle.setdefault("full_stop_price", entry - lifecycle["full_stop_r"] * risk)
    lifecycle.setdefault("breakeven_stop_price", entry + lifecycle["breakeven_stop_r"] * risk)
    return pos


def evaluate_exit_plan(pos: dict, bar: dict | None, score: float | None,
                       now_min: int, close_min: int, cfg: dict) -> list[dict]:
    initialize_lifecycle(pos, cfg)
    qty = _as_int(pos.get("qty"))
    if qty <= 0:
        return []

    from ml import intraday_axes

    lifecycle = pos["lifecycle"]
    if not bar:
        res = intraday_axes.check_exit(pos, bar, score, now_min, close_min, cfg)
        if not res:
            return []
        reason, ref_price = res
        return [{"reason": reason, "qty": qty, "ref_price": ref_price, "final": True}]

    high = _as_float(bar.get("h"))
    low = _as_float(bar.get("l"))
    close = _as_float(bar.get("c"))
    legs: list[dict] = []

    full_stop = _as_float(lifecycle.get("full_stop_price"))
    full_target = _as_float(lifecycle.get("full_target_price"))
    partial_stop = _as_float(lifecycle.get("partial_stop_price"))
    partial_target = _as_float(lifecycle.get("partial_target_price"))

    # Conservative path assumption: if a stop and a target are both touched in
    # the same confirmed bar, book the adverse exit first.
    if low <= full_stop:
        ref = min(full_stop, close) if close else full_stop
        return [{"reason": "stop", "qty": qty, "ref_price": ref, "final": True}]

    if (not lifecycle.get("partial_stop_taken")
            and not lifecycle.get("partial_target_taken")
            and low <= partial_stop):
        leg_qty = min(qty, _as_int(lifecycle.get("partial_qty"), _half_qty(qty)))
        legs.append({"reason": "partial_stop", "qty": leg_qty,
                     "ref_price": min(partial_stop, close) if close else partial_stop,
                     "final": leg_qty >= qty})
        qty = max(0, qty - leg_qty)
        if qty <= 0:
            return legs

    if high >= partial_target and not lifecycle.get("partial_target_taken"):
        leg_qty = min(qty, _as_int(lifecycle.get("partial_qty"), _half_qty(qty)))
        legs.append({"reason": "partial_target", "qty": leg_qty,
                     "ref_price": partial_target, "final": leg_qty >= qty})
        qty = max(0, qty - leg_qty)
        if qty <= 0:
            return legs

    if high >= full_target:
        legs.append({"reason": "target", "qty": qty, "ref_price": full_target, "final": True})
        return legs

    res = intraday_axes.check_exit(pos, bar, score, now_min, close_min, cfg)
    if not res:
        return legs
    reason, ref_price = res
    if reason in {"stop", "target"}:
        return legs
    legs.append({"reason": reason, "qty": qty, "ref_price": ref_price, "final": True})
    return legs


def apply_filled_leg(pos: dict, leg: dict, cfg: dict | None = None) -> dict:
    initialize_lifecycle(pos, cfg)
    qty = _as_int(pos.get("qty"))
    leg_qty = min(qty, _as_int(leg.get("qty")))
    pos["qty"] = max(0, qty - leg_qty)
    lifecycle = pos["lifecycle"]
    reason = leg.get("reason")
    if reason == "partial_target":
        lifecycle["partial_target_taken"] = True
        pos["stop"] = max(_as_float(pos.get("stop")), _as_float(lifecycle.get("breakeven_stop_price")))
    elif reason == "partial_stop":
        lifecycle["partial_stop_taken"] = True
    return pos
