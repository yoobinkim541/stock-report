"""dashboard/app.py — 퀀트 터미널 Streamlit 엔트리 (QT1: 6모듈 배선).

실행: bash scripts/run_dashboard.sh  (프로젝트 .venv 의 streamlit)
모듈은 기존 providers/·reports/·ml/ 함수를 그대로 재사용(views.py). 스크리너/
백테스트(스크리너 탭)는 QT3.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import auth, data, views

st.set_page_config(page_title="퀀트 터미널", page_icon="📊", layout="wide")

if not auth.password_gate():
    st.stop()


# ── 캐시 래퍼 (네트워크 호출 15분 캐시) ─────────────────────────────────────
@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _val(t):
    return views.valuation(t)


@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _fin(t):
    return views.financials(t)


@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _inst(t):
    return views.institutional(t)


@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _news(t):
    return views.news_digest(t)


@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _cal(t):
    return views.earnings_calendar(t)


@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _risk():
    return views.risk_report_text(data.portfolio_weights())


@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _iv(t):
    return views.intrinsic_value(t)


@st.cache_data(ttl=1800, show_spinner="불러오는 중…")
def _econ(days):
    return views.econ_events(days)


# ── 헤더: 포트폴리오 + Phase ────────────────────────────────────────────────
summ = data.portfolio_summary()
ph = data.phase_badge()
st.title("📊 퀀트 터미널")
c1, c2, c3, c4 = st.columns(4)
c1.metric("내 포트 (USD)", f"${summ['total_usd']:,.0f}", f"{summ['return_pct']:+.1f}%")
c2.metric("Phase", f"{ph['emoji']} {ph['label']}")
c3.metric("QQQ 낙폭", f"{ph['drawdown']:+.1f}%")
c4.metric("DCA 배율", f"{ph['dca']}×")

ticker = st.text_input("종목 (예: MSFT, 005930.KS)", value="MSFT").strip().upper()

t_val, t_fin, t_risk, t_inst, t_news, t_cal, t_scr = st.tabs(
    ["가치평가", "재무제표", "리스크", "기관 보유", "뉴스", "캘린더", "스크리너"])

# ── 가치평가 (상대 + 컨센서스 + 서프라이즈) ─────────────────────────────────
with t_val:
    v = _val(ticker)
    m = v.get("metrics") or {}
    if m:
        a = st.columns(4)
        a[0].metric("PER", data.f_ratio(m.get("per")))
        a[1].metric("Fwd PE", data.f_ratio(m.get("forward_pe")))
        a[2].metric("PBR", data.f_ratio(m.get("pbr")))
        a[3].metric("PSR", data.f_ratio(m.get("psr")))
        b = st.columns(4)
        b[0].metric("ROE", data.f_frac_pct(m.get("roe")))
        b[1].metric("배당수익률", data.f_pct(m.get("div_yield"), 2))
        b[2].metric("배당성장 3Y", data.f_frac_pct_s(m.get("div_growth_3y")))
        b[3].metric("EPS(TTM)", data.f_usd(m.get("eps_ttm")))
    else:
        st.warning(f"밸류에이션 데이터 없음 ({v.get('metrics_error', '')})")
    c = v.get("consensus") or {}
    if c:
        st.markdown(
            f"**컨센서스** · 목표가 {data.f_usd(c.get('target_mean'), 0)} "
            f"(상승여력 {data.f_pct_s(c.get('target_upside_pct'))}) · "
            f"애널 {int(c.get('n_analysts') or 0)}명 · "
            f"리비전 모멘텀 {data.f_ratio(c.get('revision_momentum'), 2)} "
            f"(▲{int(c.get('eps_rev_up_30d') or 0)}/▼{int(c.get('eps_rev_down_30d') or 0)})")
    iv = _iv(ticker)
    rim, ddm = iv.get("rim"), iv.get("ddm")
    if rim or ddm:
        st.markdown("**적정가치 (모델·가정 민감)**")
        cc = st.columns(3)
        if rim:
            cc[0].metric("RIM 적정가", data.f_usd(rim["mid"], 0),
                         help=f"범위 {data.f_usd(rim['low'], 0)}~{data.f_usd(rim['high'], 0)}")
        if ddm:
            cc[1].metric("DDM 적정가" + ("" if iv.get("ddm_reliable") else " ⚠️"),
                         data.f_usd(ddm["mid"], 0),
                         help=None if iv.get("ddm_reliable") else "배당성향 낮아 신뢰도 낮음")
        if iv.get("upside_pct") is not None:
            cc[2].metric("RIM 상승여력", data.f_pct_s(iv["upside_pct"]))
        st.caption("RIM=잔여이익(고ROE 반영·범용) · DDM=배당할인(고배당주만 유효) · "
                   "r 8~11%·g 4% 밴드 · ROE 영속 가정(보수성 주의)")
    h = v.get("history") or []
    if h:
        st.caption("실적 서프라이즈 (최근)")
        st.dataframe(pd.DataFrame(h), hide_index=True, width="stretch")
    st.caption("정보·표시용 · 매매신호 아님")

# ── 재무제표 (SEC EDGAR) ────────────────────────────────────────────────────
with t_fin:
    f = _fin(ticker)
    tr = f.get("trends") or {}
    if tr:
        a = st.columns(4)
        a[0].metric("매출 YoY", data.f_frac_pct(tr.get("rev_yoy")))
        a[1].metric("순마진", data.f_frac_pct(tr.get("net_margin")),
                    data.f_frac_pct_s(tr.get("net_margin_chg")))
        a[2].metric("부채/자산", data.f_frac_pct(tr.get("debt_to_assets")),
                    data.f_frac_pct_s(tr.get("debt_to_assets_chg")), delta_color="inverse")
        a[3].metric("연속 보고연수", f"{int(tr.get('n_years') or 0)}년")
        if tr.get("is_loss"):
            st.warning("최근 적자 구간")
        st.caption("출처: SEC EDGAR companyfacts (美) · 무룩어헤드")
    else:
        st.warning(f"재무 데이터 없음 — 美 종목만 지원 ({f.get('error', '')})")

# ── 리스크 (포트폴리오) ─────────────────────────────────────────────────────
with t_risk:
    st.caption("포트폴리오 전체 (USD 북) · 표시 전용·배분 불변")
    st.code(_risk(), language=None)

# ── 기관 보유 (13F + 매집) ──────────────────────────────────────────────────
with t_inst:
    i = _inst(ticker)
    acc, inst = i.get("accum"), i.get("inst13f")
    if acc:
        st.caption("매집 강도")
        st.json(acc, expanded=False)
    if inst:
        st.caption("13F 기관 지분")
        st.json(inst, expanded=False)
    if not acc and not inst:
        st.warning("기관 데이터 없음")
    st.caption("정보·표시용")

# ── 뉴스 ────────────────────────────────────────────────────────────────────
with t_news:
    st.markdown(_news(ticker) or "_뉴스 없음_")

# ── 캘린더 (경제 일정 + 실적 이력) ─────────────────────────────────────────
with t_cal:
    ec = _econ(14)
    if ec:
        st.caption("경제 일정 — 향후 2주 (saveticker)")
        st.dataframe(
            pd.DataFrame([{"일시": e["date_str"], "중요도": e["marker"], "이벤트": e["title"]}
                         for e in ec[:40]]),
            hide_index=True, width="stretch")
    cal = _cal(ticker)
    h = cal.get("history") or []
    if h:
        st.caption(f"{ticker} 실적 서프라이즈 이력")
        st.dataframe(pd.DataFrame(h), hide_index=True, width="stretch")
    elif not ec:
        st.warning("캘린더 데이터 없음")

# ── 스크리너 (QT3) ──────────────────────────────────────────────────────────
with t_scr:
    st.info("스크리너 + 백테스트는 QT3에서 구현 예정")

st.divider()
st.caption("표시·정직 우선 · 주문 집행 없음 · 무엣지 신호는 참고용 · 과거 기반, 미래 보장 아님")
