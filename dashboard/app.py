"""dashboard/app.py — 퀀트 터미널 엔트리 (멀티페이지 · U1).

실행: bash scripts/run_dashboard.sh (프로젝트 .venv streamlit).
인증 게이트 → 사이드바(보유종목 퀵픽) → st.navigation(홈/포트폴리오/종목/시장/리서치).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

# streamlit run 은 sys.path[0]=dashboard/ 로 잡으므로 프로젝트 루트를 추가해야
# `from dashboard import …`·providers/reports/ml import 가 동작한다(필수).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

import ticker_names  # 종목명 resolver (검색·표시 — 루트 모듈, sys.path 세팅 이후)
from dashboard import auth, data, theme

st.set_page_config(page_title="퀀트 터미널", page_icon="📊", layout="wide")
theme.inject_global_css()

if not auth.password_gate():
    st.stop()

# ── 사이드바: 보유종목 퀵픽 (타이핑 없이 선택) ──────────────────────────────
_holdings = data.load_holdings()
_tickers = [h["ticker"] for h in _holdings if h.get("ticker")] or ["MSFT"]
st.session_state.setdefault("ticker", _tickers[0])

with st.sidebar:
    st.markdown("### 🔎 종목 선택")
    _cur = st.session_state["ticker"]
    _idx = _tickers.index(_cur) if _cur in _tickers else 0
    _pick = st.selectbox("보유 종목", _tickers, index=_idx)
    _custom = st.text_input("또는 검색 (이름·티커)", "", placeholder="예: 마이크론 · micron · MU").strip()
    if _custom:
        # 한글명·영문명·티커 어느 것으로도 resolve. 미해석 시 입력값을 티커로 폴백.
        st.session_state["ticker"] = ticker_names.resolve(_custom) or _custom.upper()
    else:
        st.session_state["ticker"] = _pick
    if st.button("🔄 새로고침", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"분석 대상: **{ticker_names.label(st.session_state['ticker'])}**")
    st.caption(f"⏱ {datetime.now().strftime('%m/%d %H:%M')} 기준 · 캐시 15~60분")

    # 보유 종목 워치리스트 (터미널 레일 — 무네트워크: 스냅샷 수익률)
    _wl = sorted(_holdings, key=lambda h: h.get("value", 0) or 0, reverse=True)
    if _wl:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        theme.render(theme.watchlist_html(
            [{"symbol": h["ticker"], "name": h.get("name"), "last": h.get("value"), "chg_pct": h.get("ret")}
             for h in _wl if h.get("ticker")], title="보유 종목"))

from dashboard.pages import home, market, portfolio, research
from dashboard.pages import ticker as ticker_pg

_home_pg = st.Page(home.render, title="홈", icon="🏠", url_path="home", default=True)
_portfolio_pg = st.Page(portfolio.render, title="포트폴리오", icon="💼", url_path="portfolio")
_ticker_pg = st.Page(ticker_pg.render, title="종목 분석", icon="🔍", url_path="ticker")
_market_pg = st.Page(market.render, title="시장·캘린더", icon="🗓️", url_path="market")
_research_pg = st.Page(research.render, title="리서치", icon="🔬", url_path="research")

# 홈 보유표 행 클릭 → 종목 분석 자동 이동용 (switch_page 는 StreamlitPage 객체 필요)
st.session_state["_ticker_page"] = _ticker_pg

nav = st.navigation([_home_pg, _portfolio_pg, _ticker_pg, _market_pg, _research_pg])
nav.run()
