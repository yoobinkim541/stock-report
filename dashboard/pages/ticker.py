"""종목 분석 — 가격차트 + 가치평가·재무·기관/내부자·공시·실적 (plotly 차트화·U3)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ticker_names
from dashboard import cached, charts, data, theme

_NOBAR = {"displayModeBar": False}


def render():
    ticker = st.session_state.get("ticker", "MSFT")
    # 기간 = 초기 표시 창만 — 데이터는 항상 전체(max) 로드라 과거로 무한 드래그 가능
    period = st.radio("기간", ["3mo", "6mo", "1y", "5y", "전체"], index=1, horizontal=True,
                      label_visibility="collapsed")
    view_days = {"3mo": 90, "6mo": 180, "1y": 365, "5y": 1825, "전체": None}[period]
    hist = cached.ohlc(ticker, period="max")
    yf_price = prev = None
    if hist is not None and not getattr(hist, "empty", True) and "Close" in getattr(hist, "columns", []):
        cl = hist["Close"]
        yf_price = float(cl.iloc[-1])
        prev = float(cl.iloc[-2]) if len(cl) > 1 else yf_price
    pos = data.holding_position(ticker)                 # 보유 포지션(평단 등)|None
    _rq0 = cached.realtime_quote(ticker)
    cur = (_rq0.get("price") if _rq0 else None) or yf_price or 0.0   # 현재가(실시간 우선)

    # 실시간 밴드(8s 자동갱신) — 히어로 ⚡가격·게이지·내 포지션 (호가는 차트 아래 접이식)
    _live_top(ticker, hist, yf_price, prev, pos)

    # 가격 차트 — 풀폭 · 봉/차트종류/지표 컨트롤 (+ 보유 시 평단 수평선)
    if yf_price is not None:
        _price_chart(ticker, hist, pos.get("avg_price_usd") if pos else None,
                     data.trade_events(ticker), view_days)
    else:
        st.info("가격 데이터 없음 (yfinance)")

    # 실시간 호가 — 접이식(기본 접힘)·8초 자동갱신 (차트 우선 레이아웃)
    _orderbook_section(ticker, hist, prev)

    # ETF 는 개별주 섹션(PER·재무·기관·실적) 대신 ETF 전용 뷰(프로필·Top10·보수·괴리율·배당)
    etf = cached.etf(ticker)
    if (etf or {}).get("is_etf"):
        _etf_sections(ticker, etf, cur)
    else:
        _analysis_snapshot(ticker)
        _detail_sections(ticker, yf_price)
    _manage_position(ticker, cur, pos)


@st.fragment
def _selected_trade(event, trades):
    try:
        points = event.selection.points
    except Exception:
        try:
            points = event.get("selection", {}).get("points", [])
        except Exception:
            points = []
    if not points:
        return None
    point = points[0]
    custom = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
    event_id = custom[0] if custom else None
    if not event_id:
        return None
    return next((t for t in trades if t.get("event_id") == event_id), None)


def _trade_detail(t):
    if not t:
        return
    side = "매수" if t.get("side") == "buy" else "매도"
    cur = t.get("currency") or "USD"
    cols = st.columns(4)
    cols[0].metric("구분", side)
    cols[1].metric("수량", f"{float(t.get('qty') or 0):g}주")
    cols[2].metric("체결가", f"{cur} {float(t.get('price') or 0):,.2f}" if t.get("price") else "—")
    cols[3].metric("평단", f"{cur} {float(t.get('avg_price') or 0):,.2f}" if t.get("avg_price") else "—")
    st.caption(f"{t.get('timestamp') or t.get('date')} · {t.get('account') or 'account'} · {t.get('source') or 'source'}"
               + (f" · {t.get('note')}" if t.get("note") else ""))


_TF = {"5분": "5m", "1시간": "1h", "1일": "1d", "주": "1wk", "월": "1mo"}
_TF_SPAN = {"5m": "최근 60일", "1h": "최근 2년"}   # yfinance 인트라데이 보존 한계 (정직 표기)
_MA_OPTS = [5, 10, 20, 60, 120, 200]
_MA_DEFAULT = {"1d": [60, 120, 200], "1wk": [60, 120, 200],   # 요청 기본값
               "1mo": [5, 10, 20, 60, 120, 200], "5m": [20, 60], "1h": [20, 60]}
_IND_OPTS = ["RSI(14)", "볼린저밴드(20,2σ)", "일목균형표"]


def _price_chart(ticker, hist, avg_cost, trades, view_days=None):
    """가격 차트 — 봉 단위(5분~월)·라인/캔들·기술적 분석 도구(MA 세트·RSI·BB·일목)."""
    tf_label = st.segmented_control("봉", list(_TF), default="1일",
                                    label_visibility="collapsed", key="_chart_tf") or "1일"
    c2, c3, _sp = st.columns([1.1, 0.5, 1.4])
    kind = c2.segmented_control("차트 종류", ["📈 라인", "🕯️ 캔들"], default="📈 라인",
                                label_visibility="collapsed", key="_chart_kind")
    tf = _TF[tf_label]
    with c3.popover("📐 지표"):
        mas = st.multiselect("이동평균", _MA_OPTS,
                             default=_MA_DEFAULT.get(tf, [60, 120, 200]), key=f"_ma_{tf}")
        inds = st.multiselect("보조지표", _IND_OPTS, default=["RSI(14)"], key=f"_ind_{tf}")
        show_vol = st.checkbox("거래량 패널", value=True, key=f"_vol_{tf}")
        st.markdown("**추세 분석** (자동 감지 — 표시·참고용)")
        want_lines = st.checkbox("자동 지지/저항선", key=f"_tl_lines_{tf}")
        want_short = st.checkbox("단기 채널 (60봉)", key=f"_tl_short_{tf}")
        want_long = st.checkbox("장기 채널 (250봉)", key=f"_tl_long_{tf}")
        legacy = st.toggle("구형 렌더러", key="_legacy_chart",
                           help="plotly.js CDN 불가 환경 폴백 — 팬 시 y 자동맞춤·인차트 상세 없음")
        st.caption("봉 단위별로 설정이 기억됩니다 · 범례 클릭으로도 개별 토글")
    df = hist
    if tf != "1d":
        df = cached.ohlc_tf(ticker, tf)
        if df is None or getattr(df, "empty", True):
            st.caption(f"⚠️ {tf_label}봉 데이터 없음 — 일봉으로 표시")
            df, tf = hist, "1d"
        elif tf in _TF_SPAN:
            st.caption(f"ℹ️ {tf_label}봉은 {_TF_SPAN[tf]}까지 제공 (yfinance 보존 한계) · 주/월/일봉은 전체 이력")
    label = ticker_names.label(ticker)
    show_rsi = "RSI(14)" in inds
    tls = []
    if want_lines or want_short or want_long:
        ch_key = tuple(k for k, w in (("short", want_short), ("long", want_long)) if w)
        tls = cached.trendlines_for(ticker, tf, want_lines, ch_key)
    show_vol = show_vol and "Volume" in getattr(df, "columns", [])
    fig = charts.price_chart(
        df, label, kind=("candle" if kind == "🕯️ 캔들" else "line"),
        avg_cost=avg_cost, trades=trades, view_days=view_days, mas=mas,
        show_rsi=show_rsi, bollinger="볼린저밴드(20,2σ)" in inds,
        ichimoku="일목균형표" in inds, trend_lines=tls, show_volume=show_vol)
    event = None
    if legacy:
        try:
            event = st.plotly_chart(
                fig, width="stretch", config=charts.PAN_DRAW_CFG,
                key=f"price_chart_{ticker}_{kind}", on_select="rerun", selection_mode="points")
        except TypeError:
            st.plotly_chart(fig, width="stretch", config=charts.PAN_DRAW_CFG)
    else:
        # 커스텀 임베드 — 팬 시 보이는 구간에 y축(가격·거래량) 부드러운 자동 맞춤
        from dashboard import plotly_embed
        h = int(fig.layout.height or 420)
        st.components.v1.html(
            plotly_embed.pannable_chart_html(
                fig, df, height=h, view_days=view_days,
                vol_axis="yaxis2" if show_vol else None),
            height=h + 80)
    st.caption("🖱️ 드래그=이동(y축 자동 맞춤) · 휠=확대/축소 · 더블클릭=원위치 · "
               "✏️ 우상단 모드바 직접 그리기(선·자유곡선·박스)·지우개 — 설정 변경 시 드로잉 초기화")
    selected = _selected_trade(event, trades or []) if legacy else None
    if selected:
        _trade_detail(selected)
    elif trades:
        st.caption("차트의 ▲/▼ 거래 마커를 클릭하면 수량·평단·체결가가 차트 아래 박스에 표시됩니다.")
    if trades:
        with st.expander(f"거래 마커 {len(trades)}건", expanded=False):
            rows = [{
                "일자": t.get("date"),
                "구분": "매수" if t.get("side") == "buy" else "매도",
                "수량": t.get("qty"),
                "체결가": t.get("price"),
                "평단": t.get("avg_price"),
                "계좌": t.get("account"),
                "출처": t.get("source"),
                "메모": t.get("note"),
            } for t in trades]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


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
        theme.render(theme.ticker_hero_html(ticker, ticker_names.display_name(ticker, allow_net=False) or ticker,
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



@st.fragment(run_every=4)
def _orderbook_section(ticker, hist, prev):
    """실시간 호가 — 차트 아래 접이식(기본 접힘·화면 점유 최소화), 4초 자동갱신.

    호가 원천: WS 실시간 캐시(워치리스트 = 1초 스트림) 우선 → REST 폴백 (views.realtime_quote).
    """
    rq = cached.realtime_quote(ticker)
    if not rq or not (rq.get("bids") or rq.get("asks")):
        return                                     # 호가 없음(US/장외) — 섹션 자체 생략
    with st.expander("📊 실시간 호가 (10단계) — 등락% = 전일 종가 기준·상승 🔴/하락 🔵",
                     expanded=False):
        _orderbook(rq, hist, prev, rq.get("price"))


def _orderbook(rq, hist=None, prev_close=None, price=None):
    """실시간 10단계 호가 사다리 (KR HTS 풍 — 잔량 바·등락%·당일/52주 패널·총잔량)."""
    if not rq:
        return
    bids, asks = rq.get("bids") or [], rq.get("asks") or []
    if not (bids or asks):
        if rq.get("market") == "US":
            st.caption("💡 미국 종목은 실시간 가격만 제공 (10단계 호가는 국내만)")
        return
    day = week52 = None
    if hist is not None and not getattr(hist, "empty", True):
        try:
            last = hist.iloc[-1]
            day = {"open": float(last["Open"]), "high": float(last["High"]),
                   "low": float(last["Low"]),
                   "volume": float(last["Volume"]) if "Volume" in hist.columns else None}
            w = hist.tail(252)
            week52 = {"high": float(w["High"].max()), "low": float(w["Low"].min())}
        except Exception:
            pass
    theme.render(theme.orderbook_ladder_html(
        bids, asks, prev_close=prev_close, price=price, day=day, week52=week52))


# ── ETF 전용 뷰 — 프로필·보유 Top10·투자지표 (개별주 섹션 대체·토스증권 풍) ──────

def _f_bil(v):
    """총자산/시총 압축 표기 — $12.9B → $129.1억 대신 $12.9B(글로벌 표준) + 억달러 병기."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v >= 1e9:
        return f"${v/1e9:,.1f}B (${v/1e8:,.0f}억)"
    if v >= 1e6:
        return f"${v/1e6:,.1f}M"
    return f"${v:,.0f}"


