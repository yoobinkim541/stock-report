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
    fetch_exchange_rate_close,
    classify_market, calculate_dca,
    BULL_PHASES, BEAR_PHASES,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
)

import notify
import fmt

MARKET_OPEN_KST = 22   # KST 22:00 = 미국 장 시작

# 소수점 매수 수량 표시·산출 자리수 (키움 소수점 매수 입력 단위와 일치)
QTY_DECIMALS = 4
# 역검증 허용오차: 1주문당 절대 100원 또는 상대 1% 중 큰 값까지 허용
PRECISION_ABS_KRW = 100.0
PRECISION_REL = 0.01

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


def round_alloc_1000(by_ticker: dict) -> dict:
    """배분을 **천원 단위**로 재배분 (순수 — 키움 주식모으기 최소/단위 금액 1,000원).

    최대잔여법: 각 종목 floor(금액/1000) 배정 후 남은 천원 묶음을 잔여(소수부)가
    큰 순서로 +1,000. 합계 = round(원합계/1000)×1000 정확 보존 · 0원 배분은 제외.
    """
    total = sum(by_ticker.values())
    if total <= 0:
        return dict(by_ticker)
    units = int(round(total / 1000.0))                 # 배정할 천원 묶음 수
    quo = {t: v / 1000.0 for t, v in by_ticker.items()}
    base = {t: int(q) for t, q in quo.items()}
    left = units - sum(base.values())
    order = sorted(quo, key=lambda t: -(quo[t] - base[t]))
    for t in order[:max(0, left)]:
        base[t] += 1
    return {t: n * 1000 for t, n in base.items() if n > 0}


def build() -> dict:
    """주식 모으기(소수점 DCA) 계획 — 구조화 산출 (봇 주문서·대시보드 사이드바 공용).

    반환: {now, market_type, phase_key, emoji, label, dd, fx, total_krw, mult,
           rows: [{ticker, krw_amt, qty|None, price, precision_warn}], total_usd,
           precision_warns}. qty=None = 가격 조회 실패 행.
    """
    qqq = fetch_qqq_data()
    rsi = fetch_rsi("QQQ")
    vix = fetch_vix()
    fx  = fetch_exchange_rate_close()   # 확정 종가 기준 — 하루 동안 고정(장중 변동 배제)

    market_type, phase_key = classify_market(qqq, rsi, vix)
    # 낙폭 정지(BARBELL_LEV_HALT_DD) 가드가 발동하도록 drawdown_pct 전달 —
    # 없으면 leverage_dca_guard 가 낙폭 정지를 건너뛰어 극단 낙폭서도 5× 권고가 나감(감사 확정).
    dca = calculate_dca(market_type, phase_key, fx, drawdown_pct=qqq.get("drawdown_pct"))

    # 키움 주식모으기 입력 단위(최소 1,000원·천원 단위)에 맞춰 재배분 — 합계 보존
    dca["by_ticker"] = round_alloc_1000(dca["by_ticker"])
    dca["total_krw"] = sum(dca["by_ticker"].values())
    tickers   = list(dca["by_ticker"].keys())
    prices    = fetch_prices(tickers)
    phase_inf = BULL_PHASES[phase_key] if market_type == "bull" else BEAR_PHASES.get(phase_key, BEAR_PHASES[0])

    total_usd  = 0.0
    rows: list[dict] = []
    precision_warns: list[dict] = []  # 역검증 실패(정밀도 드리프트) 종목 모음
    # fx 가 비정상(0·음수·NaN)이면 KRW→USD 환산 자체가 무의미 → 0 으로 방어
    safe_fx = _safe(fx, default=0.0)
    for ticker, krw_amt in dca["by_ticker"].items():
        price = prices.get(ticker, 0)
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
            rows.append({"ticker": ticker, "krw_amt": krw_amt, "qty": qty,
                         "price": price, "precision_warn": warn})
        else:
            rows.append({"ticker": ticker, "krw_amt": krw_amt, "qty": None,
                         "price": price, "precision_warn": False})

    return {
        "now": datetime.now().strftime("%Y-%m-%d %H:%M KST"),
        "market_type": market_type, "phase_key": phase_key,
        "emoji": phase_inf.get("emoji", ""), "label": phase_inf.get("label", ""),
        "dd": qqq.get("drawdown_pct", 0), "fx": fx,
        "total_krw": dca["total_krw"], "mult": dca.get("multiplier"),
        "rows": rows, "total_usd": total_usd, "precision_warns": precision_warns,
    }


def generate(send: bool = False) -> str:
    """주문서 생성 + 선택적 텔레그램 발송 (build() 포맷팅 레이어 — 출력 불변)."""
    plan = build()
    market_type, phase_key = plan["market_type"], plan["phase_key"]
    fx, total_usd = plan["fx"], plan["total_usd"]
    precision_warns = plan["precision_warns"]

    SEP = "─" * 61
    COL1 = 22  # 종목 라벨 표시 폭

    lines = [
        "📋 소수점 매수 주문서",
        f"📅 {plan['now']}",
        f"{plan['emoji']} {plan['label']}  ({plan['dd']:+.1f}%)  /  {plan['total_krw']:,}원",
        SEP,
        f"{_dw_pad('종목', COL1)}  {'금액':>9}  {'수량':>9}  {'현재가':>8}",
        SEP,
    ]

    for r in plan["rows"]:
        label_str = fmt.name(r["ticker"])
        if r["qty"] is not None:
            mark = " ⚠️" if r["precision_warn"] else ""
            lines.append(
                f"{_dw_pad(label_str, COL1)}  {r['krw_amt']:>8,}원  {r['qty']:>8.{QTY_DECIMALS}f}주  @${r['price']:>7.2f}{mark}"
            )
        else:
            lines.append(f"{_dw_pad(label_str, COL1)}  {r['krw_amt']:>8,}원  (가격 조회 실패)")

    lines += [
        SEP,
        f"{_dw_pad('합계', COL1)}  {plan['total_krw']:>8,}원  ≈ ${total_usd:.2f}  (@{fx:,.0f}원)",
        "",
        "📱 키움증권  →  해외주식  →  소수점 매수  →  금액 입력",
    ]

    # 정밀도 경고: 소수점 수량 반올림 때문에 실투입 원화가 선언액과 어긋나는 종목 안내
    if precision_warns:
        names = ", ".join(
            f"{fmt.name(w['ticker'], maxlen=12)}({w['drift_krw']:+,.0f}원)" for w in precision_warns
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
    escaped = html.escape(text)
    for i in range(0, len(escaped), 4000):
        chunk = f"<pre>{escaped[i:i + 4000]}</pre>"
        notify.send_telegram(
            chunk, token=TELEGRAM_TOKEN, chat_id=TELEGRAM_CHAT_ID,
            parse_mode="HTML", split=False,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="소수점 매수 주문서")
    parser.add_argument("--send", action="store_true", help="텔레그램 발송")
    args = parser.parse_args()
    print(generate(send=args.send))
