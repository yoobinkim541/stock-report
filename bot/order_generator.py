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

import html
import logging
import os, sys, argparse, unicodedata
from datetime import datetime

_logger = logging.getLogger(__name__)

import yfinance as yf
import requests

# bot/ → 프로젝트 루트 (barbell_strategy 등 루트 모듈 import용)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# 소수점 매수 수량 표시·산출 자리수 (키움 소수점 매수 입력 단위와 일치)
QTY_DECIMALS = 4
# 역검증 허용오차: 1주문당 절대 100원 또는 상대 1% 중 큰 값까지 허용
PRECISION_ABS_KRW = 100.0
PRECISION_REL = 0.01

COMPANY_NAMES: dict[str, str] = {
    "ORCL":  "Oracle",
    "NVDA":  "NVIDIA",
    "MSFT":  "Microsoft",
    "GOOGL": "Alphabet",
    "UNH":   "UnitedHealth",
    "SAP":   "SAP SE",
    "SPMO":  "SP500 Mom",
    "SGOV":  "T-Bill",
    "QQQI":  "NBI CC",
    "QQQ":   "Nasdaq 100",
}


def _display_width(s: str) -> int:
    """문자열의 실제 표시 폭 (CJK=2, 이모지=2, ASCII=1)."""
    width = 0
    for ch in s:
        cp = ord(ch)
        if cp < 128:
            width += 1
        elif cp >= 0x1F000:
            width += 2  # 이모지 등 상위 평면 문자
        else:
            ea = unicodedata.east_asian_width(ch)
            width += 2 if ea in ('W', 'F') else 1
    return width


def _dw_pad(s: str, target_width: int) -> str:
    """표시 폭 기준 우측 공백 패딩."""
    pad = max(0, target_width - _display_width(s))
    return s + ' ' * pad


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
    rsi = fetch_rsi("QQQ")
    vix = fetch_vix()
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

    SEP = "─" * 61
    COL1 = 22  # 종목 라벨 표시 폭

    lines = [
        "📋 소수점 매수 주문서",
        f"📅 {now}",
        f"{emoji} {label}  ({dd:+.1f}%)  /  {dca['total_krw']:,}원",
        SEP,
        f"{_dw_pad('종목', COL1)}  {'금액':>9}  {'수량':>9}  {'현재가':>8}",
        SEP,
    ]

    total_usd  = 0.0
    order_rows = []
    precision_warns: list[dict] = []  # 역검증 실패(정밀도 드리프트) 종목 모음
    # fx 가 비정상(0·음수·NaN)이면 KRW→USD 환산 자체가 무의미 → 0 으로 방어
    safe_fx = _safe(fx, default=0.0)
    for ticker, krw_amt in dca["by_ticker"].items():
        price = prices.get(ticker, 0)
        company = COMPANY_NAMES.get(ticker, "")
        label_str = f"{ticker} — {company}" if company else ticker
        if price > 0 and safe_fx > 0:
            usd_amt    = krw_amt / safe_fx
            # 표시·실주문 자리수와 동일하게 반올림한 수량을 권위값으로 사용
            # (이중 나눗셈 후 4자리 절삭으로 발생하는 실투입액 드리프트를 일관화)
            qty        = round(usd_amt / price, QTY_DECIMALS)
            total_usd += usd_amt

            # 역검증: 실제로 qty 주를 price·fx 로 매수하면 들어가는 원화
            actual_krw = qty * price * safe_fx
            tol = max(PRECISION_ABS_KRW, abs(krw_amt) * PRECISION_REL)
            warn = abs(actual_krw - krw_amt) > tol
            if warn:
                drift = actual_krw - krw_amt
                precision_warns.append({
                    "ticker": ticker,
                    "declared_krw": krw_amt,
                    "actual_krw": round(actual_krw, 2),
                    "drift_krw": round(drift, 2),
                })
                _logger.warning(
                    "주문서 정밀도 경고 %s: 선언 %s원 vs 실투입 %.2f원 (드리프트 %+.2f원, 허용 %.2f원)",
                    ticker, f"{krw_amt:,}", actual_krw, drift, tol,
                )

            order_rows.append({
                "ticker": ticker,
                "krw_amt": krw_amt,
                "qty": qty,
                "price": price,
                "precision_warn": warn,
            })
            mark = " ⚠️" if warn else ""
            lines.append(
                f"{_dw_pad(label_str, COL1)}  {krw_amt:>8,}원  {qty:>8.{QTY_DECIMALS}f}주  @${price:>7.2f}{mark}"
            )
        else:
            lines.append(f"{_dw_pad(label_str, COL1)}  {krw_amt:>8,}원  (가격 조회 실패)")

    lines += [
        SEP,
        f"{_dw_pad('합계', COL1)}  {dca['total_krw']:>8,}원  ≈ ${total_usd:.2f}  (@{fx:,.0f}원)",
        "",
        "📱 키움증권  →  해외주식  →  소수점 매수  →  금액 입력",
    ]

    # 정밀도 경고: 소수점 수량 반올림 때문에 실투입 원화가 선언액과 어긋나는 종목 안내
    if precision_warns:
        names = ", ".join(
            f"{w['ticker']}({w['drift_krw']:+,.0f}원)" for w in precision_warns
        )
        lines += [
            "",
            f"⚠️  소수점 반올림으로 실투입액 오차: {names}",
            "    → 금액(원) 기준 입력 시 큰 차이 없음",
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
    escaped = html.escape(text)
    for i in range(0, len(escaped), 4000):
        chunk = f"<pre>{escaped[i:i + 4000]}</pre>"
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            _logger.error(f"주문서 텔레그램 전송 실패: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="소수점 매수 주문서")
    parser.add_argument("--send", action="store_true", help="텔레그램 발송")
    args = parser.parse_args()
    print(generate(send=args.send))
