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
from dashboard import auth, cached, data, theme

st.set_page_config(page_title="퀀트 터미널", page_icon="📊", layout="wide")
theme.inject_global_css()

# 서버 재기동 감지 워치독 — 배포/재시작 후 좀비 탭이 자동 새로고침 → 로그인 게이트
st.components.v1.html(auth.reconnect_watchdog_html(), height=0)

if not auth.password_gate():
    st.stop()

# ── 사이드바: 단일 검색 셀렉트박스 (한글·영문·티커 타입어헤드) ────────────────
_holdings = data.load_holdings()
_held = [h["ticker"] for h in _holdings if h.get("ticker")]
st.session_state.setdefault("ticker", _held[0] if _held else "MSFT")

with st.sidebar:
    st.markdown("### 🔎 종목")
    _cur = st.session_state["ticker"]
    # 옵션 = 보유 우선 + 전체 유니버스. 현재 종목/정규화 가능한 대기입력이 유니버스 밖이면 앞에 보장.
    # accept_new_options 로 새로 입력한 티커(예: RIVN)가 아래 reconciliation 의 `_tsel not in _opts`
    # 절에 걸려 첫 옵션으로 리셋되는 것 방지 — 유효 신규티커만 옵션 편입(garbage 는 미편입→self-heal).
    _pending = st.session_state.get("_tsel")
    _pending_ok = bool(_pending) and ticker_names.normalize_input(_pending) is not None
    _opts = list(dict.fromkeys(_held + ticker_names.universe()))
    for _extra in (_cur, _pending if _pending_ok else None):
        if _extra and _extra not in _opts:
            _opts = [_extra] + _opts
    # 외부(홈 행클릭·초기화·페이지 이동)로 ticker 가 바뀌거나 위젯상태가 유실되면 위젯에 반영.
    # (Streamlit 위젯상태 vs 외부 session_state 동기화 관용 패턴 · switch_page 후 위젯 초기화 방어)
    if st.session_state.get("_tsel_sync") != _cur or st.session_state.get("_tsel") not in _opts:
        st.session_state["_tsel"] = _cur
        st.session_state["_tsel_sync"] = _cur
    _sel = st.selectbox("검색 (한글·영문·티커)", _opts,
                        format_func=ticker_names.search_label, key="_tsel",
                        accept_new_options=True,
                        help="목록에 없어도 티커를 직접 입력하면 조회합니다 (예: BRK-B · COIN · RIVN)")
    if _sel and _sel != _cur:
        _tk = ticker_names.normalize_input(_sel)   # 자유입력 티커/이름 → 정규 티커 (없으면 None)
        if _tk:
            st.session_state["ticker"] = _tk
            st.session_state["_nav_to_ticker"] = True   # 검색·선택 → 종목 분석으로 자동 이동
            # 위젯 표시를 정규 티커로 맞춤: _tk!=_sel(리터럴·대소문자 차) 이면 _tsel_sync 를 비워
            # 다음 rerun 의 reconciliation 이 _tsel←_tk 로 자기보정.
            if _tk != _sel:
                st.session_state.pop("_tsel_sync", None)
            else:
                st.session_state["_tsel_sync"] = _tk
        else:
            st.warning("종목을 찾지 못했습니다 — 티커(예: BRK-B)로 입력해 주세요")
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

    # 🧪 모의투자 레일 (KR·US 자동 페이퍼트레이딩 — EOD 스냅샷 초경량·무네트워크)
    _pg = cached.paper_glance()
    if _pg:
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        theme.render(theme.paper_rail_html(_pg))
        if st.button("🧪 모의투자 상세", width="stretch", key="_paper_btn",
                     help="계좌 현황·NAV 곡선·판단근거 원장"):
            st.session_state["_nav_to_paper"] = True   # 페이지 객체 생성 후 switch (아래)

    # 💰 주식 모으기 레일 (소수점 DCA 통합 관리 — 계획·기록·비중 편집 다이얼로그)
    from dashboard import accumulate as _accum
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    _accum.sidebar_rail()

from dashboard.pages import chart_full, home, market, paper, portfolio, research
from dashboard.pages import ticker as ticker_pg

_home_pg = st.Page(home.render, title="홈", icon="🏠", url_path="home", default=True)
_portfolio_pg = st.Page(portfolio.render, title="포트폴리오", icon="💼", url_path="portfolio")
_ticker_pg = st.Page(ticker_pg.render, title="종목 분석", icon="🔍", url_path="ticker")
_market_pg = st.Page(market.render, title="시장·캘린더", icon="🗓️", url_path="market")
_paper_pg = st.Page(paper.render, title="모의투자", icon="🧪", url_path="paper")
_research_pg = st.Page(research.render, title="리서치", icon="🔬", url_path="research")
_chart_pg = st.Page(chart_full.render, title="차트 풀뷰", icon="🖥️", url_path="chart")

# 홈 보유표 행 클릭 → 종목 분석 자동 이동용 (switch_page 는 StreamlitPage 객체 필요)
st.session_state["_ticker_page"] = _ticker_pg
st.session_state["_chart_page"] = _chart_pg          # ⛶ 전체화면 풀차트 왕복용

nav = st.navigation([_home_pg, _portfolio_pg, _ticker_pg, _chart_pg, _market_pg,
                     _paper_pg, _research_pg])
# 사이드바에서 종목을 새로 고르면 종목 분석 페이지로 이동 (홈 행클릭과 동일 UX)
if st.session_state.pop("_nav_to_ticker", False):
    st.switch_page(_ticker_pg)
# 사이드바 모의 레일 버튼 → 모의투자 페이지 (위 _nav_to_ticker 와 동일 패턴)
if st.session_state.pop("_nav_to_paper", False):
    st.switch_page(_paper_pg)
nav.run()

# 하단 시장 마퀴 띠 — 전 페이지 공통 (VIX·달러·코스피/닥·나스닥·선물 — 5분 캐시·graceful)
try:
    from dashboard import cached as _cached
    from dashboard import theme as _theme
    _tape = _theme.market_tape_html(_cached.market_tape())
    if _tape:
        st.markdown(_tape, unsafe_allow_html=True)
except Exception:
    pass
