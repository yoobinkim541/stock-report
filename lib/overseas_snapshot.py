"""lib/overseas_snapshot.py — 해외(USD) 보유 → portfolio_snapshot.json 동기화 공용 헬퍼.

토스(crons/toss_sync)·키움 해외(crons/kiwoom_sync_rest) 두 read-only 잔고 소스가
같은 overseas_general 섹션을 갱신할 수 있으므로, **단일 apply 소스 원칙**을 env 로 강제:

    OVERSEAS_SYNC_SOURCE = "" (기본·아무도 안 씀 — diff 보고만) | "toss" | "kiwoom"

지정된 소스만 스냅샷을 실제 갱신하고, 나머지는 항상 보고 전용 — 이중 writer 가 서로의
동기화를 되돌리는 lost-update 를 구조적으로 차단한다. 쓰기는 kiwoom_sync_rest(국내)와
동일 규율: file_write_lock + .bak 백업 + atomic write + store 그림자 + trade_events 원장.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime

logger = logging.getLogger(__name__)

PROJECT_DIR = os.getenv("STOCK_REPORT_PROJECT_DIR",
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTFOLIO_PATH = os.path.join(PROJECT_DIR, "portfolio_snapshot.json")


def apply_source() -> str:
    return os.getenv("OVERSEAS_SYNC_SOURCE", "").strip().lower()


def can_apply(source: str) -> bool:
    """이 소스가 스냅샷을 갱신해도 되는가 — OVERSEAS_SYNC_SOURCE 와 일치할 때만."""
    return apply_source() == str(source).strip().lower() != ""


def diff_holdings(current: list[dict], fetched: list[dict]) -> list[str]:
    """현 스냅샷 overseas_general vs 브로커 잔고 차이 요약 (순수 — 보고용).

    current: [{ticker, shares, ...}] / fetched: [{ticker, shares, name, ...}].
    """
    cur = {str(h.get("ticker", "")).upper(): float(h.get("shares", 0) or 0)
           for h in current or [] if h.get("ticker")}
    new = {str(h.get("ticker", "")).upper(): float(h.get("shares", 0) or 0)
           for h in fetched or [] if h.get("ticker")}
    lines = []
    for tk in sorted(set(cur) | set(new)):
        a, b = cur.get(tk), new.get(tk)
        if a is None:
            lines.append(f"➕ {tk}: 스냅샷에 없음 → 브로커 {b:g}주")
        elif b is None:
            lines.append(f"➖ {tk}: 스냅샷 {a:g}주 → 브로커에 없음")
        elif abs(a - b) > 1e-6:
            lines.append(f"↔️ {tk}: {a:g} → {b:g}주 ({b - a:+g})")
    return lines


def update_overseas_holdings(holdings: list[dict], *, source: str,
                             portfolio_path: str | None = None) -> str:
    """overseas_general.holdings_usd 갱신 — can_apply(source) 확인은 호출부 책임.

    holdings: [{ticker, name, shares, avg_price_usd, current_price_usd,
                cost_usd, value_usd, pnl_usd, return_pct}]  (USD 종목만 넣을 것)
    반환: 사람용 요약 문자열.
    """
    path = portfolio_path or PORTFOLIO_PATH
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    import safe_io
    from lib import trade_events

    trade_recs = []
    with safe_io.file_write_lock(path):
        shutil.copy2(path, path + ".bak")
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)

        sect = snap.setdefault("overseas_general", {})
        existing = {str(h.get("ticker", "")).upper(): h
                    for h in sect.get("holdings_usd", []) if h.get("ticker")}
        had_prior = bool(snap.get("last_overseas_sync"))
        for h in holdings:
            tk = str(h.get("ticker", "")).upper()
            if not tk:
                continue
            old_shares = float((existing.get(tk) or {}).get("shares", 0) or 0)
            delta = round(float(h.get("shares", 0) or 0) - old_shares, 6)
            if had_prior and abs(delta) > 1e-8:
                side = "buy" if delta > 0 else "sell"
                trade_recs.append({
                    "ticker": tk, "side": side, "qty": abs(delta),
                    "price": h.get("avg_price_usd") if side == "buy" else h.get("current_price_usd"),
                    "avg_price": h.get("avg_price_usd"),
                    "account": source, "source": f"{source}_sync",
                    "market": "US", "currency": "USD", "confirmed": True,
                    "note": f"{source} 해외 잔고 동기화 수량 변화",
                })
            existing[tk] = h
        sect["holdings_usd"] = list(existing.values())
        snap["last_overseas_sync"] = datetime.now().isoformat()
        snap["last_overseas_sync_source"] = source
        safe_io.atomic_write_json(path, snap)

    try:
        import store
        store.shadow_doc("portfolio_snapshot", snap)
    except Exception as e:
        logger.warning("store 그림자 동기화 실패(무시): %s", e)
    for rec in trade_recs:
        try:
            trade_events.record_trade(**rec)
        except Exception as e:
            logger.warning("trade_events 기록 실패(무시): %s", e)

    lines = [f"  {h['ticker']} {h.get('name', '')} {h.get('shares', 0):g}주 "
             f"{h.get('return_pct', 0):+.1f}%" for h in holdings]
    return "\n".join(lines) or "(보유 없음)"


def load_current_overseas(portfolio_path: str | None = None) -> list[dict]:
    """현 스냅샷의 overseas_general.holdings_usd (diff 보고용). 실패 → []."""
    try:
        with open(portfolio_path or PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        return (snap.get("overseas_general") or {}).get("holdings_usd", []) or []
    except Exception:
        return []
