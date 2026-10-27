"""국내 ETF 전용 분석 화면 — 원화 기준 프로필·수익률·보유종목·괴리율·추적오차."""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from dashboard import cached
from dashboard.pages import ticker as ticker_page

try:
    from kr_etf_seed import KR_ETF as _KR_ETF
except Exception:
    _KR_ETF = {}

_NOBAR = {"displayModeBar": False}
_KR_TICKERS = sorted(_KR_ETF)


def _default_ticker() -> str:
    cur = ticker_names.normalize_input(str(st.session_state.get("ticker") or ""))
    if cur in _KR_TICKERS:
        return cur
    for cand in ("069500.KS", "0167A0.KS"):
        if cand in _KR_TICKERS:
            return cand
    return _KR_TICKERS[0] if _KR_TICKERS else "069500.KS"


def render():
    st.title("🇰🇷 국내 ETF")
    st.subheader("국내 ETF 전용")
    st.caption("국내 상장 ETF만 모아서 원화 기준으로 봅니다. 표시 전용 · 주문 0")

    if not _KR_TICKERS:
        st.info("국내 ETF 목록을 불러오지 못했습니다.")
        return

    default = _default_ticker()
    try:
        idx = _KR_TICKERS.index(default)
    except ValueError:
        idx = 0

    ticker = st.selectbox(
        "국내 ETF 선택",
        _KR_TICKERS,
        index=idx,
        format_func=ticker_names.search_label,
        key="_kr_etf_ticker",
        help="국내 ETF 전용 보기입니다. 예: 069500.KS · 0167A0.KS",
    )

    st.session_state["ticker"] = ticker
    st.markdown(f"**{ticker_names.label(ticker, allow_net=False)}**")

    etf = cached.etf(ticker) or {}
    if not etf.get("is_etf"):
        # 데이터 공백(미지원 종목)과 일시 장애(공급자 타임아웃)를 구분할 수 없으므로
        # 캐시를 비우고 재조회할 수 있는 경로를 준다 — 종전엔 TTL 만료까지 막혀 있었다.
        st.info("국내 ETF 데이터를 아직 준비하지 못했습니다 — 데이터 공급자 지연이면 잠시 후 다시 시도하세요.")
        if st.button("🔄 다시 불러오기", key="_kr_etf_retry"):
            try:
                cached.etf.clear()
            except Exception:
                pass
            st.rerun()
        return
    if (etf.get("market_type") or "").lower() != "kr":
        st.info("해당 종목은 국내 ETF가 아닙니다 — 해외 ETF·개별주는 ‘종목 분석’ 페이지에서 보세요.")
        return

    st.caption("가격·괴리·보수·구성은 아래 한 화면에서 봅니다.")
    ticker_page._etf_sections(ticker, etf, etf.get("price"))
