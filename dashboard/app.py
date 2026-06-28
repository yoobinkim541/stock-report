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


@st.cache_data(ttl=900, show_spinner="불러오는 중…")
def _insider(t):
    return views.insider_trades(t)


@st.cache_data(ttl=1800, show_spinner="불러오는 중…")
def _disc(t):
    return views.disclosures(t)


@st.cache_data(ttl=3600, show_spinner="랭킹 계산 중… (최대 1분)")
def _screener(n):
    return views.screener(n)


@st.cache_data(ttl=3600, show_spinner="백테스트 실행 중… (최대 1분)")
def _backtest():
    return views.backtest_summary()


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

t_val, t_fin, t_risk, t_inst, t_disc, t_news, t_cal, t_scr = st.tabs(
    ["가치평가", "재무제표", "리스크", "기관 보유", "공시", "뉴스", "캘린더", "스크리너"])

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

# ── 기관 보유 (13F + 매집 + 내부자거래) ────────────────────────────────────
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

    ins = _insider(ticker)
    txs = ins.get("transactions") or []
    if txs:
        st.caption(f"내부자거래 (SEC Form 4) · 순매수 {ins.get('net_buy_shares', 0):,.0f}주 "
                   f"(매수 {ins.get('n_buys', 0)}·매도 {ins.get('n_sells', 0)})")
        rows = [{"일자": t["date"], "임원": t["owner"], "직책": t["role"],
                 "구분": {"P": "매수", "S": "매도", "A": "무상", "M": "행사"}.get(t["code"], t["code"]),
                 "수량": f"{t['shares']:,.0f}", "단가": data.f_usd(t["price"]) if t["price"] else "—"}
                for t in txs[:25]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    elif ins.get("error"):
        st.caption(f"내부자거래: {ins['error']}")
    st.caption("정보·표시용")

# ── 공시 (美 SEC · 韓 DART) ──────────────────────────────────────────────────
with t_disc:
    dd = _disc(ticker)
    lst = dd.get("list") or []
    if lst:
        mkt = dd.get("market")
        st.caption(f"{'DART' if mkt == 'KR' else 'SEC'} 최근 공시")
        if mkt == "KR":
            rows = [{"일자": x["date"], "공시": x["title"], "제출인": x.get("filer", ""),
                     "링크": x["url"]} for x in lst]
        else:
            rows = [{"일자": x["date"], "유형": x["form"], "설명": x.get("title", ""),
                     "링크": x["url"]} for x in lst]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"링크": st.column_config.LinkColumn("원문", display_text="열기")})
    else:
        st.warning(f"공시 없음 ({dd.get('error', '')})")

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

# ── 스크리너 + 백테스트 ─────────────────────────────────────────────────────
with t_scr:
    st.caption("종목 랭킹 스크리너 — NASDAQ100 · LightGBM QQQ 초과수익 예측")
    topn = st.slider("상위 N", 10, 50, 20, 5)
    sc = _screener(topn)
    meta = sc.get("meta") or {}
    if meta:
        st.caption(f"OOS IC {data.f_ratio(meta.get('ic'), 3)} · ICIR {data.f_ratio(meta.get('icir'), 2)} · "
                   f"상위10% 초과 {data.f_frac_pct_s(meta.get('top_decile'))} · 학습 {meta.get('train_end', '')}")
    rows = sc.get("rows") or []
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.warning(f"랭킹 없음 ({sc.get('error', '')})")
    st.caption("⚠️ 생존편향 + 검증상 종목선택 무엣지 — 정보·표시용, 매매신호 아님")

    st.divider()
    st.caption("ML 전략 백테스트 — QQQ 3년 실데이터 (nested OOS)")
    bt = _backtest()
    if bt.get("error"):
        st.warning(f"백테스트 실패: {bt['error']}")
    else:
        a = st.columns(3)
        a[0].metric("ML CAGR", data.f_frac_pct(bt["ml"]["cagr"]))
        a[1].metric("ML Sharpe", data.f_ratio(bt["ml"]["sharpe"], 2))
        a[2].metric("ML MDD", data.f_frac_pct(bt["ml"]["mdd"]))
        b = st.columns(3)
        b[0].metric("QQQ CAGR", data.f_frac_pct(bt["qqq"]["cagr"]))
        b[1].metric("QQQ Sharpe", data.f_ratio(bt["qqq"]["sharpe"], 2))
        b[2].metric("QQQ MDD", data.f_frac_pct(bt["qqq"]["mdd"]))
        v = bt.get("verdict", "")
        (st.success if ("채택" in v and "비채택" not in v) else st.warning)(v)
        for r in bt.get("reasons", []):
            st.caption("· " + r)
        eq = bt.get("equity")
        if eq is not None:
            try:
                st.line_chart(eq)
            except Exception:
                pass
    st.caption("⚠️ 검증상 ML 종목선택·장중타이밍 무엣지 — 정보·표시용 (검증 통과 공격은 구조적 레버리지뿐)")

st.divider()
st.caption("표시·정직 우선 · 주문 집행 없음 · 무엣지 신호는 참고용 · 과거 기반, 미래 보장 아님")
