#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
order_generator.py — 매일 아침 소수점 매수 주문서

키움증권 앱 > 해외주식 > 소수점 매수 화면에서 바로 따라할 수 있는 형식.
종목명 / 투입 원화 / 매수 수량(소수점) / 현재가를 한 화면에 표시.

Usage:
  python3 order_generator.py          # 콘솔 출력
  python3 order_generator.py --send   # 텔레그램 발송
"""

import os, sys, argparse
from datetime import datetime

import yfinance as yf
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

from barbell_strategy import (
    fetch_qqq_data, fetch_rsi, fetch_vix, fetch_exchange_rate,
    classify_market, calculate_dca,
    BULL_PHASES, BEAR_PHASES,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
)

MARKET_OPEN_KST = 22   # KST 22:00 = 미국 장 시작

COMPANY_NAMES: dict[str, str] = {
    "NOW":   "ServiceNow",
    "ORCL":  "Oracle",
    "NVDA":  "NVIDIA",
    "MSFT":  "Microsoft",
    "GOOGL": "Alphabet",
    "UNH":   "UnitedHealth",
    "CRM":   "Salesforce",
    "SAP":   "SAP SE",
    "SPMO":  "SP500 Mom",
    "SGOV":  "T-Bill",
    "QQQI":  "NBI CC",
    "CPNG":  "Coupang",
    "QQQ":   "Nasdaq 100",
}


def _safe(val, default=0.0) -> float:
    try:
        v = float(val)
        import numpy as np
        return v if np.isfinite(v) and v > 0 else default
    except Exception:
        return default


def fetch_prices(tickers: list) -> dict:
    """DCA 종목 현재가 일괄 조회."""
    prices = {}
    try:
        data = yf.download(tickers, period="2d", auto_adjust=True, progress=False)
        if not data.empty and "Close" in data.columns:
            close = data["Close"]
            if hasattr(close, "columns"):
                for t in tickers:
                    if t in close.columns:
                        s = close[t].dropna()
                        if not s.empty:
                            prices[t] = round(_safe(s.iloc[-1]), 2)
            else:
                s = data["Close"].dropna()
                if not s.empty and tickers:
                    prices[tickers[0]] = round(_safe(s.iloc[-1]), 2)
    except Exception:
        pass

    # fallback: 개별 조회
    for t in tickers:
        if t in prices:
            continue
        try:
            h = yf.Ticker(t).history(period="2d")
            if not h.empty:
                prices[t] = round(_safe(h["Close"].iloc[-1]), 2)
        except Exception:
            pass

    return prices


def generate(send: bool = False) -> str:
    """주문서 생성 + 선택적 텔레그램 발송."""
    qqq = fetch_qqq_data()
    rsi = fetch_rsi("QQQ") if hasattr(fetch_rsi, "__call__") else 50.0
    vix = fetch_vix()       if hasattr(fetch_vix, "__call__") else 20.0
    fx  = fetch_exchange_rate()

    market_type, phase_key = classify_market(qqq, rsi, vix)
    dca = calculate_dca(market_type, phase_key, fx)

    tickers   = list(dca["by_ticker"].keys())
    prices    = fetch_prices(tickers)
    phase_inf = BULL_PHASES[phase_key] if market_type == "bull" else BEAR_PHASES.get(phase_key, BEAR_PHASES[0])

    now    = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    dd     = qqq.get("drawdown_pct", 0)
    emoji  = phase_inf.get("emoji", "")
    label  = phase_inf.get("label", "")

    SEP = "─" * 54
    lines = [
        "📋 소수점 매수 주문서",
        f"📅 {now}",
        f"{emoji} {label}  ({dd:+.1f}%)  /  {dca['total_krw']:,}원",
        SEP,
        f"{'종목':<22}  {'금액':>9}  {'수량':>9}  {'현재가':>8}",
        SEP,
    ]

    total_usd  = 0.0
    order_rows = []
    for ticker, krw_amt in dca["by_ticker"].items():
        price = prices.get(ticker, 0)
        company = COMPANY_NAMES.get(ticker, "")
        label_str = f"{ticker} — {company}" if company else ticker
        if price > 0:
            usd_amt    = krw_amt / fx
            qty        = usd_amt / price
            total_usd += usd_amt
            order_rows.append((ticker, krw_amt, qty, price))
            lines.append(
                f"{label_str:<22}  {krw_amt:>8,}원  {qty:>8.4f}주  @${price:>7.2f}"
            )
        else:
            lines.append(f"{label_str:<22}  {krw_amt:>8,}원  (가격 조회 실패)")

    lines += [
        SEP,
        f"합계  {dca['total_krw']:>8,}원  ≈ ${total_usd:.2f}  (@{fx:,.0f}원)",
        "",
        "📱 키움증권  →  해외주식  →  소수점 매수  →  금액 입력",
    ]

    # Phase별 부가 안내
    if market_type == "bear" and isinstance(phase_key, int) and phase_key >= 2:
        sgov_note = BEAR_PHASES[phase_key].get("action_items", [""])[0]
        lines += ["", f"⚠️  DCA 외 추가 행동: {sgov_note}"]
    elif market_type == "bull":
        lines += ["", f"💡 강세장: QQQI 배당 → SGOV 비축 우선"]

    report = "\n".join(lines)
    if send:
        _send(report)
    return report


def _send(text: str):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[i:i + 4000]}, timeout=10)
        except Exception:
            pass


# fetch_rsi / fetch_vix re-import (order_generator 단독 실행 대비)
try:
    from barbell_strategy import fetch_rsi, fetch_vix
except ImportError:
    def fetch_rsi(*a, **k): return 50.0
    def fetch_vix(*a, **k): return 20.0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="소수점 매수 주문서")
    parser.add_argument("--send", action="store_true", help="텔레그램 발송")
    args = parser.parse_args()
    print(generate(send=args.send))
