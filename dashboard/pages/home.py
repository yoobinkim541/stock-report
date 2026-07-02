"""홈 — 글랜스 랜딩 (포트폴리오 중심). 히어로 KPI + 배분 도넛 + 클릭 보유표 + Phase + 오늘 일정."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ticker_names
from dashboard import cached, charts, data, theme


def render():
    summ = data.portfolio_summary()
    ph = data.phase_badge()

    theme.render(theme.ticker_hero_html(
        symbol="PORT", name="내 포트폴리오", price=summ["total_usd"],
        change=summ.get("pnl_usd"), change_pct=summ["return_pct"],
        asof="USD 해외북 · 스냅샷 기준", currency="USD"))

    c1, c2, c3 = st.columns(3)
    c1.metric("Phase", f"{ph['emoji']} {ph['label']}")
    c2.metric("QQQ 낙폭", f"{ph['drawdown']:+.1f}%")
    c3.metric("DCA 배율", f"{ph['dca']}×")

    st.divider()
    _market_bar()
    _market_map()
    st.divider()

    rows = data.load_holdings()
    if rows:
        left, right = st.columns([1, 1.3])
        with left:
            st.caption("배분")
            st.plotly_chart(charts.allocation_donut(rows), width="stretch",
                            config={"displayModeBar": False})
        with right:
            st.caption("보유 종목 &nbsp;·&nbsp; 🔍 **행을 클릭**하면 해당 종목 상세 분석으로 이동", unsafe_allow_html=True)
            df = pd.DataFrame([{
                "종목": r["ticker"], "이름": (r["name"] or "")[:18],
                "평가액($)": round(r["value"]), "손익%": round(r["ret"], 1), "비중%": round(r["weight"], 1),
            } for r in rows])
            ev = st.dataframe(df, hide_index=True, width="stretch",
                              on_select="rerun", selection_mode="single-row")
            sel = ev.selection.rows if hasattr(ev, "selection") else []
            if sel:
                picked = df.iloc[sel[0]]["종목"]
                if picked and picked != st.session_state.get("ticker"):
                    st.session_state["ticker"] = picked
                    st.toast(f"종목 분석 → {picked}")
                    _tp = st.session_state.get("_ticker_page")
                    if _tp is not None:
                        st.switch_page(_tp)   # 종목 분석 페이지로 자동 이동
                    else:
                        st.rerun()            # 단독 렌더(테스트) 폴백
    else:
        st.warning("보유 데이터 없음 — portfolio_snapshot 확인")

    # Phase 행동 지침 (표시 전용)
    st.info(f"**이번 국면 {ph['emoji']} {ph['label']}** · 권장 DCA 배율 **{ph['dca']}×** "
            f"(QQQ 낙폭 {ph['drawdown']:+.1f}%) — 표시·참고용, 자동집행 없음")

    # 오늘/임박 경제 일정 (상위 5)
    ec = cached.econ(7)
    if ec:
        st.caption("📅 임박 경제 일정")
        for e in ec[:5]:
            st.write(f"{e['marker']} `{e['date_str']}` {e['title']}")

    st.caption("표시·정보용 · 주문 집행 없음 · 과거 기반, 미래 보장 아님")


def _market_bar():
    """시장 지표 — 공포·탐욕지수 + S&P500·나스닥 일/주봉 RSI (경량·15분 캐시)."""
    st.markdown("#### 📊 시장 지표")
    mi = cached.market_indicators()
    fg = mi.get("fear_greed")
    idx = mi.get("indices") or []
    cols = st.columns([1.1, 1, 1])
    with cols[0]:
        if fg:
            theme.render(theme.fng_gauge_html(fg.get("score"), fg.get("prev_week")))
        else:
            st.caption("😱 공포·탐욕 지수 N/A")
    for i in range(2):
        with cols[i + 1]:
            if i < len(idx):
                ix = idx[i]
                theme.render(theme.index_rsi_gauges_html(ix.get("name"), ix.get("price"),
                                                         ix.get("chg"), ix.get("rsi_d"), ix.get("rsi_w")))
            else:
                st.caption("지수 데이터 N/A")


@st.fragment
def _market_map():
    """S&P 500 섹터 시장 맵 — 시총 크기·당일 등락 색 + 타일 클릭→종목분석 (Finviz 풍)."""
    st.markdown("#### 🗺️ S&P 500 시장 맵")
    st.caption("섹터별 · 타일 크기 = 시가총액 · 색 = 당일 등락(🟩상승 / 🟥하락) · **타일 클릭 → 종목 분석**")
    rows = cached.sp500_heatmap()
    if not rows:
        st.info("시장 맵 데이터를 불러오지 못했습니다 (네트워크/시드 확인).")
        return
    ev = st.plotly_chart(charts.market_treemap(rows), width="stretch",
                         config={"displayModeBar": False}, on_select="rerun", key="_heatmap")
    # 타일 클릭 → 라벨(티커) 정규화 → 종목 분석 이동 (섹터 헤더 클릭은 normalize None → 무시)
    picked = None
    sel = getattr(ev, "selection", None)
    pts = (sel.get("points") if isinstance(sel, dict) else getattr(sel, "points", None)) or []
    for p in pts:
        lab = p.get("label") if isinstance(p, dict) else getattr(p, "label", None)
        tk = ticker_names.normalize_input(lab or "")
        if tk:
            picked = tk
            break
    if picked and picked != st.session_state.get("ticker"):
        st.session_state["ticker"] = picked
        st.toast(f"종목 분석 → {picked}")
        _tp = st.session_state.get("_ticker_page")
        if _tp is not None:
            st.switch_page(_tp)
        else:
            st.rerun()
