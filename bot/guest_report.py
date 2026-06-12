#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
guest_report.py — 읽기전용(게스트) 계정용 sanitized 리포트

법적 안전 경계: 게스트에게는 **사실형 시장 데이터·기술적 지표**만 제공한다.
처방형 출력(매수/매도 신호, 목표가/손절가, DCA 배분, 레버리지 전환, Phase 행동지침,
AI 투자상담)은 절대 포함하지 않는다 — 이는 소유자 전용.

→ "서술(descriptive)은 OK, 지시(prescriptive)는 금지" 원칙.
"""

from __future__ import annotations

DISCLAIMER = (
    "\n────────────\n"
    "ℹ️ 참고용 시장 데이터·기술적 지표입니다. 매매 권유가 아니며 "
    "투자 판단과 책임은 본인에게 있습니다."
)

_REGIME_LABEL = {"bull": "강세", "bear": "약세", "neutral": "중립"}


def _fg_proxy() -> float | None:
    try:
        from ml.data_pipeline import get_fg_proxy_score
        v = get_fg_proxy_score()
        return v if v is not None and v >= 0 else None
    except Exception:
        return None


def build_market_brief(d: dict) -> str:
    """fetch_market() 결과 dict → 사실형 시황 브리핑 (처방 없음).

    Phase 번호·행동지침(DCA 배율·레버리지)은 의도적으로 제외하고
    국면(강세/약세/중립)은 서술적 라벨로만 표기한다.
    """
    qqq = d.get("qqq", {}) or {}
    bm  = d.get("benchmarks", {}) or {}
    rsi = d.get("rsi")
    vix = d.get("vix")
    dd  = qqq.get("drawdown_pct")
    regime = _REGIME_LABEL.get(d.get("market_type"), "-")

    lines = [
        "📊 시황 브리핑 (읽기전용)",
        "━━━━━━━━━━━━━━━━━━━",
        f"  시장 국면   {regime}",
        f"  QQQ 현재가  ${qqq.get('current', '-')}",
        f"  고점 대비   {dd:+.1f}%" if isinstance(dd, (int, float)) else "  고점 대비   -",
        f"  RSI(QQQ)    {rsi if rsi is not None else '-'}",
        f"  VIX         {vix if vix is not None else '-'}",
    ]

    fg = _fg_proxy()
    if fg is not None:
        label = ("극도공포" if fg <= 20 else "공포" if fg <= 45 else
                 "중립" if fg <= 55 else "탐욕" if fg <= 75 else "극도탐욕")
        lines.append(f"  F&G Proxy   {fg:.0f}/100 ({label})")

    q = bm.get("QQQ") or {}
    s = bm.get("SPY") or {}
    if q or s:
        lines += [
            "",
            "  [벤치마크 YTD]",
            f"   QQQ  {q.get('ytd_pct', '-')}%",
            f"   SPY  {s.get('ytd_pct', '-')}%",
        ]

    return "\n".join(lines) + DISCLAIMER


def build_indicators(ticker: str) -> str:
    """종목 기술적 지표 (RSI·이동평균·모멘텀·52주 위치) — 서술형, 매매신호 없음."""
    ticker = (ticker or "").upper().strip()
    if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
        return "사용법: /indicators TICKER\n예: /indicators QQQ"

    import numpy as np
    import yfinance as yf

    try:
        hist = yf.Ticker(ticker).history(period="1y")
    except Exception:
        return f"❌ {ticker} 데이터 조회 실패"
    if hist is None or hist.empty or len(hist) < 30:
        return f"❌ {ticker} 데이터 부족 (티커 확인)"

    close = hist["Close"].dropna()
    price = float(close.iloc[-1])

    def _sma(n: int):
        return float(close.rolling(n).mean().iloc[-1]) if len(close) >= n else None

    s20, s50, s200 = _sma(20), _sma(50), _sma(200)

    # RSI(14)
    delta = close.diff()
    up   = delta.clip(lower=0).rolling(14).mean()
    down = (-delta.clip(upper=0)).rolling(14).mean()
    rs   = up / down.replace(0, np.nan)
    rsi_series = 100 - 100 / (1 + rs)
    rsi = float(rsi_series.iloc[-1]) if not rsi_series.dropna().empty else None

    def _mom(n: int):
        return (price / float(close.iloc[-n]) - 1) * 100 if len(close) > n else None

    m1, m3 = _mom(21), _mom(63)
    hi, lo = float(close.max()), float(close.min())
    pos = (price - lo) / (hi - lo) * 100 if hi > lo else 0.0

    def _trend(s):
        if s is None:
            return "-"
        return "위 ▲" if price >= s else "아래 ▼"

    rsi_tag = ""
    if rsi is not None:
        rsi_tag = "  (과매수)" if rsi >= 70 else "  (과매도)" if rsi <= 30 else ""

    lines = [
        f"📈 {ticker} 기술적 지표 (읽기전용)",
        "━━━━━━━━━━━━━━━━━━━",
        f"  현재가      ${price:,.2f}",
        f"  RSI(14)     {rsi:.0f}{rsi_tag}" if rsi is not None else "",
        f"  SMA20       ${s20:,.2f}  ({_trend(s20)})" if s20 else "",
        f"  SMA50       ${s50:,.2f}  ({_trend(s50)})" if s50 else "",
        f"  SMA200      ${s200:,.2f}  ({_trend(s200)})" if s200 else "",
        f"  1M 모멘텀   {m1:+.1f}%" if m1 is not None else "",
        f"  3M 모멘텀   {m3:+.1f}%" if m3 is not None else "",
        f"  52주 위치   {pos:.0f}%  (저점0 ~ 고점100)",
    ]
    return "\n".join(l for l in lines if l) + DISCLAIMER


def guest_help() -> str:
    lines = [
        "🤖 읽기전용 계정 — 사용 가능 명령어",
        "━━━━━━━━━━━━━━━━━━━",
        "[시황·지표]",
        "/market             시황 브리핑 (국면·낙폭·RSI·VIX·F&G)",
        "/indicators TICKER  종목 기술적 지표 (RSI·이동평균·모멘텀)",
        "",
        "[내 포트폴리오]",
    ]
    try:
        from bot.guest_portfolio import guest_portfolio_help
        lines.append(guest_portfolio_help())
    except Exception:
        pass
    lines.append("")
    lines.append("/help               이 도움말")
    return "\n".join(lines) + DISCLAIMER