def _is_kr_etf(etf):
    return (etf or {}).get("market_type") == "kr" or (etf or {}).get("currency") == "KRW"


def _etf_money(etf, value, dec=2):
    return _f_krw(value, dec=dec) if _is_kr_etf(etf) else data.f_usd(value, dec)


def _etf_asset(etf, value):
    return _f_krw_large(value) if _is_kr_etf(etf) else _f_bil(value)


def _etf_div_amount(etf, value):
    if not value:
        return "—"
    return f"연 {_f_krw(value)}" if _is_kr_etf(etf) else f"연 ${float(value):,.2f}"


def _etf_sections(ticker, etf, price):
    is_kr = _is_kr_etf(etf)
    desc = (etf.get("description") or "").strip()
    if desc:
        st.info(desc[:280] + ("…" if len(desc) > 280 else ""), icon="📊")

    st.subheader("ETF 한눈에")
    dv = etf.get("dividends") or {}
    k = st.columns(5)
    k[0].metric("현재가", _etf_money(etf, etf.get("price") or price, 0 if is_kr else 2))
    k[1].metric("NAV", _etf_money(etf, etf.get("nav"), 0 if is_kr else 2))
    pm = etf.get("premium_pct")
    k[2].metric("괴리율", f"{pm:+.2f}%" if pm is not None else "—",
                help="시장가격과 기준가(NAV)의 차이")
    er = etf.get("expense_ratio")
    k[3].metric("총보수", data.f_frac_pct(er) if er is not None else "—",
                help="연간 총보수/운용보수")
    k[4].metric("분배금 수익률", f"연 {dv['yield_pct']:.2f}%" if dv.get("yield_pct") else "—",
                help="최근 12개월 분배금 합계 ÷ 현재가")

    # ── 프로필 (시가총액/운용자산·운용사·NAV·상장일·발행주식수) ──
    st.subheader("ETF 프로필")
    asset_value = etf.get("total_assets") or etf.get("market_cap")
    rows = [
        ("순자산/AUM", _etf_asset(etf, asset_value)),
        ("운용사", etf.get("family") or "—"),
        ("추종지수", etf.get("benchmark") or "—"),
        ("상장일", etf.get("inception") or "—"),
        ("종목코드", etf.get("stock_code") or ticker),
        ("카테고리", etf.get("category") or "—"),
    ]
    c1, c2 = st.columns(2)
    for i, (k, v) in enumerate(rows):
        (c1 if i % 2 == 0 else c2).markdown(
            f"<div style='display:flex;justify-content:space-between;"
            f"border-bottom:1px solid {theme.BORDER};padding:6px 2px'>"
            f"<span style='color:{theme.MUTED}'>{k}</span><b>{v}</b></div>",
            unsafe_allow_html=True)

    # ── 보유 비중 Top 10 (도넛 + 리스트) ──
    top = etf.get("top_holdings") or []
    if top:
        st.subheader("구성종목 Top 10" if is_kr else "보유 비중 Top 10")
        dcol, lcol = st.columns([1, 1.4])
        with dcol:
            st.plotly_chart(charts.allocation_donut(
                [{"ticker": h.get("symbol") or h.get("name"), "value": h.get("pct") or 0,
                  "name": (h.get("name") if is_kr else
                           ticker_names.display_name(h["symbol"], allow_net=False) or h.get("name"))}
                 for h in top]), width="stretch", config=_NOBAR)
        with lcol:
            if is_kr:
                rows = [{
                    "구성종목": h.get("name") or h.get("symbol"),
                    "비중": f"{h['pct']:.2f}%" if h.get("pct") is not None else "—",
                    "수량": f"{h['shares']:,.0f}" if h.get("shares") is not None else "—",
                    "평가금액": _f_krw_large(h.get("amount")) if h.get("amount") is not None else "—",
                } for h in top]
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                half = (len(top) + 1) // 2
                l1, l2 = st.columns(2)
                for col, chunk in ((l1, top[:half]), (l2, top[half:])):
                    with col:
                        for h in chunk:
                            nm = ticker_names.display_name(h["symbol"], allow_net=False) or h.get("name") or h["symbol"]
                            pct = f"{h['pct']:.2f}%" if h.get("pct") is not None else "—"
                            st.markdown(f"**{nm}** <span style='color:{theme.MUTED}'>{pct}</span>",
                                        unsafe_allow_html=True)
        src = etf.get("top_holdings_source") or ("pykrx PDF" if is_kr else "yfinance funds_data")
        st.caption(f"출처: {src} · 비중은 공시/조회 시점 기준")
    else:
        st.caption("구성종목 데이터 없음" if is_kr else "보유 종목 데이터 없음 (yfinance funds_data)")

    # ── 투자 지표: 운용보수·괴리율 | 배당 ──
    st.subheader("투자 지표")
    ic1, ic2 = st.columns(2)
    with ic1:
        st.markdown("**ETF 정보**")
        st.metric("운용보수", data.f_frac_pct(er) if er is not None else "—",
                  help="연간 총보수 (Expense Ratio)")
        st.metric("괴리율", f"{pm:+.2f}%" if pm is not None else "—",
                  help="(시장가 − NAV) / NAV — 음수 = NAV 대비 할인 거래")
        if is_kr:
            te = etf.get("tracking_error_pct")
            st.metric("추적오차", f"{te:.2f}%" if te is not None else "—",
                      help="ETF 수익률과 추종지수 수익률의 차이")
    with ic2:
        st.markdown(f"**분배금** <span style='color:{theme.MUTED};font-size:.8rem'>최근 12개월</span>",
                    unsafe_allow_html=True)
        d1, d2, d3 = st.columns(3)
        d1.metric("횟수", f"{dv.get('count_12m', 0)}번",
                  dv.get("freq_label") if dv.get("freq_label", "—") != "—" else None)
        d2.metric("주당 분배금", _etf_div_amount(etf, dv.get("per_share_12m")))
        d3.metric("수익률", f"연 {dv['yield_pct']:.2f}%" if dv.get("yield_pct") else "—",
                  help="최근 12개월 분배금 합 ÷ 현재가")

    sw = etf.get("sector_weights") or {}
    if sw:
        with st.expander("🏭 섹터 비중", expanded=False):
            items = sorted(sw.items(), key=lambda x: -x[1])[:11]
            st.plotly_chart(charts.hbar([k for k, _ in items], [v for _, v in items], "섹터 %", pct=False),
                            width="stretch", config=_NOBAR)
    src = etf.get("source") or ("KR ETF" if is_kr else "yfinance")
    st.caption(f"정보·표시용 · 매매신호 아님 · 결측 필드는 — 표기 · 데이터: {src}")


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


