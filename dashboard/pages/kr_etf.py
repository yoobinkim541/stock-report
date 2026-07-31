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
        st.info("국내 ETF 데이터를 아직 준비하지 못했습니다.")
        return
    if (etf.get("market_type") or "").lower() != "kr":
        st.info("해당 종목은 국내 ETF가 아닙니다.")
        return

    cols = st.columns(5)
    cols[0].metric("현재가", ticker_page._etf_money(etf, etf.get("price"), 0))
    cols[1].metric("NAV", ticker_page._etf_money(etf, etf.get("nav"), 0))
    pm = etf.get("premium_pct")
    cols[2].metric("괴리율", f"{pm:+.2f}%" if pm is not None else "—")
    te = etf.get("tracking_error_pct")
    cols[3].metric("추적오차", f"{te:.2f}%" if te is not None else "—")
    cols[4].metric("총보수", ticker_page.data.f_frac_pct(etf.get("expense_ratio"))
                   if etf.get("expense_ratio") is not None else "—")

    st.caption("선택한 ETF의 세부 정보는 아래 원형 뷰와 동일한 기준으로 렌더됩니다.")
    ticker_page._etf_sections(ticker, etf, etf.get("price"))
