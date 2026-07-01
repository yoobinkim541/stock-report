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
    yf_price = prev = None
    if hist is not None and not getattr(hist, "empty", True) and "Close" in getattr(hist, "columns", []):
        cl = hist["Close"]
        yf_price = float(cl.iloc[-1])
        prev = float(cl.iloc[-2]) if len(cl) > 1 else yf_price
    pos = data.holding_position(ticker)                 # 보유 포지션(평단 등)|None
    _rq0 = cached.realtime_quote(ticker)
    cur = (_rq0.get("price") if _rq0 else None) or yf_price or 0.0   # 현재가(실시간 우선)

    # 실시간 밴드(8s 자동갱신) — 히어로 ⚡가격·게이지·내 포지션·호가
    _live_top(ticker, hist, yf_price, prev, pos)

    # 가격 차트 — 풀폭 (+ 보유 시 평단 수평선)
    if yf_price is not None:
        st.plotly_chart(charts.price_line(hist, ticker_names.label(ticker),
                                          avg_cost=pos.get("avg_price_usd") if pos else None),
                        width="stretch", config=_NOBAR)
    else:
        st.info("가격 데이터 없음 (yfinance)")

    _detail_sections(ticker, yf_price)
    _manage_position(ticker, cur, pos)


@st.fragment(run_every=8)
def _live_top(ticker, hist, yf_price, prev, pos):
    """히어로 ⚡실시간가 + 게이지 + 내 포지션(평단·손익) + 호가 — 8초 자동갱신."""
    rq = cached.realtime_quote(ticker)
    rt = rq.get("price") if rq else None
    price = rt if (rt and rt > 0) else yf_price
    src = "⚡ 실시간 KIS" if (rt and rt > 0) else f"yfinance 종가"
    chg = (price - prev) if (price is not None and prev) else None
    chg_pct = (chg / prev * 100) if (chg is not None and prev) else None
    ts = data.technical_score(hist["Close"]) if (hist is not None and yf_price is not None) else None

    hcol, gcol = st.columns([1.6, 1])
    with hcol:
        theme.render(theme.ticker_hero_html(ticker, ticker_names.display_name(ticker) or ticker,
                                            price, chg, chg_pct, src, ""))
    with gcol:
        if ts:
            theme.render(theme.rating_gauge_html(ts["score"], sub=ts["sub"]))
        else:
            st.caption("기술 신호 N/A")

    # 내 포지션 (보유 시) — 평단·평가손익·주수·평가액
    if pos:
        avg = pos.get("avg_price_usd")
        cur_ret = (price / avg - 1) * 100 if (avg and price) else pos.get("ret", 0)
        cur_val = pos["shares"] * price if price else pos.get("value", 0)
        m = st.columns(4)
        m[0].metric("평단", data.f_usd(avg))
        m[1].metric("평가손익", data.f_pct_s(cur_ret))
        m[2].metric("보유주수", f"{pos['shares']:g}주")
        m[3].metric("평가액", data.f_usd(cur_val, 0))

    _orderbook(rq)


def _orderbook(rq):
    """실시간 10단계 호가 (KR 완전·US 가격만 안내)."""
    if not rq:
        return
    bids, asks = rq.get("bids") or [], rq.get("asks") or []
    if not (bids or asks):
        if rq.get("market") == "US":
            st.caption("💡 미국 종목은 실시간 가격만 제공 (10단계 호가는 국내만)")
        return
    st.markdown("**📊 실시간 호가**")
    cb, ca = st.columns(2)
    with cb:
        st.caption("매수 (BID)")
        for px, qty in bids[:5]:
            st.markdown(f"<div style='font-family:monospace;color:{theme.GREEN}'>"
                        f"{px:,.2f} <span style='color:{theme.MUTED}'>× {qty:,.0f}</span></div>",
                        unsafe_allow_html=True)
    with ca:
        st.caption("매도 (ASK)")
        for px, qty in asks[:5]:
            st.markdown(f"<div style='font-family:monospace;color:{theme.RED}'>"
                        f"{px:,.2f} <span style='color:{theme.MUTED}'>× {qty:,.0f}</span></div>",
                        unsafe_allow_html=True)


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


