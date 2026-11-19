"""차트 풀뷰 — 전체화면 풀사이즈 차트 (종목분석과 동일 컨트롤·컴포넌트 공용).

봉/기간/라인·캔들/📐 지표/⇄ 비교 전부 `ticker._price_chart` 그대로 — 차트 iframe 은 창의 남은
높이를 채우고(frame_fit) 그리기 도구는 왼쪽 독. ↙ 종목 분석으로 복귀.
"""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from dashboard import cached, chart_workspace_ui, data, theme, ui_actions
from dashboard.pages import ticker as ticker_pg


def _active_ticker() -> str:
    raw = st.session_state.get("ticker", "MSFT")
    norm = ticker_names.normalize_input(str(raw or "")) or str(raw or "MSFT")
    if norm != raw:
        st.session_state["ticker"] = norm
    return norm


def _fmt_header_price(ticker: str, price: float | None) -> str:
    if price is None:
        return "—"
    if ticker_names.is_macro(ticker):
        unit = ticker_names.macro_unit(ticker)
        if unit in {"₩", "$"}:
            return f"{unit}{price:,.2f}"
        if unit == "%":
            return f"{price:,.2f}%"
        return f"{price:,.2f}{(' ' + unit) if unit else ''}"
    if str(ticker).upper().endswith((".KS", ".KQ")):
        return f"₩{price:,.0f}"
    return f"${price:,.2f}"


def _fmt_header_delta(ticker: str, delta: float | None, prev: float | None) -> str:
    if delta is None or not prev:
        return "—"
    pct = f"{delta / prev * 100:+.2f}%"
    sign = "+" if delta >= 0 else "-"
    mag = abs(delta)
    if ticker_names.is_macro(ticker):
        unit = ticker_names.macro_unit(ticker)
        if unit == "₩":
            return f"{sign}₩{mag:,.0f} ({pct})"
        if unit == "$":
            return f"{sign}${mag:,.2f} ({pct})"
        if unit == "%":
            return f"{delta:+,.2f} ({pct})"
        return f"{delta:+,.2f}{(' ' + unit) if unit else ''} ({pct})"
    if str(ticker).upper().endswith((".KS", ".KQ")):
        return f"{sign}₩{mag:,.0f} ({pct})"
    return f"{sign}${mag:,.2f} ({pct})"


def render():
    # 진짜 전체화면 — 사이드바·하단 마퀴·상단 헤더 숨김 + 여백 최소화. 머리줄은 한 줄
    # (복귀·종목/가격·워크스페이스·자동 갱신)로 묶고, 차트 iframe 이 창 남은 높이를 채운다
    # (frame_fit). 사이드바를 숨기므로 복귀 버튼은 가격 데이터 유무와 관계없이 항상 둔다.
    st.markdown("""<style>
      [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
      [data-testid="stHeader"] { display: none !important; }
      .stMainBlockContainer { padding: 0.5rem 1rem 0 !important; max-width: 100% !important; }
      .stMainBlockContainer [data-testid="stVerticalBlock"] { gap: 0.5rem; }
      .tn-tape { display: none !important; }
    </style>""", unsafe_allow_html=True)
    t = _active_ticker()
    cback, ctitle, cws, clive = st.columns([0.9, 4.2, 1.15, 1.25], vertical_alignment="center")
    if cback.button("↙ 종목 분석", key="_chart_page_back",
                    help="사이드바가 숨겨져 있어도 종목 분석으로 돌아갑니다"):
        ui_actions.open_ticker(t)
    workspace_mode = cws.toggle("워크스페이스", value=False, key="_chart_workspace_mode",
                                help="저장형 멀티차트 작업공간으로 보기")
    if workspace_mode:
        chart_workspace_ui.render_chart_workspace(st.session_state.get("_cw_workspace"))
        return

    hist = cached.ohlc(t, period="max")
    if hist is None or getattr(hist, "empty", True):
        st.info("가격 데이터가 없습니다. 시세 출처가 비어 있거나 종목 코드를 확인하세요.")
        return
    cl = hist["Close"].dropna()
    yf_price = float(cl.iloc[-1]) if len(cl) else None
    prev = float(cl.iloc[-2]) if len(cl) > 1 else yf_price
    rq = cached.realtime_quote(t)
    price = (rq.get("price") if rq else None) or yf_price
    chg = (price - prev) if (price is not None and prev) else None
    col = theme.GREEN if (chg or 0) >= 0 else theme.RED
    ctitle.markdown(
        f'<div style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap">'
        f'<b style="font-size:1.15rem">{ticker_names.label(t)}</b>'
        f'<span style="font-size:1rem;font-weight:700;font-variant-numeric:tabular-nums">'
        f'{_fmt_header_price(t, price)}</span>'
        f'<span style="color:{col};font-size:0.88rem">'
        f'{_fmt_header_delta(t, chg, prev)}</span>'
        f'<span style="color:{theme.MUTED};font-size:0.75rem">'
        f'{"⚡ 실시간 시세" if rq and rq.get("price") else "지연 종가"}</span></div>',
        unsafe_allow_html=True)
    pos = data.holding_position(t)
    live = clive.toggle("⚡ 자동 갱신", key="_chart_live",
                        help="8초마다 실시간가로 마지막 봉·현재가 갱신 — 보던 위치·드로잉 유지")
    _chart = (ticker_pg._price_chart_replay if st.session_state.get("_chart_replay_playing")
              else (ticker_pg._price_chart_live if live else ticker_pg._price_chart_frag))
    _chart(t, hist, pos.get("avg_price_usd") if pos else None,
           data.trade_events(t), fullscreen=True)
