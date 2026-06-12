#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guest_portfolio.py — 게스트 본인 포트폴리오 (user_id 스코프 store)

게스트가 자기 보유 종목을 직접 입력하고, 자기 데이터의 평가(평가액·손익·수익률)를
조회한다. 소유자 portfolio_snapshot.json 과 완전히 분리 — store 문서 "guest_holdings"
를 각 게스트의 chat_id(user_id) 네임스페이스에 저장.

법적 안전: 그들 자신의 데이터·평가만 제공 (포트폴리오 트래커 수준). 매매신호·목표가·
리밸런싱·DCA 등 처방형 출력은 일절 없음.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import store
from bot.guest_report import DISCLAIMER

_DOC_KEY = "guest_holdings"


# ══════════════════════════════════════════════════════════════════════
#  CRUD (user = 게스트 chat_id)
# ══════════════════════════════════════════════════════════════════════

def list_holdings(user: str) -> dict:
    """{ticker: {shares, avg_price}} (게스트 본인 데이터)."""
    data = store.get_doc(_DOC_KEY, {}, user=user)
    return data if isinstance(data, dict) else {}


def add_holding(user: str, ticker: str, shares: float, price: float) -> dict:
    """보유 종목 추가/누적 (기존 포지션이면 가중평단 재계산)."""
    data   = list_holdings(user)
    ticker = ticker.upper()
    shares = float(shares)
    price  = float(price)
    cur = data.get(ticker)
    if cur:
        old_sh  = float(cur.get("shares", 0))
        old_avg = float(cur.get("avg_price", price))
        new_sh  = old_sh + shares
        new_avg = (old_sh * old_avg + shares * price) / new_sh if new_sh > 0 else price
        data[ticker] = {"shares": round(new_sh, 4), "avg_price": round(new_avg, 4)}
    else:
        data[ticker] = {"shares": round(shares, 4), "avg_price": round(price, 4)}
    store.put_doc(_DOC_KEY, data, user=user)
    return data[ticker]


def remove_holding(user: str, ticker: str) -> bool:
    """보유 종목 삭제. 성공 시 True."""
    data   = list_holdings(user)
    ticker = ticker.upper()
    if ticker in data:
        del data[ticker]
        store.put_doc(_DOC_KEY, data, user=user)
        return True
    return False


# ══════════════════════════════════════════════════════════════════════
#  가격 조회 + sanitized 평가 리포트
# ══════════════════════════════════════════════════════════════════════

def _fetch_prices(tickers: list[str]) -> dict:
    """yfinance 일괄 현재가 (실패 시 개별 fallback)."""
    import yfinance as yf

    prices: dict[str, float] = {}
    if not tickers:
        return prices
    try:
        data = yf.download(tickers, period="2d", auto_adjust=True, progress=False)
        if not data.empty and "Close" in data.columns:
            close = data["Close"]
            if hasattr(close, "columns"):
                for t in tickers:
                    if t in close.columns:
                        s = close[t].dropna()
                        if not s.empty:
                            prices[t] = round(float(s.iloc[-1]), 2)
            else:
                s = close.dropna()
                if not s.empty:
                    prices[tickers[0]] = round(float(s.iloc[-1]), 2)
    except Exception:
        pass
    for t in tickers:
        if t not in prices:
            try:
                h = yf.Ticker(t).history(period="2d")
                if not h.empty:
                    prices[t] = round(float(h["Close"].iloc[-1]), 2)
            except Exception:
                pass
    return prices


def build_portfolio_report(user: str) -> str:
    """게스트 본인 포트폴리오 평가 (평가액·손익·수익률) — 처방 없음."""
    data = list_holdings(user)
    if not data:
        return ("📭 등록된 보유 종목이 없습니다.\n"
                "/myadd TICKER 주수 평단가  로 추가하세요.\n"
                "예) /myadd QQQ 10 500" + DISCLAIMER)

    prices = _fetch_prices(list(data.keys()))

    lines = ["📒 내 포트폴리오 (읽기전용)", "━━━━━━━━━━━━━━━━━━━"]
    total_val = total_cost = 0.0
    for t, h in sorted(data.items()):
        sh  = float(h.get("shares", 0))
        avg = float(h.get("avg_price", 0))
        cur = prices.get(t)
        cost = sh * avg
        total_cost += cost
        if cur is None:
            lines.append(f"  {t:<6} {sh:g}주 @${avg:.2f}  (현재가 조회 실패)")
            total_val += cost
            continue
        val = sh * cur
        total_val += val
        ret = (cur - avg) / avg * 100 if avg > 0 else 0.0
        sign = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
        lines.append(f"  {t:<6} {sh:g}주 @${avg:.2f} → ${cur:.2f}  {sign}{abs(ret):.1f}%")

    total_pnl = total_val - total_cost
    total_ret = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    psign = "▲" if total_pnl > 0 else ("▼" if total_pnl < 0 else "─")
    lines += [
        "━━━━━━━━━━━━━━━━━━━",
        f"  평가액   ${total_val:,.2f}",
        f"  매입가   ${total_cost:,.2f}",
        f"  평가손익 {psign}${abs(total_pnl):,.2f}  ({total_ret:+.1f}%)",
    ]
    return "\n".join(lines) + DISCLAIMER


def guest_portfolio_help() -> str:
    return (
        "/myadd TICKER 주수 평단가   보유 종목 추가 (예: /myadd QQQ 10 500)\n"
        "/myremove TICKER            보유 종목 삭제\n"
        "/myportfolio                내 포트폴리오 평가 (평가액·손익)"
    )
