"""lib/domestic_snapshot.py — 국내(KRW) 보유 → portfolio_snapshot.json 동기화 공용 헬퍼.

lib/overseas_snapshot.py 와 동일한 단일 apply 소스 원칙을 국내에 적용:

    DOMESTIC_SYNC_SOURCE = "" (기본·아무도 안 씀 — diff 보고만) | "toss" | "kiwoom"

kiwoom_sync_rest 의 국내 잔고 조회가 0건일 때(국내주식을 다른 브로커로 보유하는 경우)를
위해 추가됨(2026-07-25) — 토스로 국내주식을 보유하면서 kiwoom_sync_rest 만 국내 권위였던
탓에 최근 거래가 스냅샷에 전혀 반영되지 않던 문제.
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
    return os.getenv("DOMESTIC_SYNC_SOURCE", "").strip().lower()


def can_apply(source: str) -> bool:
    """이 소스가 스냅샷을 갱신해도 되는가 — DOMESTIC_SYNC_SOURCE 와 일치할 때만."""
    return apply_source() == str(source).strip().lower() != ""


def update_domestic_holdings(holdings: list[dict], *, source: str,
                             cash_krw: float | None = None,
                             portfolio_path: str | None = None) -> str:
    """domestic.holdings(+cash_krw) 갱신 — can_apply(source) 확인은 호출부 책임.

    holdings: [{ticker, name, shares, avg_price, current_price, pnl_krw, return_pct}]
    cash_krw: 예수금(매수가능금액). None 이면 기존 값 유지(조회 실패 시 덮어쓰지 않음).
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

        sect = snap.setdefault("domestic", {})
        existing = {str(h.get("ticker", "")).upper(): h
                    for h in sect.get("holdings", []) if h.get("ticker")}
        had_prior = bool(snap.get("last_domestic_sync"))
        fresh = {}
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
                    "price": h.get("avg_price") if side == "buy" else h.get("current_price"),
                    "avg_price": h.get("avg_price"),
                    "account": source, "source": f"{source}_sync",
                    "market": "KR", "currency": "KRW", "confirmed": True,
                    "note": f"{source} 국내 잔고 동기화 수량 변화",
                })
            fresh[tk] = h
        # 전량매도 감지 — overseas_snapshot.update_overseas_holdings 와 동일 규율
        # (최신 조회에 없는 종목을 그냥 남겨두면 전량매도가 영구히 반영 안 됨).
        for tk, old_h in existing.items():
            if tk in fresh:
                continue
            old_shares = float(old_h.get("shares", 0) or 0)
            if had_prior and old_shares > 1e-8:
                trade_recs.append({
                    "ticker": tk, "side": "sell", "qty": old_shares,
                    "price": old_h.get("current_price") or old_h.get("avg_price"),
                    "avg_price": old_h.get("avg_price"),
                    "account": source, "source": f"{source}_sync",
                    "market": "KR", "currency": "KRW", "confirmed": True,
                    "note": f"{source} 국내 잔고 동기화 — 전량매도 감지(스냅샷에서 제거)",
                })
        sect["holdings"] = list(fresh.values())
        if cash_krw is not None:
            sect["cash_krw"] = round(float(cash_krw), 2)
        total_cost = sum(float(h.get("shares", 0) or 0) * float(h.get("avg_price", 0) or 0)
                         for h in fresh.values())
        total_value = sum(float(h.get("shares", 0) or 0) * float(h.get("current_price", 0) or 0)
                          for h in fresh.values())
        sect["summary"] = {
            "total_cost_krw": round(total_cost, 2),
            "total_value_krw": round(total_value, 2),
            "total_pnl_krw": round(total_value - total_cost, 2),
            "total_return_pct": round((total_value / total_cost - 1) * 100, 2) if total_cost > 0 else 0.0,
        }
        snap["last_domestic_sync"] = datetime.now().isoformat()
        snap["last_domestic_sync_source"] = source
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
    if cash_krw is not None:
        lines.append(f"  예수금 ₩{cash_krw:,.0f}")
    return "\n".join(lines) or "(보유 없음)"


def load_current_domestic(portfolio_path: str | None = None) -> list[dict]:
    """현 스냅샷의 domestic.holdings (diff 보고용). 실패 → []."""
    try:
        with open(portfolio_path or PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        return (snap.get("domestic") or {}).get("holdings", []) or []
    except Exception:
        return []
