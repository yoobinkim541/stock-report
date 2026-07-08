"""홈 — 글랜스 랜딩 (포트폴리오 중심). 히어로 KPI + 배분 도넛 + 클릭 보유표 + Phase + 오늘 일정."""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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

    # 🚦 ML 게이트 한눈 (주간 재검증 — 상세: 리서치 → 축 게이트)
    try:
        g, t3 = cached.axes_gate(), cached.tier3_gate()

        def _ax(e):
            if not e.get("available"):
                return "—"
            code = (e.get("verdict") or {}).get("code", "?")
            rec = ((e.get("recommendation") or {}).get("chosen") or "")
            ap = "·반영중" if (e.get("shadow") or {}).get("applied") else ""
            return f"{code}{('·' + rec) if rec else ''}{ap}"

        if t3.get("available") and t3.get("fresh"):
            t3s = f"GO ×{t3.get('reco_lev'):.2f}" + (" (모의 슬리브 ON)" if t3.get("sleeve_env") else "")
        else:
            t3s = "미기록" if not t3.get("available") else "stale"
        st.caption(f"🚦 ML 게이트 — 구조레버 {t3s} · KR축 {_ax(g.get('kr', {}))} · "
                   f"US축 {_ax(g.get('us', {}))} · 상세: 리서치 → 축 게이트")
    except Exception:
        pass

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


_MAPS = {   # 라벨 → (cached 로더명, 부가 캡션)
    "S&P 500": ("sp500_heatmap",
                "기술 섹터는 세부 카테고리(반도체·소프트웨어/클라우드·IT서비스·하드웨어)로 분해"),
    "코스피 200": ("kr200_heatmap", "업종별(Naver) · 시총 = marcap 스냅샷"),
    "러셀 2000": ("russell2000_heatmap",
                 "미국 소형주 **근사** — 보통주 시총 1001~3000위 (러셀 공식 구성 아님·정직 표기)"),
}


@st.fragment
def _market_map():
    """시장 맵 3종(S&P500·코스피200·러셀2000) — 시총 크기·등락 색 + 타일 클릭→종목분석."""
    st.markdown("#### 🗺️ 시장 맵")
    which = st.segmented_control("시장", list(_MAPS), default="S&P 500",
                                 label_visibility="collapsed", key="_map_kind") or "S&P 500"
    loader, note = _MAPS[which]
    st.caption("타일 크기 = 시가총액 · 색 = 당일 등락(🟩상승 / 🟥하락) · "
               f"**타일 클릭 → 종목 분석** · {note}")
    rows = getattr(cached, loader)()
    if not rows:
        st.info("시장 맵 데이터를 불러오지 못했습니다 (첫 로드는 크론 스냅샷 대기 — 최대 20분).")
        return
    ev = st.plotly_chart(charts.market_treemap(rows), width="stretch",
                         config={"displayModeBar": False}, on_select="rerun",
                         key=f"_heatmap_{loader}")
    # 타일 클릭 → 종목 분석 이동. 리프 라벨 = 정확한 티커 → rows 멤버십으로 판정
    # (normalize_input 부분매칭 함정: 세부 헤더 "반도체"→TSM 오이동 — 헤더/섹터 클릭은 무시)
    valid = {r.get("ticker") for r in rows}
    picked = None
    sel = getattr(ev, "selection", None)
    pts = (sel.get("points") if isinstance(sel, dict) else getattr(sel, "points", None)) or []
    for p in pts:
        lab = p.get("label") if isinstance(p, dict) else getattr(p, "label", None)
        if lab in valid:
            picked = lab
            break
    if picked and picked != st.session_state.get("ticker"):
        st.session_state["ticker"] = picked
        st.toast(f"종목 분석 → {picked}")
        _tp = st.session_state.get("_ticker_page")
        if _tp is not None:
            st.switch_page(_tp)
        else:
            st.rerun()
