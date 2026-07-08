"""차트 풀뷰 — 전체화면 풀사이즈 차트 (종목분석과 동일 컨트롤·컴포넌트 공용).

봉/기간/라인·캔들/📐 지표/⇄ 비교 전부 `ticker._price_chart` 그대로 — 높이만
뷰포트급(840px)이고 상단 패딩을 줄여 차트가 화면을 지배한다. ↙ 로 복귀.
"""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from dashboard import cached, data, theme
from dashboard.pages import ticker as ticker_pg


def render():
    # 상단 패딩 슬림 — 차트가 화면 최대 점유
    st.markdown("<style>.stMainBlockContainer{padding-top:1.2rem !important}</style>",
                unsafe_allow_html=True)
    t = st.session_state.get("ticker", "MSFT")
    hist = cached.ohlc(t, period="max")
    if hist is None or getattr(hist, "empty", True):
        st.info("가격 데이터 없음 (yfinance)")
        return
    cl = hist["Close"].dropna()
    yf_price = float(cl.iloc[-1]) if len(cl) else None
    prev = float(cl.iloc[-2]) if len(cl) > 1 else yf_price
    rq = cached.realtime_quote(t)
    price = (rq.get("price") if rq else None) or yf_price
    chg = (price - prev) if (price is not None and prev) else None
    col = theme.GREEN if (chg or 0) >= 0 else theme.RED
    st.markdown(
        f'<div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap">'
        f'<b style="font-size:1.25rem">⛶ {ticker_names.label(t)}</b>'
        f'<span style="font-family:JetBrains Mono,monospace;font-size:1.05rem">'
        f'{price:,.2f}</span>'
        f'<span style="color:{col};font-size:0.9rem">'
        f'{(chg or 0):+,.2f} ({((chg or 0) / prev * 100 if prev else 0):+.2f}%)</span>'
        f'<span style="color:{theme.MUTED};font-size:0.75rem">'
        f'{"⚡ 실시간 KIS" if rq and rq.get("price") else "yfinance 종가"}</span></div>',
        unsafe_allow_html=True)
    pos = data.holding_position(t)
    ticker_pg._price_chart(t, hist, pos.get("avg_price_usd") if pos else None,
                           data.trade_events(t), fullscreen=True)