def _analysis_snapshot(ticker):
    """개별주 첫 화면용 압축 판단. 상세 섹션의 원자료를 먼저 읽기 쉽게 요약한다."""
    v = cached.valuation(ticker)
    f = cached.financials(ticker)
    iv = cached.intrinsic(ticker)
    summary = data.company_analysis_summary(v.get("metrics") or {}, (f.get("trends") or {}), iv)

    st.subheader("기업 판단 요약")
    verdict, good, risk = st.columns([0.8, 1.4, 1.4])
    verdict.metric("판단", summary["verdict"])
    with good:
        st.markdown("**강점**")
        st.markdown("\n".join(f"- {x}" for x in summary["positives"]))
    with risk:
        st.markdown("**주의점**")
        st.markdown("\n".join(f"- {x}" for x in summary["risks"]))
    st.caption("다음 확인: " + " · ".join(summary["checks"]))


def _valuation(ticker, price=None):
    v = cached.valuation(ticker)
    m = v.get("metrics") or {}
    is_kr = m.get("market_type") == "kr"
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
        b[3].metric("EPS(TTM)", _f_krw(m.get("eps_ttm")) if is_kr else data.f_usd(m.get("eps_ttm")))
        if is_kr:
            c = st.columns(4)
            c[0].metric("시가총액", _f_krw_large(m.get("market_cap")))
            c[1].metric("순이익", _f_krw_large(m.get("net_income")))
            c[2].metric("자본", _f_krw_large(m.get("equity")))
            c[3].metric("BPS", _f_krw(m.get("bps")))
            st.caption(_kr_valuation_caption(m))
    else:
        st.warning(f"밸류에이션 데이터 없음 ({v.get('metrics_error', '')})")
    c = v.get("consensus") or {}
    cur_sym = "₩" if is_kr else "$"
    _fmt_t = (lambda x: f"₩{x:,.0f}") if is_kr else (lambda x: f"${x:,.2f}")
    # 🎯 애널리스트 의견 분포 (토스 풍 — 최다 카테고리 강조)
    rec = {k: c.get(f"rec_{k}") for k in ("strong_sell", "sell", "hold", "buy", "strong_buy")}
    rec_counts = {k: int(x) for k, x in rec.items() if x is not None}
    total_rec = sum(rec_counts.values())
    if total_rec > 0:
        buyers = rec_counts.get("buy", 0) + rec_counts.get("strong_buy", 0)
        st.markdown("##### 🎯 애널리스트 의견")
        st.markdown(f"애널리스트 **{total_rec}명 중 {buyers}명**이 매수 의견을 냈어요.")
        st.plotly_chart(charts.analyst_ratings(rec), width="stretch", config=_NOBAR)
    # 🎯 예상 목표주가 팬 차트 (과거 1y + 1년 후 최고/평균/최저 투영)
    if c.get("target_mean") and price:
        up = c.get("target_upside_pct")
        st.markdown("##### 🎯 예상 목표주가 (1년)")
        st.markdown(f"평균 목표가 **{_fmt_t(c['target_mean'])}**"
                    + (f" — 지금보다 **{up:+.1f}%**" if up is not None else ""))
        st.plotly_chart(
            charts.target_price_fan(cached.ohlc(ticker, period="1y"), price,
                                    c.get("target_high"), c.get("target_mean"),
                                    c.get("target_low"), cur_sym),
            width="stretch", config=_NOBAR)
        st.caption("점선 = 애널리스트 목표가 범위(최고/평균/최저) · 목표가는 컨센서스 — 리비전에 따라 변동")
    if c and (c.get("revision_momentum") is not None or c.get("n_analysts")):
        st.markdown(
            f"리비전 모멘텀 {data.f_ratio(c.get('revision_momentum'), 2)} "
            f"(▲{int(c.get('eps_rev_up_30d') or 0)}/▼{int(c.get('eps_rev_down_30d') or 0)}) · "
            f"애널 {int(c.get('n_analysts') or 0)}명")
    # 💰 멀티플 적정가 — 포워드 EPS × 현재 PER (= 현재가 × PER/fPER). 사용자 채택 방식(1순위).
    fv = data.fair_value_multiple(price, m.get("per"), m.get("forward_pe"))
    if fv:
        _fmt_px = _f_krw if is_kr else (lambda x: data.f_usd(x, 2))
        fc = st.columns(3)
        fc[0].metric("💰 적정가 (EPS×PER)", _fmt_px(fv["fair"]),
                     delta=f"{fv['upside_pct']:+.1f}% vs 현재가",
                     help="포워드 EPS × 현재 PER = 현재가 × (PER/fPER). "
                          "이익이 컨센서스대로 성장하고 멀티플이 유지될 때의 가격 — "
                          "re-rating 은 가정하지 않는 보수형.")
        fc[1].metric("포워드 EPS (내재)", _fmt_px(fv["eps_fwd"]),
                     help="현재가 ÷ fPER — 컨센서스 이익 기준")
        fc[2].metric("PER → fPER", f"{fv['per']:.1f} → {fv['fper']:.1f}",
                     help="PER > fPER = 이익 성장 예상 (그 폭이 곧 상방)")
        st.caption("⚠️ fPER 는 애널리스트 컨센서스 — 리비전에 따라 흔들림 · 멀티플 유지는 가정")

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