# ── ⚙️ 내 포지션 관리 — 추가·적립·축소 (실제 추적 포트폴리오 기록 · 실주문 아님) ──────
# holding_manager 경유(atomic write + 교차프로세스 락) = 봇 /holding 과 동일 경로.
# 실계좌 브로커 주문 없음(기록 전용·grep 강제). 해외(USD) general 계좌만.
def _hm():
    import holding_manager
    return holding_manager


def _apply_action(fn):
    """holding_manager 액션 실행 → 결과 표시 + 캐시비움 + rerun (포지션 즉시 갱신)."""
    try:
        with st.spinner("기록 중… (가격 갱신 포함)"):
            msg = fn()
        st.success(str(msg) if msg else "완료")
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"실패: {e}")


@st.fragment
def _manage_position(ticker, cur_price, pos):
    with st.expander("⚙️ 내 포지션 관리 — 추가·적립·축소 (실제 추적 포트폴리오 · 실주문 아님)",
                     expanded=False):
        cur = float(cur_price or 0.0)
        held = pos["shares"] if pos else 0.0
        st.caption(f"현재가 ${cur:,.2f} · 보유 {held:g}주"
                   + (f" · 평단 {data.f_usd(pos.get('avg_price_usd'))}" if pos else " · 미보유"))
        mode = st.segmented_control("작업", ["➕ 추가", "💧 적립(금액)", "➖ 축소"], default="➕ 추가",
                                    key="mng_mode", label_visibility="collapsed") or "➕ 추가"
        if mode == "💧 적립(금액)":
            amt = st.number_input("적립 금액 ($)", min_value=0.0, value=100.0, step=10.0, key="acc_amt")
            qty = (amt / cur) if cur > 0 else 0.0
            st.caption(f"→ 현재가 기준 약 **{qty:.4f}주** 소수점 적립 (평단 자동 재계산)")
            if st.button("💧 적립 기록", key="acc_btn", type="primary",
                         disabled=(amt <= 0 or cur <= 0), width="stretch"):
                _apply_action(lambda: _hm().buy_holding(ticker, round(qty, 4), round(cur, 4)))
        elif mode == "➖ 축소":
            if not pos:
                st.info("보유하지 않은 종목입니다.")
            else:
                q = st.number_input(f"축소 주수 (0 = 전량, 보유 {held:g})", min_value=0.0,
                                    max_value=float(held), value=0.0, step=0.0001, format="%.4f", key="red_qty")
                lab = "전량 정리" if q <= 0 else f"{q:.4f}주 축소"
                if st.button(f"➖ {lab} 기록", key="red_btn", width="stretch"):
                    _apply_action(lambda: _hm().sell_holding(ticker, q if q > 0 else None))
        else:                                                       # 추가(신규·증액)
            c = st.columns(2)
            q = c[0].number_input("주수 (소수점 가능)", min_value=0.0, value=1.0, step=0.0001,
                                  format="%.4f", key="add_qty")
            px = c[1].number_input("단가 ($)", min_value=0.0, value=round(cur, 2), step=0.01, key="add_px")
            st.caption(f"→ 약 ${q * px:,.2f} 취득 기록 (평단 자동 재계산)")
            if st.button("➕ 보유 추가 기록", key="add_btn", type="primary",
                         disabled=(q <= 0 or px <= 0), width="stretch"):
                _apply_action(lambda: _hm().buy_holding(ticker, round(q, 4), round(px, 4)))
        st.caption("holding_manager 안전기록(atomic·교차프로세스 락) · 봇 /holding 과 동일 · 실계좌 주문 없음(기록 전용)")
