#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified trade-event ledger for chart overlays.

This is a display/audit ledger, not a broker order system.  It records confirmed
mock orders, broker-sync inferred position changes, and manual portfolio edits
so the dashboard can draw buy/sell markers on ticker charts.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

COLLECTION = "trade_events"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _compact(text, limit=140) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _to_float(value, default=None):
    try:
        if value is None:
            return default
        number = float(value)
        if number != number:
            return default
        return number
    except (TypeError, ValueError):
        return default


def _symbol(ticker: str) -> str:
    t = str(ticker or "").upper().strip()
    return t.replace(".KS", "").replace(".KQ", "")


def _event_id(rec: dict) -> str:
    raw = "|".join(str(rec.get(k, "")) for k in (
        "timestamp", "ticker", "side", "qty", "price", "account", "source", "broker_order_id", "note"
    ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def normalize_trade(*, ticker: str, side: str, qty, price=None, avg_price=None,
                    account: str = "", source: str = "manual", market: str = "US",
                    currency: str | None = None, timestamp: str | None = None,
                    broker_order_id: str | None = None, confirmed: bool = True,
                    note: str = "", raw: dict | None = None,
                    event_id: str | None = None) -> dict:
    side = str(side or "").lower().strip()
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        raise ValueError("ticker is required")
    quantity = _to_float(qty)
    if quantity is None or quantity <= 0:
        raise ValueError("qty must be positive")
    ts = timestamp or _now()
    rec = {
        "timestamp": ts,
        "date": ts[:10],
        "ticker": ticker,
        "symbol": _symbol(ticker),
        "side": side,
        "qty": quantity,
        "price": _to_float(price),
        "avg_price": _to_float(avg_price),
        "account": account,
        "source": source,
        "market": market,
        "currency": currency or ("KRW" if market == "KR" else "USD"),
        "broker_order_id": broker_order_id,
        "confirmed": bool(confirmed),
        "note": _compact(note),
    }
    if raw:
        rec["raw"] = raw
    rec["event_id"] = event_id or _event_id(rec)
    return rec


def record_trade(**kwargs) -> dict:
    rec = normalize_trade(**kwargs)
    try:
        import store
        existing = {r.get("event_id") for r in store.all(COLLECTION)}
        if rec["event_id"] not in existing:
            store.append(COLLECTION, rec)
    except Exception:
        # Chart annotations are useful but must never break portfolio/order paths.
        pass
    return rec


def all_trades() -> list[dict]:
    try:
        import store
        rows = store.all(COLLECTION)
    except Exception:
        return []
    return sorted(rows, key=lambda r: str(r.get("timestamp") or r.get("date") or ""))


def trades_for_ticker(ticker: str, *, include_mock: bool = True) -> list[dict]:
    target = _symbol(ticker)
    if not target:
        return []
    rows = []
    for r in all_trades():
        if _symbol(r.get("ticker") or r.get("symbol")) != target:
            continue
        src = str(r.get("source") or "")
        acct = str(r.get("account") or "")
        if not include_mock and ("mock" in src or "mock" in acct):
            continue
        rows.append(r)
    return rows


def import_mock_history(collection: str, *, market: str) -> int:
    """Best-effort one-shot import from legacy mock order history rows."""
    try:
        import store
        rows = store.all(collection)
    except Exception:
        return 0
    added = 0
    for r in rows:
        if r.get("kind") != "order" or not r.get("ok"):
            continue
        ticker = r.get("ticker") or r.get("symbol") or r.get("code")
        if market == "KR" and ticker and "." not in str(ticker):
            ticker = f"{ticker}.KS"
        try:
            rec = record_trade(
                ticker=ticker,
                side=r.get("side"),
                qty=r.get("qty"),
                price=r.get("price"),
                account=f"{market.lower()}_mock",
                source=f"{market.lower()}_mock_history",
                market=market,
                timestamp=r.get("date"),
                confirmed=True,
                note=r.get("reason", ""),
                event_id=f"legacy:{collection}:{r.get('date')}:{ticker}:{r.get('side')}:{r.get('qty')}",
            )
            added += 1 if rec else 0
        except Exception:
            continue
    return added


def remove_event(event_id: str) -> bool:
    """event_id 이벤트 1건 제거 (undo 전용) — store.replace_all 원자 교체. 성공 True.

    기록로그는 store 경유 원칙 준수. 대상 부재/스토어 실패 시 False.
    """
    try:
        import store
        rows = store.all(COLLECTION)
        keep = [r for r in rows if r.get("event_id") != event_id]
        if len(keep) == len(rows):
            return False
        store.replace_all(COLLECTION, keep)
        return True
    except Exception:
        return False


def latest_manual_event(ticker: str) -> dict | None:
    """해당 티커의 최신 manual_holding 이벤트 (undo 대상) — 없으면 None."""
    rows = [r for r in trades_for_ticker(ticker, include_mock=False)
            if str(r.get("source") or "") == "manual_holding"]
    return rows[-1] if rows else None


def rewrite_events(remove_id: str, avg_updates: dict | None = None) -> bool:
    """이벤트 1건 제거 + 후속 이벤트 avg_price 일괄 갱신 (임의 기록 취소 replay 전용).

    store.replace_all 원자 교체 — 취소된 기록 이후 이벤트들의 '기록 시점 평단'을
    재계산 값으로 바꿔 이후 undo 의 평단 정합 검증이 계속 성립하게 한다.
    """
    try:
        import store
        rows = store.all(COLLECTION)
        out, changed = [], False
        for r in rows:
            if r.get("event_id") == remove_id:
                changed = True
                continue
            upd = (avg_updates or {}).get(r.get("event_id"))
            if upd is not None:
                r = dict(r)
                r["avg_price"] = upd
                changed = True
            out.append(r)
        if not changed:
            return False
        store.replace_all(COLLECTION, out)
        return True
    except Exception:
        return False