def _f_krw(v, dec=0):
    try:
        f = float(v)
        if f != f:
            return "—"
        return f"₩{f:,.{dec}f}"
    except (TypeError, ValueError):
        return "—"


def _f_krw_large(v):
    try:
        f = float(v)
        if f != f:
            return "—"
    except (TypeError, ValueError):
        return "—"
    a = abs(f)
    if a >= 1e12:
        return f"₩{f / 1e12:,.1f}조"
    if a >= 1e8:
        return f"₩{f / 1e8:,.0f}억"
    return f"₩{f:,.0f}"


def _kr_valuation_caption(m):
    bits = [str(m.get("source") or "DART+marcap")]
    if m.get("fiscal_year"):
        bits.append(f"{m['fiscal_year']} 사업보고서")
    if m.get("fs_nm"):
        bits.append(str(m["fs_nm"]))
    elif m.get("fs_div"):
        bits.append("연결 기준" if m.get("fs_div") == "CFS" else "별도 기준")
    if m.get("asof"):
        bits.append(f"마캡 {m['asof']}")
    if m.get("confidence"):
        bits.append(f"신뢰도 {m['confidence']}")
    if m.get("per_status") == "loss":
        bits.append("PER 적자")
    return " · ".join(bits)


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
    is_kr = f.get("market_type") == "kr"
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
        rows = f.get("rows") or []
        if is_kr and rows:
            table = [{
                "연도": r.get("year"),
                "매출": _f_krw_large(r.get("revenue")),
                "영업이익": _f_krw_large(r.get("operating_income")),
                "순이익": _f_krw_large(r.get("net_income")),
                "자산": _f_krw_large(r.get("assets")),
                "부채": _f_krw_large(r.get("liabilities")),
            } for r in reversed(rows[-4:])]
            st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        if is_kr:
            bits = ["출처: DART 단일회사 주요계정"]
            if f.get("fiscal_year"):
                bits.append(f"{f['fiscal_year']} 사업보고서")
            if f.get("fs_nm"):
                bits.append(str(f["fs_nm"]))
            bits.append("매출·마진·부채 추세")
            st.caption(" · ".join(bits))
        else:
            st.caption("출처: SEC EDGAR companyfacts (美) · 무룩어헤드")
    else:
        source = "DART" if is_kr else "SEC EDGAR"
        st.warning(f"재무 데이터 없음 — {source} 확인 필요 ({f.get('error', '')})")


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
                    _apply_action(lambda: _hm().sell_holding(ticker, q if q > 0 else None, price_usd=round(cur, 4)))
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
