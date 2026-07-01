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

# ── 사이드바: 단일 검색 셀렉트박스 (한글·영문·티커 타입어헤드) ────────────────
_holdings = data.load_holdings()
_held = [h["ticker"] for h in _holdings if h.get("ticker")]
st.session_state.setdefault("ticker", _held[0] if _held else "MSFT")

with st.sidebar:
    st.markdown("### 🔎 종목")
    _cur = st.session_state["ticker"]
    # 옵션 = 보유 우선 + 전체 유니버스. 현재 종목이 유니버스 밖이면 앞에 보장.
    _opts = list(dict.fromkeys(_held + ticker_names.universe()))
    if _cur not in _opts:
        _opts = [_cur] + _opts
    # 외부(홈 행클릭·초기화·페이지 이동)로 ticker 가 바뀌거나 위젯상태가 유실되면 위젯에 반영.
    # (Streamlit 위젯상태 vs 외부 session_state 동기화 관용 패턴 · switch_page 후 위젯 초기화 방어)
    if st.session_state.get("_tsel_sync") != _cur or st.session_state.get("_tsel") not in _opts:
        st.session_state["_tsel"] = _cur
        st.session_state["_tsel_sync"] = _cur
    _sel = st.selectbox("검색 (한글·영문·티커)", _opts,
                        format_func=ticker_names.search_label, key="_tsel")
    if _sel != _cur:
        st.session_state["ticker"] = _sel
        st.session_state["_tsel_sync"] = _sel
        st.session_state["_nav_to_ticker"] = True   # 검색·선택 → 종목 분석으로 자동 이동
    if st.button("🔄 새로고침", width="stretch", help="캐시 비우고 다시 불러오기"):
        st.cache_data.clear()
        # 무거운 게이트(스크리너·백테스트)도 초기화 → 캐시 비운 뒤 자동 재계산 방지
        for _k in ("scr_done", "bt_done"):
            st.session_state.pop(_k, None)
        st.rerun()
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
# 사이드바에서 종목을 새로 고르면 종목 분석 페이지로 이동 (홈 행클릭과 동일 UX)
if st.session_state.pop("_nav_to_ticker", False):
    st.switch_page(_ticker_pg)
nav.run()
