"""종목 분석 — 가격차트 + 가치평가·재무·기관/내부자·공시·실적 (plotly 차트화·U3)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ticker_names
from dashboard import cached, charts, data, theme

_NOBAR = {"displayModeBar": False}


def render():
    ticker = st.session_state.get("ticker", "MSFT")
    period = st.radio("기간", ["3mo", "6mo", "1y"], index=1, horizontal=True,
                      label_visibility="collapsed")
    hist = cached.ohlc(ticker, period=period)
    price = prev = None
    if hist is not None and not getattr(hist, "empty", True) and "Close" in getattr(hist, "columns", []):
        cl = hist["Close"]
        price = float(cl.iloc[-1])
        prev = float(cl.iloc[-2]) if len(cl) > 1 else price
    chg = (price - prev) if (price is not None and prev) else None
    chg_pct = (chg / prev * 100) if (chg is not None and prev) else None
    ts = data.technical_score(hist["Close"]) if price is not None else None

    # 상단 밴드: 심볼 히어로 + 기술 신호 게이지 (게이지에 충분한 폭 — 기존 [2.3,1] 슬리버 폐지)
    hcol, gcol = st.columns([1.6, 1])
    with hcol:
        theme.render(theme.ticker_hero_html(ticker, ticker_names.display_name(ticker) or ticker,
                                            price, chg, chg_pct, f"{period} · yfinance 종가", ""))
    with gcol:
        if ts:
            theme.render(theme.rating_gauge_html(ts["score"], sub=ts["sub"]))
        else:
            st.caption("기술 신호 N/A")

    # 가격 차트 — 풀폭
    if price is not None:
        st.plotly_chart(charts.price_line(hist, ticker_names.label(ticker)), width="stretch", config=_NOBAR)
    else:
        st.info("가격 데이터 없음 (yfinance)")

    _detail_sections(ticker, price)


# 섹션 셀렉터 + fragment — 활성 섹션만 렌더(그 섹션 네트워크만 호출)·전환은 fragment만 rerun.
# 기존 st.tabs 는 숨겨도 5개 바디 전부 렌더 → 매 로드마다 네트워크 5회. 그걸 1회로.
_SECTIONS = ["가치평가", "재무제표", "기관·내부자", "공시", "실적"]


@st.fragment
def _detail_sections(ticker, price):
    sec = st.segmented_control("상세 분석", _SECTIONS, default="가치평가",
                               key="ticker_section", label_visibility="collapsed") or "가치평가"
    if sec == "재무제표":
        _financials(ticker)
    elif sec == "기관·내부자":
        _institutional(ticker)
    elif sec == "공시":
        _disclosures(ticker)
    elif sec == "실적":
        _earnings(ticker)
    else:
        _valuation(ticker, price)


def _valuation(ticker, price=None):
    v = cached.valuation(ticker)
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
    iv = cached.intrinsic(ticker)
    rim, ddm = iv.get("rim"), iv.get("ddm")
    if rim or ddm:
        with st.expander("💰 적정가치 (RIM·DDM 모델 · 가정 민감)", expanded=False):
            if price:
                st.plotly_chart(charts.value_bullet(price, rim, ddm), width="stretch", config=_NOBAR)
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
        with st.expander("📈 실적 서프라이즈 이력", expanded=False):
            _surprise_chart(h, "실적 서프라이즈 (최근)")
    st.caption("정보·표시용 · 매매신호 아님")


def _surprise_chart(history, caption):
    """서프라이즈 % 부호 막대 (오래된→최근) + 원표."""
    hh = list(reversed(history))   # 최신순 → 시간순
    labels = [str(x.get("date", ""))[:10] for x in hh]
    vals = [x.get("surprise_pct") for x in hh]
    st.caption(caption)
    if any(x is not None for x in vals):
        st.plotly_chart(charts.signed_bars(labels, [float(x or 0) for x in vals]),
                        width="stretch", config=_NOBAR)
    st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch")


def _financials(ticker):
    f = cached.financials(ticker)
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


def _institutional(ticker):
    i = cached.institutional(ticker)
    acc = i.get("accum")
    if acc:
        st.metric("매집 강도 점수", data.f_ratio(acc.get("accum_score"), 1),
                  help="OBV·CMF·상승하락 거래량비·A/D — 높을수록 매집")
        sig = acc.get("signals") or {}
        if sig:
            g = st.columns(3)
            g[0].metric("OBV(정규화)", data.f_ratio(sig.get("obv_norm"), 2))
            g[1].metric("CMF", data.f_ratio(sig.get("cmf"), 2))
            g[2].metric("상승/하락 거래량", data.f_ratio(sig.get("updown_ratio"), 2))
        inst = acc.get("institutional")
        if inst:
            st.caption("13F 기관 지분 (교차검증)")
            st.dataframe(pd.DataFrame([inst]) if isinstance(inst, dict) else pd.DataFrame(inst),
                         hide_index=True, width="stretch")
    else:
        st.info(f"기관 매집 데이터 없음 ({i.get('error_accum', '')})")
    ins = cached.insider(ticker)
    txs = ins.get("transactions") or []
    if txs:
        with st.expander(f"내부자거래 (SEC Form 4) · 순매수 {ins.get('net_buy_shares', 0):,.0f}주 "
                         f"(매수 {ins.get('n_buys', 0)}·매도 {ins.get('n_sells', 0)})", expanded=False):
            rows = [{"일자": t["date"], "임원": t["owner"], "직책": t["role"],
                     "구분": {"P": "매수", "S": "매도", "A": "무상", "M": "행사"}.get(t["code"], t["code"]),
                     "수량": f"{t['shares']:,.0f}", "단가": data.f_usd(t["price"]) if t["price"] else "—"}
                    for t in txs[:25]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    elif ins.get("error"):
        st.caption(f"내부자거래: {ins['error']}")
    st.caption("정보·표시용")


def _disclosures(ticker):
    dd = cached.disclosures(ticker)
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


def _earnings(ticker):
    cal = cached.earnings(ticker)
    h = cal.get("history") or []
    if h:
        _surprise_chart(h, f"{ticker} 실적 서프라이즈 이력")
    else:
        st.warning(f"실적 이력 없음 ({cal.get('error', '')})")
