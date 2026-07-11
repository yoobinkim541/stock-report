"""종목 분석 — 가격차트 + 가치평가·재무·기관/내부자·공시·실적 (plotly 차트화·U3)."""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from dashboard import cached, charts, data, theme

_NOBAR = {"displayModeBar": False}


def render():
    ticker = st.session_state.get("ticker", "MSFT")
    # 접미사 없는 6자리 KR 코드(예: "005930")는 yfinance 가 빈 데이터를 줘 차트·밸류가
    # 통째로 안 뜸 → .KS 보정(방어). US 티커는 6자리 숫자가 아니라 무영향. 위젯 상태는
    # 건드리지 않음(_tsel 리셋 함정 회피) — 로컬 정규화만.
    if ticker and ticker.isdigit() and len(ticker) == 6:
        ticker = ticker + ".KS"
    hist = cached.ohlc(ticker, period="max")
    yf_price = prev = None
    if hist is not None and not getattr(hist, "empty", True) and "Close" in getattr(hist, "columns", []):
        cl = hist["Close"].dropna()               # 마감 직후 미확정 봉(NaN 종가) 제외 —
        if len(cl):                               # 안 하면 yf_price=NaN 이 hero·진입레벨 오염
            yf_price = float(cl.iloc[-1])
            prev = float(cl.iloc[-2]) if len(cl) > 1 else yf_price
    pos = data.holding_position(ticker)                 # 보유 포지션(평단 등)|None
    _rq0 = cached.realtime_quote(ticker)
    cur = (_rq0.get("price") if _rq0 else None) or yf_price or 0.0   # 현재가(실시간 우선)

    # 실시간 밴드(8s 자동갱신) — 히어로 ⚡가격·게이지·내 포지션 (호가는 차트 아래 접이식)
    _live_top(ticker, hist, yf_price, prev, pos)

    # 가격 차트 — 풀폭 · 봉/기간/차트종류/지표 컨트롤 (+ 보유 시 평단 수평선)
    if yf_price is not None:
        # ⚡자동 갱신 토글은 fragment **밖** — 켜고 끄기가 래퍼(주기 재실행)를 전환해야 함
        live = st.toggle("⚡ 자동 갱신 (8초)", key="_chart_live",
                         help="실시간가로 마지막 봉·현재가 갱신 — 보던 위치·드로잉 유지")
        _chart = _price_chart_live if live else _price_chart_frag
        _chart(ticker, hist, pos.get("avg_price_usd") if pos else None,
               data.trade_events(ticker))
    else:
        st.info("가격 데이터 없음 (yfinance)")

    # 매크로·지수 자산 — 주식 섹션(호가·진입레벨·밸류·재무·포지션관리) 대신 전용 뷰
    if ticker_names.is_macro(ticker):
        _macro_sections(ticker, hist)
        _llm_related_section(ticker)
        return

    # 실시간 호가 — 접이식(기본 접힘)·8초 자동갱신 (차트 우선 레이아웃)
    _orderbook_section(ticker, hist, prev)

    # 🎯 진입 레벨 가이드 — 기술 지지/저항 × 밸류 기준가 (표시·참고용)
    if yf_price is not None:
        _entry_levels_section(ticker, hist, cur or yf_price)

    # ETF 는 개별주 섹션(PER·재무·기관·실적) 대신 ETF 전용 뷰(프로필·Top10·보수·괴리율·배당)
    etf = cached.etf(ticker)
    if (etf or {}).get("is_etf"):
        _etf_sections(ticker, etf, cur)
    else:
        _analysis_snapshot(ticker)
        _detail_sections(ticker, yf_price)
    _llm_related_section(ticker)
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


_TF = {"5분": "5m", "1시간": "1h", "2시간": "2h", "4시간": "4h",
       "1일": "1d", "주": "1wk", "월": "1mo"}
# yfinance 인트라데이 보존 한계 (정직 표기) — 2h/4h 는 1h 리샘플이라 같은 한계
_TF_SPAN = {"5m": "최근 60일", "1h": "최근 2년", "2h": "최근 2년", "4h": "최근 2년"}
_MA_OPTS = [5, 10, 20, 60, 120, 200]
_MA_DEFAULT = {"1d": [60, 120, 200], "1wk": [60, 120, 200],   # 요청 기본값
               "1mo": [5, 10, 20, 60, 120, 200], "5m": [20, 60], "1h": [20, 60]}
_TOP_INDS = ["이동평균선", "자동 추세선·채널", "지수이평(EMA)", "볼린저 밴드", "일목균형표",
             "슈퍼트렌드", "엔벨로프", "파라볼릭 SAR", "프라이스 채널", "매물대", "프랙탈",
             "VWAP(세션)", "앵커드 VWAP", "켈트너 채널", "KAMA", "샹들리에 엑시트"]


def _chart_events(ticker, df, ev_sel) -> tuple[list, list]:
    """이벤트 마커 조립 — 실적(E·beat 초록/miss 빨강)·배당(D)·뉴스(N·방향색). graceful.

    실적=valuation history(서프라이즈%) · 배당=일봉 Dividends 열(리샘플 봉은 열 없음→스킵)
    · 뉴스=LLM 구조화 라벨(point-in-time·표시 전용). 반환 (events, zones=[]).
    """
    events: list[dict] = []
    if "실적 E" in ev_sel:
        try:
            for h in (cached.valuation(ticker) or {}).get("history") or []:
                sp = h.get("surprise_pct")
                if not h.get("date"):
                    continue
                if sp is None:
                    hover, color = f"실적 {h['date']}", theme.MUTED
                else:
                    beat = sp >= 0
                    color = theme.GREEN if beat else theme.RED
                    hover = (f"실적 {h['date']} · EPS {h.get('eps_actual')} vs "
                             f"예상 {h.get('eps_est')} ({sp:+.1f}% {'beat' if beat else 'miss'})")
                events.append({"date": h["date"], "marker": "E", "color": color,
                               "hover": hover})
        except Exception:
            pass
    if "배당 D" in ev_sel and "Dividends" in getattr(df, "columns", []):
        try:
            dv = df["Dividends"]
            for ts_, amt in dv[dv > 0].tail(40).items():
                events.append({"date": ts_, "marker": "D", "color": "#22d3ee",
                               "hover": f"배당락 {str(ts_)[:10]} · {float(amt):,.4f}"})
        except Exception:
            pass
    if "뉴스 N" in ev_sel:
        try:
            for n in cached.chart_news(ticker) or []:
                d_ = n.get("direction")
                color = theme.GREEN if (d_ or 0) > 0 else (theme.RED if (d_ or 0) < 0
                                                           else theme.MUTED)
                events.append({"date": n.get("date"), "marker": "N", "color": color,
                               "hover": f"📰 {n.get('event_type')} · {n.get('title')} "
                                        f"(강도 {n.get('strength')})"})
        except Exception:
            pass
    return events, []


@st.fragment(run_every=8)
def _price_chart_live(ticker, hist, avg_cost, trades, fullscreen: bool = False):
    """⚡ 자동 갱신 차트 — 8초 fragment 재실행 (실시간가는 피더가 클라이언트 패치).

    live 모드의 메인 차트 html 은 **바이트 안정** — 실시간가를 서버에서 bake 하면
    8초마다 srcdoc 이 바뀌어 iframe 재마운트(그리던 드로잉 리셋 + 수 MB 재전송)가
    일어난다. 대신 초소형 피더 컴포넌트가 localStorage 로 가격을 push 하고 차트
    iframe 이 마지막 봉·현재가선만 in-place 패치 (plotly_embed live 경로).
    """
    _price_chart(ticker, hist, avg_cost, trades, fullscreen, live=True)


@st.fragment
def _price_chart_frag(ticker, hist, avg_cost, trades, fullscreen: bool = False):
    """기본 차트 fragment — 봉/기간/지표 컨트롤 변경이 **차트만** 부분 rerun.

    비프래그먼트로 render 에 인라인이면 컨트롤 하나 바꿀 때마다 페이지 전체(히어로·호가·
    진입레벨·분석 섹션)가 재실행돼 체감 버벅임의 주원인이 된다 (H-series UX 모델 복원).
    """
    _price_chart(ticker, hist, avg_cost, trades, fullscreen)


@st.fragment
def _llm_related_section(ticker):
    """🤖 AI 연관 종목 — LLM 아이디어 (버튼 게이트·표시 전용·환각은 provider 가 폐기).

    fragment — 추천/이동 버튼이 페이지 전체 rerun 을 유발하지 않음. 정직 라벨 필수.
    """
    with st.expander("🤖 AI 연관 종목 추천"):
        st.caption("LLM 아이디어 — **검증 안 된 참고용·매매신호 아님** · 응답 티커는 "
                   "화이트리스트 검증(환각 폐기) · 24시간 캐시")
        res_key = f"_llmrel_res_{ticker}"
        if res_key not in st.session_state:
            if st.button("🤖 추천 받기", key=f"_llmrel_btn_{ticker}",
                         help="LLM 1회 호출 (최대 60초·이후 24h 캐시)"):
                with st.spinner("LLM 연관 종목 생성 중… (최대 60초)"):
                    st.session_state[res_key] = cached.llm_related(ticker)
                st.rerun(scope="fragment")
            return
        # 결과는 세션에 고정 — 캐시 만료 후 리런이 버튼 없이 LLM 을 재호출하는 누수 방지
        items, status = st.session_state[res_key]
        if not items:
            msg = {"disabled": "DASH_LLM_RELATED_ENABLED=0 — 비활성화됨",
                   "empty": "LLM 이 검증 통과 종목을 내지 못했습니다 — 잠시 후 다시 시도"}
            st.info(msg.get(status, f"추천 실패 — {status}"))
            if st.button("다시 시도", key=f"_llmrel_retry_{ticker}"):
                cached.llm_related.clear()
                st.session_state.pop(res_key, None)
                st.rerun(scope="fragment")
            return
        for it in items:
            c1, c2, c3 = st.columns([2.2, 3, 0.9], vertical_alignment="center")
            c1.write(f"**{ticker_names.label(it['ticker'], maxlen=24)}** "
                     f"<span style='color:{theme.MUTED};font-size:.72rem'>"
                     f"{it.get('relation', '')}</span>", unsafe_allow_html=True)
            c2.caption(it.get("reason", ""))
            if c3.button("분석 →", key=f"_llmrel_nav_{ticker}_{it['ticker']}"):
                st.session_state["ticker"] = it["ticker"]
                _tp = st.session_state.get("_ticker_page")
                if _tp is not None:
                    st.switch_page(_tp)
                else:
                    st.rerun()
        st.caption(f"상태: {'디스크 캐시' if status == 'cached' else 'LLM 신규 생성'} · "
                   "표시·아이디어용 — 투자 판단·자동 반영 없음")


def _macro_sections(ticker, hist):
    """매크로·지수 전용 분석 — 성과 밴드 + 자산 특화 + 연관 자산 상관 (표시·참고용).

    주식 섹션(밸류·재무·기관·공시·호가·진입레벨·포지션관리)은 매크로에 무의미 → 대체.
    환율=환전 타이밍·포트 민감도 / 금리=역사 백분위 / 금=금은비 / 암호·지수=상관 맥락.
    """
    prof = data.series_profile(hist)
    if prof:
        st.markdown("##### 📊 성과 프로필")

        def _c(v):
            # 값 없음(None)은 중립색 — `(None or 0) >= 0` 이 초록을 칠하던 것 방지
            if not isinstance(v, (int, float)):
                return None
            return theme.GREEN if v >= 0 else theme.RED

        theme.render(theme.position_band_html([
            ("1주", data.f_pct_s(prof["r1w"]), _c(prof["r1w"])),
            ("1개월", data.f_pct_s(prof["r1m"]), _c(prof["r1m"])),
            ("3개월", data.f_pct_s(prof["r3m"]), _c(prof["r3m"])),
            ("1년", data.f_pct_s(prof["r1y"]), _c(prof["r1y"])),
            ("YTD", data.f_pct_s(prof["ytd"]), _c(prof["ytd"])),
        ]))
        pos52 = prof.get("pos52")
        theme.render(theme.position_band_html([
            ("52주 위치", f"{pos52 * 100:.0f}%" if pos52 is not None else "—",
             None),
            ("52주 고점", f"{prof['hi52']:,.2f}" if prof.get("hi52") else "—", None),
            ("52주 저점", f"{prof['lo52']:,.2f}" if prof.get("lo52") else "—", None),
            ("연변동성", f"{prof['vol_ann']:.1f}%" if prof.get("vol_ann") is not None else "—",
             None),
            ("200일선 이격", data.f_pct_s(prof.get("ma200_gap")),
             _c(prof.get("ma200_gap"))),
        ]))

    # ── 자산 특화 ──
    if ticker == "KRW=X":
        fx = cached.fx_timing() or {}
        if fx.get("ok"):
            st.markdown("##### 💱 환전 타이밍 (3년 백분위)")
            st.info(f"{fx.get('emoji', '')} **{fx.get('verdict', '')}** — 현재 "
                    f"{fx.get('rate', 0):,.1f}원 · 3년 분포 상위 {fx.get('pct_display', 0):.0f}% "
                    f"구간 · 권장: {fx.get('action', '')} (환전 배율 {fx.get('multiplier', 1):g}×)")
        summ = data.portfolio_summary() or {}
        tot = summ.get("total_usd")
        if tot:
            st.caption(f"💼 내 포트 민감도 — 해외북 ${tot:,.0f} 기준 환율 **10원 변동 ≈ "
                       f"₩{tot * 10:,.0f}** 원화 평가액 변동 (자연 환헤지 없음 가정)")
    elif ticker == "^TNX":
        pct = data.history_percentile(hist, years=10)
        if pct is not None:
            st.markdown("##### 🏦 금리 레벨 맥락")
            st.info(f"현재 미 10년물 금리는 **최근 10년 분포의 상위 {100 - pct:.0f}%** 수준 — "
                    f"금리 ↑ = 성장주 할인율·금 보유비용 압박, ↓ = 위험자산 우호 "
                    f"(백분위 {pct:.0f})")
    elif ticker == "GC=F":
        try:
            si = cached.ohlc("SI=F", "6mo")
            ratio = float(hist["Close"].dropna().iloc[-1]) / float(si["Close"].dropna().iloc[-1])
            st.markdown("##### 🥇 금은비 (Gold/Silver Ratio)")
            st.info(f"금은비 **{ratio:.1f}** — 역사 평균대 60~70. 80↑ = 은 상대 저평가, "
                    f"50↓ = 금 상대 저평가 신호로 해석되곤 함 (참고용)")
        except Exception:
            pass

    # ── 🔗 연관 자산 — 90일 상관·30일 등락 ──
    rel = cached.macro_corr(ticker) or []
    if rel:
        st.markdown("##### 🔗 연관 자산 — 90일 상관")
        cols = st.columns(min(len(rel), 3))
        for i, r in enumerate(rel[:3]):
            with cols[i]:
                corr = r.get("corr90")
                cs = f"{corr:+.2f}" if corr is not None else "—"
                strong = corr is not None and abs(corr) >= 0.5
                st.metric(f"{r['label']}", cs,
                          delta=(f"{r['chg30']:+.2f}% (30일)" if r.get("chg30") is not None
                                 else None), delta_color="off",
                          help=r.get("note", ""))
                if strong:
                    st.caption("⚡ 상관 뚜렷" + (" (역방향)" if corr < 0 else ""))
                st.caption(r.get("note", ""))
    st.caption("표시·참고용 — 상관은 국면에 따라 변하며 인과가 아님 · 주문 집행 없음")


def _price_chart(ticker, hist, avg_cost, trades, fullscreen: bool = False,
                 live: bool = False):
    """가격 차트 — 봉·기간·라인/캔들·지표·비교 컨트롤 (풀뷰 페이지와 공용 컴포넌트).

    fullscreen=True 면 차트 풀뷰 페이지 모드 — 높이 확대·⛶ 는 복귀 버튼.
    live=True(⚡자동갱신 래퍼) — 실시간가 서버 bake 생략 + 피더 컴포넌트로 클라이언트
    패치 (html 바이트 안정 = 8초 재실행이 iframe 을 재마운트하지 않음).
    """
    # 컨트롤 한 줄 — 봉 | 라인/캔들 | 지표 | 비교 | 기간 | ⛶ (좁은 화면은 자동 줄바꿈)
    ctf, ckind, c3, c4, cper, cfull = st.columns([1.45, 0.72, 0.34, 0.34, 1.4, 0.35],
                                                 vertical_alignment="center")
    if fullscreen:
        if cfull.button("↙", key="_chart_back", help="종목 분석으로 복귀"):
            pg = st.session_state.get("_ticker_page")
            if pg:
                st.switch_page(pg)
    else:
        if cfull.button("⛶", key="_chart_fullbtn", help="전체화면 풀차트 — 모든 컨트롤 그대로"):
            pg = st.session_state.get("_chart_page")
            if pg:
                st.switch_page(pg)
    tf_label = ctf.segmented_control("봉", list(_TF), default="1일",
                                     label_visibility="collapsed", key="_chart_tf") or "1일"
    kind = ckind.segmented_control("차트 종류", ["📈 라인", "🕯️ 캔들", "🟩 HA"], default="📈 라인",
                                   label_visibility="collapsed", key="_chart_kind",
                                   help="HA = 하이킨아시(평활 캔들·표시용 — 실체결가와 다름)")
    # 기간 = 초기 표시 창 — 데이터는 뷰의 5배 팬버퍼로 윈도잉(charts.view_window·"전체"=전량)
    period = cper.radio("기간", ["3mo", "6mo", "1y", "5y", "전체"], index=1, horizontal=True,
                        label_visibility="collapsed", key="_chart_period")
    view_days = {"3mo": 90, "6mo": 180, "1y": 365, "5y": 1825, "전체": None}[period]
    tf = _TF[tf_label]
    # ── ⇄ 비교 — 최대 3종목 % 상대수익 오버레이 (사이드바 검색과 동일 정규화) ──
    with c4.popover("⇄ 비교"):
        st.caption("최대 3종목 — 기간 시작=0% 상대수익으로 겹쳐 비교")
        _raw = st.multiselect(
            "비교 종목 (한글·영문·티커)",
            [t for t in ticker_names.universe() if t != ticker],
            max_selections=3, format_func=ticker_names.search_label,
            accept_new_options=True, key="_cmp_sel",
            help="목록에 없어도 티커 직접 입력 가능 (예: AMD · BRK-B)")
        cmp_tickers = []
        for _r in _raw or []:
            _tk = ticker_names.normalize_input(_r)
            if _tk and _tk != ticker and _tk not in cmp_tickers:
                cmp_tickers.append(_tk)
            elif not _tk:
                st.warning(f"'{_r}' 종목을 찾지 못했습니다")
        # 같은 지수 추종 ETF 원클릭 추가 (etf_meta 정적 시드 — 무네트워크).
        # 기존 multiselect 의 session_state 는 불변 — 별도 pills 를 merge (위젯 상태 함정 회피)
        import etf_meta
        _peers = etf_meta.peers_of(ticker)
        if _peers:
            _pks = st.pills("같은 지수 추종 — 원클릭 추가", _peers, selection_mode="multi",
                            key="_cmp_peers", format_func=ticker_names.search_label) or []
            for _pk in _pks:
                if _pk in cmp_tickers:
                    continue
                if len(cmp_tickers) >= 3:
                    st.caption("⚠️ 비교는 최대 3종목 — 초과 선택은 제외")
                    break
                cmp_tickers.append(_pk)
        pr_mode = False
        if cmp_tickers:
            pr_mode = st.toggle("PR(가격) 기준 — 분배금 제외", key="_cmp_pr",
                                help="기본=TR(배당재투자·조정종가). 커버드콜 ETF 비교 시 "
                                     "가격만의 성과 확인용 — 비교 모드·일봉 전용")
            if pr_mode and tf != "1d":
                st.caption("ℹ️ PR 기준은 일봉 전용 — 현재 봉 단위에선 TR(조정종가)로 표시")
    with c3.popover("📐 지표"):
        st.markdown("**상단 지표** — 가격 차트 오버레이")
        top = st.pills("상단 지표", _TOP_INDS, selection_mode="multi",
                       default=["이동평균선"], key=f"_top_{tf}",
                       label_visibility="collapsed") or []
        mas = []
        if "이동평균선" in top:
            mas = st.multiselect("이동평균 기간", _MA_OPTS,
                                 default=_MA_DEFAULT.get(tf, [60, 120, 200]), key=f"_ma_{tf}")
        emas = []
        if "지수이평(EMA)" in top:
            emas = st.multiselect("EMA 기간", _MA_OPTS, default=[20, 60], key=f"_ema_{tf}")
        if "VWAP(세션)" in top and tf not in ("5m", "1h"):
            st.caption("ℹ️ VWAP(세션)은 인트라데이(5분·1시간) 전용 — 일봉+ 는 앵커드 VWAP 사용")
        if "앵커드 VWAP" in top:
            st.caption("ℹ️ 앵커드 VWAP 앵커 = 기간(라디오) 시작 · 팬 시 고정")
        want_lines = want_short = want_long = False
        if "자동 추세선·채널" in top:
            want_lines = True
            cch1, cch2 = st.columns(2)
            # 채널 기본 ON — pill 선택 즉시 지지/저항선 + 상승/하락 채널까지 그려짐
            want_short = cch1.checkbox("단기 채널(60봉)", value=True, key=f"_tl_short_{tf}")
            want_long = cch2.checkbox("장기 채널(250봉)", value=True, key=f"_tl_long_{tf}")
            st.caption("채널 = 회귀 ±2σ 자동 감지 — 상승(초록)/하락(빨강)/횡보(회색)·"
                       "라벨에 방향 표기 · 지지/저항선 동시 표시")
        # 매크로(환율·금·금리 등)는 실적·배당·진입존이 무의미 — 뉴스만 노출
        _ev_opts = (["뉴스 N"] if ticker_names.is_macro(ticker)
                    else ["실적 E", "배당 D", "뉴스 N", "진입존 🎯"])
        _ev_def = [] if ticker_names.is_macro(ticker) else ["실적 E", "배당 D"]
        st.markdown("**이벤트 마커** — " + ("뉴스 오버레이" if ticker_names.is_macro(ticker)
                                          else "실적·배당·뉴스·진입존 오버레이 (이 프로젝트 데이터)"))
        ev_sel = st.pills("이벤트", _ev_opts, selection_mode="multi", default=_ev_def,
                          key=f"_ev_{tf}_{'macro' if ticker_names.is_macro(ticker) else 'eq'}",
                          label_visibility="collapsed") or []
        st.markdown("**하단 지표** — 서브 패널")
        bottom = st.pills("하단 지표", ["거래량", "RSI", "MACD", "스토캐스틱",
                                     "Aroon", "%b", "PVT", "분기 EPS"], selection_mode="multi",
                          default=["거래량", "RSI"], key=f"_bot_{tf}",
                          label_visibility="collapsed") or []
        log_scale = st.toggle("로그 스케일", key=f"_logscale_{tf}",
                              help="가격축을 로그로 — 장기·급등 종목의 % 변화 비교에 유리 "
                                   "(비교 모드·서브패널 제외)")
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
    show_rsi = "RSI" in bottom
    show_macd = "MACD" in bottom
    show_stoch = "스토캐스틱" in bottom
    show_aroon = "Aroon" in bottom
    show_bbpct = "%b" in bottom
    show_pvt = "PVT" in bottom
    tls = []
    if want_lines or want_short or want_long:
        ch_key = tuple(k for k, w in (("short", want_short), ("long", want_long)) if w)
        tls = cached.trendlines_for(ticker, tf, want_lines, ch_key)
    show_vol = "거래량" in bottom and "Volume" in getattr(df, "columns", [])
    # 비교 종목 데이터 — 메인과 동일 봉 단위 파이프라인 (결측은 정직 스킵).
    # PR 토글(일봉·비교 모드): 시리즈를 raw Close(분배 제외)로 치환 — 실패 시 TR 유지.
    _use_pr = pr_mode and tf == "1d"
    compare = {}
    for _ct in cmp_tickers:
        series = None
        if _use_pr:
            _d = cached.tr_pr(_ct)
            series = _d["pr"] if _d else None
        if series is None:
            _cdf = cached.ohlc(_ct, "max") if tf == "1d" else cached.ohlc_tf(_ct, tf)
            if _cdf is not None and not getattr(_cdf, "empty", True) and "Close" in _cdf.columns:
                series = _cdf["Close"]
        if series is not None:
            _sfx = " PR" if _use_pr else ""
            compare[ticker_names.label(_ct, maxlen=22) + _sfx] = series
        else:
            st.caption(f"⚠️ {_ct} {tf_label}봉 데이터 없음 — 비교에서 제외")
    if compare and _use_pr:
        _dm = cached.tr_pr(ticker)
        if _dm:
            df = pd.DataFrame({"Close": _dm["pr"]})   # 메인도 PR — 거래량 패널 자연 생략
        else:
            st.caption("⚠️ 메인 PR 데이터 없음 — 메인은 TR(조정종가) 유지")
    if compare:
        st.caption("⇄ 비교 모드 — % 상대수익 겹침 · "
                   + ("**PR(가격) 기준 — 분배금 제외**" if _use_pr
                      else "TR(배당재투자·조정종가) 기준")
                   + " · 가격 지표(캔들·평단·MA·매물대 등) 비활성")
        show_vol = show_vol and "Volume" in getattr(df, "columns", [])   # PR 스왑 후 재판정
    # 직렬화 윈도잉 — max 전량(장기주 ~11k봉) fig+bounds 직렬화가 지표/기간 토글마다
    # 수 MB push + 수초 ScriptRunner 점유의 주원인. 뷰의 5배 팬버퍼+지표 워밍업만
    # 남긴다("전체"=무윈도잉). 이하 실시간 패치·이벤트마커·HA·bounds 전부 같은 윈도우.
    df = charts.view_window(df, view_days)
    if compare:
        compare = {k: charts.view_window(s, view_days) for k, s in compare.items()}
    # ⚡ live(자동갱신) — 실시간가는 **클라이언트 패치**(피더 iframe → localStorage →
    # plotly_embed patchLast). 서버 bake 를 하면 8초마다 srcdoc 이 바뀌어 iframe
    # 재마운트(그리던 드로잉 리셋 + 대형 재전송)가 일어나므로 live 땐 생략해 html 을
    # 바이트 안정으로 유지. HA·비교·구형 렌더러는 클라 패치 미지원 → 종전 bake 유지.
    _client_rt = (bool(live) and not compare and not legacy and kind != "🟩 HA"
                  and df is not None and not getattr(df, "empty", True))
    # ⚡ 실시간 — 마지막 봉을 KIS 실시간가로 패치 (fresh 시·비교 모드 제외).
    # 캐시된 df 원본 오염 금지 → copy 후 수정. HA 변환 앞이라 HA 도 최신가 반영.
    if not compare and not _client_rt and df is not None and not getattr(df, "empty", True):
        _rt = (cached.realtime_quote(ticker) or {}).get("price")
        if _rt and _rt > 0:
            df = df.copy()
            for _c in ("Close", "High", "Low"):     # int 열에 float 대입 = pandas 3 에러
                if _c in df.columns and getattr(df[_c].dtype, "kind", "") != "f":
                    df[_c] = df[_c].astype("float64")
            _il = df.index[-1]
            df.loc[_il, "Close"] = float(_rt)
            if "High" in df.columns:
                df.loc[_il, "High"] = max(float(df.loc[_il, "High"]), float(_rt))
            if "Low" in df.columns:
                df.loc[_il, "Low"] = min(float(df.loc[_il, "Low"]), float(_rt))
    # 로그 스케일은 비교(%) 모드와 공존 불가 — 비교 시 자동 비활성
    use_log = bool(log_scale) and not compare
    _df_events = df   # 이벤트 조립용 원본 참조 — HA 변환은 Dividends 열을 보존 안 함
    # 하이킨아시 — 표시용 평활 변형(OHLC 재계산·거래량 보존). OHLC 없으면 라인 폴백.
    use_ha = kind == "🟩 HA" and not compare
    if use_ha:
        _ha = charts.heikin_ashi(df)
        if _ha is not df:
            df = _ha
            st.caption("🟩 하이킨아시 — 평활 캔들(표시용) · 시고저종은 HA 재계산값 — "
                       "실제 체결가·평단 비교는 근사")
        else:
            use_ha = False
            st.caption("⚠️ 하이킨아시는 OHLC 필요 — 라인으로 표시")
    events, zones = _chart_events(ticker, _df_events, ev_sel) if not compare else ([], [])
    if "진입존 🎯" in ev_sel and not compare:
        try:
            zones = _chart_entry_zones(ticker, hist, float(hist["Close"].iloc[-1]))
        except Exception:
            zones = []
    fig = charts.price_chart(
        df, label, kind=("candle" if (kind == "🕯️ 캔들" or use_ha) else "line"),
        avg_cost=avg_cost, trades=trades, view_days=view_days, mas=mas,
        show_rsi=show_rsi, bollinger="볼린저 밴드" in top,
        ichimoku="일목균형표" in top, trend_lines=tls, show_volume=show_vol,
        supertrend="슈퍼트렌드" in top, envelope="엔벨로프" in top,
        fractals="프랙탈" in top, vol_profile="매물대" in top,
        emas=emas, psar="파라볼릭 SAR" in top, donchian_on="프라이스 채널" in top,
        vwap=("VWAP(세션)" in top and tf in ("5m", "1h")), avwap="앵커드 VWAP" in top,
        compare=compare, show_macd=show_macd, show_stoch=show_stoch, log_scale=use_log,
        keltner="켈트너 채널" in top, kama="KAMA" in top,
        chandelier="샹들리에 엑시트" in top,
        show_aroon=show_aroon, show_bbpct=show_bbpct, show_pvt=show_pvt,
        fund_eps=(((cached.valuation(ticker) or {}).get("history") or [])
                  if "분기 EPS" in bottom and not compare
                  and not ticker_names.is_macro(ticker) else None),
        events=events, zones=zones)
    if fullscreen:                                  # ⛶ 풀뷰 — 뷰포트 거의 채우는 높이
        fig.update_layout(height=840)
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
        _bj = (plotly_embed.compare_bounds_json(df, compare, view_days)
               if compare else None)                   # 비교 모드 — % 프레임으로 y 맞춤
        # 드로잉 영속화 키 — 좌표계가 다른 조합(스케일·HA)은 분리 버킷
        _scale = "pct" if compare else ("log" if use_log else "lin")
        _sk = f"{ticker}:{tf}:{_scale}" + (":ha" if use_ha else "")
        st.components.v1.html(
            plotly_embed.pannable_chart_html(
                fig, df, height=h, view_days=view_days,
                vol_axis="yaxis2" if show_vol else None, bounds_json=_bj,
                fit_viewport=fullscreen, pct_mode=bool(compare), y_log=use_log,
                store_key=_sk, dock=fullscreen, live=_client_rt),
            height=h + 164)
        if _client_rt:
            # ⚡ 피더 — <1KB 컴포넌트만 8초 재실행마다 재마운트(가격+신선도 push).
            # 메인 차트 html 은 위에서 바이트 안정 → 드로잉·뷰·플롯 상태 유지.
            _rtp = (cached.realtime_quote(ticker) or {}).get("price")
            st.components.v1.html(plotly_embed.realtime_feed_html(_sk, _rtp), height=0)
    st.caption("🖱️ 드래그=이동(y축 자동 맞춤) · 휠=확대/축소 · 더블클릭=원위치 · "
               "✏️ 모드바 직접 그리기(선·자유곡선·박스)·지우개 + 차트 위 도구바: "
               "🧲 자석(봉 OHLC 스냅)·─ 수평선·🔱 피보나치·📏 측정·📐 회귀추세(±2σ)·"
               "⚓ 고정VWAP·📊 볼륨프로필(POC)·🗑 지우기 · "
               "드로잉은 이 브라우저에 종목·봉·스케일별 자동 저장")
    selected = _selected_trade(event, trades or []) if legacy else None
    if selected:
        _trade_detail(selected)
    elif trades:
        st.caption("차트의 ▲/▼ 거래 마커 클릭 = 상세 · 전체 이력·되돌리기는 하단 ⚙️ 내 포지션 관리")
    _alert_section(ticker, hist)


@st.fragment
def _alert_section(ticker, hist):
    """🔔 가격 알림 — 차트에서 본 레벨을 봇 알림으로 (bot/price_alerts store 공용).

    발동 체크·텔레그램 발송은 봇의 5분 루프가 담당 — 여기는 등록/목록/삭제만.
    표시·알림용, 주문 아님. (iframe 드로잉은 서버로 못 돌아오므로 가격 입력 프리필 방식)
    fragment — 등록/삭제가 expander 만 부분 rerun (차트 재렌더·드로잉 세션 무영향).
    """
    alerts = data.ticker_alerts(ticker)
    # 라벨은 고정 — 개수를 넣으면 등록 직후 라벨 변경으로 expander 가 접힘(위젯 상태=라벨 키)
    with st.expander("🔔 가격 알림"):
        if alerts:
            st.caption(f"활성 알림 {len(alerts)}건")
        try:
            _last = float(hist["Close"].iloc[-1]) if hist is not None else 0.0
        except Exception:
            _last = 0.0
        c1, c2, c3, c4 = st.columns([1.2, 1, 1.4, 0.8], vertical_alignment="bottom")
        px = c1.number_input("목표가", min_value=0.0, value=round(_last, 2),
                             format="%.2f", key=f"_al_px_{ticker}")
        # 클릭 해제 시 None 반환 → 방향 반전 오등록 방지 (_chart_tf 와 동일 가드)
        du = c2.segmented_control("방향", ["📉 이하", "📈 이상"], default="📉 이하",
                                  key=f"_al_dir_{ticker}",
                                  help="이하=현재가가 목표가 아래로(매수 관심) · 이상=위로(목표/청산)"
                                  ) or "📉 이하"
        memo = c3.text_input("메모(선택)", key=f"_al_note_{ticker}",
                             placeholder="예: 지지선 이탈 감시")
        if c4.button("등록", key=f"_al_add_{ticker}", width="stretch"):
            aid = data.add_ticker_alert(ticker, px, "buy" if du == "📉 이하" else "sell",
                                        note=memo or "")
            if aid:
                st.toast(f"🔔 {ticker} {px:,.2f} 알림 등록 — 발동 시 텔레그램 발송")
                st.rerun(scope="fragment")
            else:
                st.warning("알림 등록 실패 — 가격을 확인하세요")
        if alerts:
            st.caption("등록된 알림 — 봇이 5분마다 체크(⚡실시간가 우선)·발동 시 텔레그램")
            for a in alerts:
                r1, r2 = st.columns([5, 0.8], vertical_alignment="center")
                arrow = "📉 이하" if a.get("type") == "buy" else "📈 이상"
                note = f" · {a['note']}" if a.get("note") else ""
                r1.write(f"{arrow} **{a.get('price'):,.2f}**{note} "
                         f"<span style='color:#9198a6;font-size:.75rem'>"
                         f"{str(a.get('created_at', ''))[:16]}</span>",
                         unsafe_allow_html=True)
                if r2.button("삭제", key=f"_al_rm_{a.get('id')}"):
                    data.remove_ticker_alert(a.get("id"))
                    st.rerun(scope="fragment")


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

    # 3열 — 히어로 | 📐 기술적 분석 | ⚖️ 가치평가 (게이지 나란히 — 빈 공간 제거).
    # 매크로 자산은 밸류에이션(PER·RIM·목표가) 개념이 없어 2열(히어로+기술)만.
    _macro = ticker_names.is_macro(ticker)
    if _macro:
        hcol, gcol = st.columns([2.5, 1])
        vcol = None
    else:
        hcol, gcol, vcol = st.columns([1.5, 1, 1])
    with hcol:
        # 통화·단위 — 매크로는 자산별(₩·$/oz·%), 그 외 해외주는 USD (오표기 방지)
        _cur = ticker_names.macro_unit(ticker) if _macro else "USD"
        theme.render(theme.ticker_hero_html(ticker, ticker_names.display_name(ticker, allow_net=False) or ticker,
                                            price, chg, chg_pct, src, _cur))
        # 내 포지션 (보유 시) — 히어로 아래 컴팩트 밴드 (게이지와 높이 균형)
        if pos:
            avg = pos.get("avg_price_usd")
            cur_ret = (price / avg - 1) * 100 if (avg and price) else pos.get("ret", 0)
            cur_val = pos["shares"] * price if price else pos.get("value", 0)
            theme.render(theme.position_band_html([
                ("평단", data.f_usd(avg), None),
                ("평가손익", data.f_pct_s(cur_ret),
                 theme.GREEN if (cur_ret or 0) >= 0 else theme.RED),
                ("보유주수", f"{pos['shares']:g}주", None),
                ("평가액", data.f_usd(cur_val, 0), None),
            ]))
    with gcol:
        if ts:
            theme.render(theme.rating_gauge_html(ts["score"], sub=ts["sub"],
                                                 title="📐 기술적 분석"))
        else:
            st.caption("기술 신호 N/A")
    # 매크로 자산은 밸류에이션(PER·RIM·목표가) 개념이 없어 가치평가 게이지 자체를 생략
    if vcol is not None:
        with vcol:
            vs = None
            try:                                # ETF 는 fundamentals 없음 — 404 스팸 방지
                from providers.etf_data import is_etf as _is_etf
                _skip_val = _is_etf(ticker)
            except Exception:
                _skip_val = False
            if not _skip_val:
                val = cached.valuation(ticker) or {}
                vs = data.valuation_score(price, val.get("metrics"), val.get("consensus"),
                                          cached.intrinsic(ticker))
            if vs:
                theme.render(theme.valuation_gauge_html(vs["score"], sub=vs["sub"]))
            else:
                _kr_nodart = (val.get("metrics") or {}).get("kr_yf_fallback") if not _skip_val else False
                _msg = ("국내주식 — DART 키 설정 시 활성" if _kr_nodart else "재료 부족 — 생략 (ETF 등)")
                st.markdown(f"<div style='color:{theme.MUTED};font-size:.78rem;"
                            f"text-align:center;padding-top:26px'>⚖️ 가치평가<br>"
                            f"<span style='font-size:.72rem'>{_msg}</span></div>",
                            unsafe_allow_html=True)



@st.fragment(run_every=1.5)
def _orderbook_section(ticker, hist, prev):
    """실시간 호가 — 차트 아래 접이식, 1.5초 자동갱신.

    WS 실시간 캐시(1초 스트림) **직독**(st.cache 우회 — 로컬 파일 읽기라 저렴) → REST 폴백.
    조회 종목은 viewer_interest 기록 → kis_stream 이 ~1.5분 내 스트림에 자동 편입(잔여 슬롯).
    """
    from lib import viewer_interest
    from providers.intraday_bars import base_symbol
    sym = base_symbol(ticker)
    viewer_interest.record(sym)                    # 보는 종목 → 실시간 구독 승격
    rq = None
    try:
        from providers import realtime_quotes
        ob = realtime_quotes.get_orderbook(sym, max_age_s=15)
        if ob and (ob.get("bids") or ob.get("asks")):
            rq = {"price": realtime_quotes.get_price(sym),
                  "bids": ob.get("bids") or [], "asks": ob.get("asks") or [],
                  "source": "kis_ws"}
    except Exception:
        rq = None
    if rq is None:
        rq = cached.realtime_quote(ticker)         # REST 폴백 (비구독 종목 — 수 초 지연)
    if not rq or not (rq.get("bids") or rq.get("asks")):
        return                                     # 호가 없음(US/장외) — 섹션 자체 생략
    live = "⚡스트림" if rq.get("source") == "kis_ws" else "REST 지연 — 스트림 편입 중(~1.5분)"
    st.markdown(f"##### 📊 실시간 호가 — 10단계 · {live} · 상승 🔴/하락 🔵")
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


_TRPR_WIN = {"1y": 365, "3y": 365 * 3, "5y": 365 * 5}


@st.fragment
def _etf_tr_pr_section(ticker, peers):
    """📈 수익률·리스크 — TR(배당재투자) vs PR(가격) + 벤치 TR 오버레이 (커버드콜 핵심 뷰)."""
    st.subheader("📈 수익률·리스크 (TR vs PR)")
    d = cached.tr_pr(ticker)
    if not d:
        st.caption("TR·PR 데이터 없음 (yfinance)")
        return
    from providers.etf_compare import mdd_pct, window_return
    win_label = st.segmented_control("구간", list(_TRPR_WIN), default="3y",
                                     label_visibility="collapsed", key="_trpr_win") or "3y"
    days = _TRPR_WIN[win_label]
    tr, pr = d["tr"], d["pr"]
    tr_r, pr_r = window_return(tr, days), window_return(pr, days)
    mdd, mdd_y = mdd_pct(tr, days)
    grp = (peers or {}).get("group") or {}
    bench = grp.get("bench")
    bench_d = cached.tr_pr(bench) if bench and bench != ticker else None
    vs = None
    if bench_d:
        b_r = window_return(bench_d["tr"], days)
        if b_r is not None and tr_r is not None:
            vs = tr_r - b_r
    m = st.columns(5)
    m[0].metric(f"TR {win_label}", f"{tr_r:+.1f}%" if tr_r is not None else "—",
                help="총수익 — 분배금 재투자 가정(조정종가)")
    m[1].metric(f"PR {win_label}", f"{pr_r:+.1f}%" if pr_r is not None else "—",
                help="가격 수익 — 분배금 제외")
    m[2].metric("분배 기여", f"{tr_r - pr_r:+.1f}%p" if None not in (tr_r, pr_r) else "—",
                help="TR − PR — 분배금(재투자)이 만든 수익 몫")
    m[3].metric(f"MDD({mdd_y or '—'}y)", f"-{mdd:.1f}%" if mdd is not None else "—",
                help="TR 기준 최대 낙폭")
    m[4].metric(f"vs {bench or '지수'}", f"{vs:+.1f}%p" if vs is not None else "—",
                help="같은 구간 TR 차이 — 벤치=그룹 대표 ETF TR 프록시")
    compare = {"PR(가격)": pr}
    if bench_d:
        compare[f"{bench} TR"] = bench_d["tr"]
    fig = charts.price_chart(pd.DataFrame({"Close": tr}), "TR(배당재투자)",
                             compare=compare, view_days=days, show_rsi=False)
    st.plotly_chart(fig, width="stretch", config=_NOBAR)
    st.caption("TR=분배금 재투자(조정종가) · PR=가격만 · 벤치=대표 ETF TR 프록시"
               "(TR 지수 원천 불안정) · 세전 · 표시·참고용")


def _fmt_aum(v):
    if not v:
        return "—"
    return f"{v / 1e9:,.1f}B" if v >= 1e9 else f"{v / 1e6:,.0f}M"


def _etf_peer_section(ticker, peers):
    """🏆 동종 ETF 비교·점수 — 게이지 + 컴포넌트 막대 + 피어 지표표 (표시·참고용)."""
    rows = (peers or {}).get("rows") or []
    if not rows:
        return                                     # 그룹 미등록 — 섹션 자연 생략 (정직)
    grp = peers["group"]
    st.subheader(f"🏆 동종 ETF 비교 — {grp['name']}")
    from providers.etf_data import normalize_ticker
    me = normalize_ticker(ticker)
    mine = next((r for r in rows if r["ticker"] == me), None)
    if mine and mine.get("score_detail"):
        sd = mine["score_detail"]
        g1, g2 = st.columns([1, 1.5], vertical_alignment="center")
        with g1:
            theme.render(theme.etf_score_html(mine.get("score"), grp["name"],
                                              sd.get("low_confidence", False)))
        with g2:
            comp = {k: v for k, v in (sd.get("components") or {}).items() if v is not None}
            if comp:
                st.plotly_chart(charts.hbar(list(comp.keys()), list(comp.values()),
                                            "구성 점수 (백분위)", pct=False,
                                            x_range=(0, 105)),
                                width="stretch", config=_NOBAR)
            missing = [k for k, v in (sd.get("components") or {}).items() if v is None]
            if missing:
                st.caption(f"결측 컴포넌트: {'·'.join(missing)} — 가중치 재정규화")
    is_cc = grp.get("strategy") != "index"
    td_col = "전략 갭(기초 대비)" if is_cc else "추적차(3y·%p)"
    table = []
    for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
        er = r.get("expense_ratio")
        table.append({
            "ETF": ("▶ " if r["ticker"] == me else "") + r["ticker"],
            "보수": f"{er * 100:.2f}%" if er is not None else "—",
            "AUM": _fmt_aum(r.get("aum")),
            "1y TR": f"{r['tr_1y']:+.1f}%" if r.get("tr_1y") is not None else "—",
            "3y TR(연)": f"{r['tr_3y_ann']:+.1f}%" if r.get("tr_3y_ann") is not None else "—",
            "MDD": f"-{r['mdd']:.1f}%" if r.get("mdd") is not None else "—",
            td_col: f"{r['tracking_diff']:+.1f}" if r.get("tracking_diff") is not None else "—",
            "분배율": f"{r['div_yield_pct']:.1f}%" if r.get("div_yield_pct") else "—",
            "점수": r.get("score") if r.get("score") is not None else "—",
        })
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
    tail = ("커버드콜 '전략 갭'은 기초지수 프록시 대비 TR 차 — 전략 특성이지 추적오차 아님 · "
            if is_cc else "")
    st.caption(f"점수 1~100 = 동종그룹 내 백분위 가중합(비용·성과·"
               f"{'인컴' if is_cc else '추적'}·리스크·유동성) · {tail}"
               f"벤치={grp['bench']} TR 프록시 · 기준 {peers.get('asof') or '—'} · "
               f"표시·참고용 · 매매신호 아님")


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

    # ── 수익률·리스크 (TR vs PR) + 동종 ETF 비교·점수 ──
    peers = cached.etf_peers(ticker)
    _etf_tr_pr_section(ticker, peers)
    _etf_peer_section(ticker, peers)

    # ── 프로필 (시가총액/운용자산·운용사·NAV·상장일·발행주식수) ──
    st.subheader("ETF 프로필")
    asset_value = etf.get("total_assets") or etf.get("market_cap")
    _grp = (peers or {}).get("group") or {}
    bench_label = etf.get("benchmark") or (
        f"{_grp['name']} (그룹 — 벤치 {_grp['bench']} 프록시)" if _grp else "—")
    rows = [
        ("순자산/AUM", _etf_asset(etf, asset_value)),
        ("운용사", etf.get("family") or "—"),
        ("추종지수", bench_label),
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
        st.markdown("##### 🏭 섹터 비중")
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
    checks = list(summary["checks"])
    try:                                            # 다음 실적일 D-day (12h 캐시)
        ed = cached.next_earnings(ticker)
        if ed:
            from datetime import date as _date
            dday = (ed - _date.today()).days
            if dday >= 0:
                checks.insert(0, f"다음 실적 {ed.strftime('%m/%d')} (D-{dday})")
    except Exception:
        pass
    theme.render(theme.analysis_card_html(summary["verdict"], summary["positives"],
                                          summary["risks"], checks))


def _valuation(ticker, price=None):
    v = cached.valuation(ticker)
    m = v.get("metrics") or {}
    is_kr = m.get("market_type") == "kr"
    if m:
        a = st.columns(5)
        a[0].metric("PER", data.f_ratio(m.get("per")))
        a[1].metric("Fwd PE", data.f_ratio(m.get("forward_pe")))
        _pt = data.peg_textbook(m)
        a[2].metric("PEG", data.f_ratio((_pt or {}).get("peg")),
                    help=("PER ÷ 예상 EPS 증가율(Fwd/TTM 1년) — 교과서 정의 직접 계산. "
                          + (f"성장률 {_pt['growth_pct']:+.0f}% 기준 · 야후 PEG(5y 성장 추정) "
                             f"{data.f_ratio(_pt.get('yahoo'))} 와 다를 수 있음"
                             if _pt else "성장률 ≤0 이거나 EPS 결측이면 — 표시")))
        a[3].metric("PBR", data.f_ratio(m.get("pbr")))
        a[4].metric("PSR", data.f_ratio(m.get("psr")))
        b = st.columns(4)
        b[0].metric("ROE", data.f_frac_pct(m.get("roe")),
                    help="자기자본이익률. 주주자본 대비 이익 창출력이며, PBR 해석과 함께 보는 품질 지표.")
        b[1].metric("배당수익률", data.f_pct(m.get("div_yield"), 2))
        b[2].metric("배당성장 3Y", data.f_frac_pct_s(m.get("div_growth_3y")))
        b[3].metric("EPS(TTM)", _f_krw(m.get("eps_ttm")) if is_kr else data.f_usd(m.get("eps_ttm")))
        if is_kr and not m.get("kr_yf_fallback"):
            c = st.columns(4)
            c[0].metric("시가총액", _f_krw_large(m.get("market_cap")))
            c[1].metric("순이익", _f_krw_large(m.get("net_income")))
            c[2].metric("자본", _f_krw_large(m.get("equity")))
            c[3].metric("BPS", _f_krw(m.get("bps")))
            st.caption(_kr_valuation_caption(m))
        elif m.get("kr_yf_fallback"):
            st.info("🇰🇷 국내주식 정밀 밸류에이션(PER·PBR·ROE·EPS)엔 **DART_API_KEY** 가 필요합니다 "
                    "— 무료 발급(opendart.fss.or.kr) 후 `.env` 에 추가하면 DART 재무제표 기반으로 계산됩니다. "
                    "현재는 yfinance 제한 데이터라 신뢰 불가한 멀티플(Fwd PE·PEG·PSR)은 숨김 처리했습니다.")
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
    # 💰 멀티플 유지 기준가 — Forward EPS × 현재 PER.
    fv = data.fair_value_multiple(price, m.get("per"), m.get("forward_pe"), m.get("eps_fwd"))
    if fv:
        _fmt_px = _f_krw if is_kr else (lambda x: data.f_usd(x, 2))
        fc = st.columns(3)
        fc[0].metric("💰 기준가 (Fwd EPS×PER)", _fmt_px(fv["fair"]),
                     delta=f"{fv['upside_pct']:+.1f}% vs 현재가",
                     help="Forward EPS × 현재 PER. EPS(TTM)×PER은 대체로 현재가를 재계산하므로, "
                          "미래 이익 컨센서스를 현재 멀티플에 대입한 보수적 기준가로 표시합니다.")
        fc[1].metric("Forward EPS", _fmt_px(fv["eps_fwd"]),
                     help="컨센서스 Forward EPS. 없으면 현재가 ÷ fPER 로 내재 EPS를 역산합니다.")
        _fper = fv.get("fper")
        fc[2].metric("PER / fPER", f"{fv['per']:.1f} / {(_fper and f'{_fper:.1f}') or '—'}",
                     help="PER > fPER = 이익 성장 예상 (그 폭이 곧 상방)")
        st.caption("⚠️ Forward EPS·fPER 는 애널리스트 컨센서스 — 리비전에 따라 흔들림 · 멀티플 유지는 가정")

    # 🎯 적정가 인디케이터 — 멀티플 기반 (Fwd EPS × PER + 성장 지표). RIM·DDM 은 보조 참고
    if fv and price:
        st.markdown("##### 🎯 적정가 인디케이터 — 멀티플 기반")
        fair = fv["fair"]
        st.plotly_chart(charts.bullet_bands(
            price, [("Fwd EPS×PER (±15%)", fair * 0.85, fair, fair * 1.15)]),
            width="stretch", config=_NOBAR)
        g = data.eps_growth_fwd(m)
        _pt2 = data.peg_textbook(m)
        mm = st.columns(4)
        mm[0].metric("멀티플 기준가", _fmt_px(fair), delta=f"{fv['upside_pct']:+.1f}%",
                     help="Forward EPS × 현재 PER — 컨센서스 이익에 현 멀티플 유지 가정")
        mm[1].metric("EPS 성장률", f"{g:+.1f}%" if g is not None else "—",
                     help="Fwd EPS ÷ TTM EPS − 1 (1년 예상)")
        mm[2].metric("PEG (계산)", data.f_ratio((_pt2 or {}).get("peg")),
                     help="PER ÷ EPS 증가율 — <1 성장 대비 저평가 해석 관례")
        _tr = (cached.financials(ticker) or {}).get("trends") or {}
        _rchg = _tr.get("roe_chg_3y")
        mm[3].metric("ROE", data.f_frac_pct(m.get("roe")) if m.get("roe") is not None else "—",
                     delta=(f"{_rchg * 100:+.1f}%p (3y)" if _rchg is not None else None),
                     help="자기자본이익률 — 이익의 질·멀티플 정당화 근거. "
                          "증감은 EDGAR 연간 재무 기준 최근 ~3년 변화")
        st.caption("밴드 = 멀티플 ±15% 가정 · Fwd EPS 는 컨센서스(리비전 민감) · 표시·참고용")

    iv = cached.intrinsic(ticker)
    rim, ddm = iv.get("rim"), iv.get("ddm")
    ddm_ok = bool(ddm and (ddm.get("mid") or 0) > 0)
    if rim or ddm_ok:
        st.markdown("###### 참고 — RIM·DDM 모델 (가정 민감·보수적)")
        cc = st.columns(3)
        if rim:
            cc[0].metric("RIM 적정가", data.f_usd(rim["mid"], 0),
                         help=f"범위 {data.f_usd(rim['low'], 0)}~{data.f_usd(rim['high'], 0)}")
        if ddm_ok:
            cc[1].metric("DDM 적정가" + ("" if iv.get("ddm_reliable") else " ⚠️"),
                         data.f_usd(ddm["mid"], 0),
                         help=None if iv.get("ddm_reliable") else "배당성향 낮아 신뢰도 낮음")
        if iv.get("upside_pct") is not None:
            cc[2].metric("RIM 상승여력", data.f_pct_s(iv["upside_pct"]))
        st.caption("RIM=잔여이익·DDM=배당할인(고배당주만) · r 8~11%·g 4% · ROE 영속 가정 — "
                   "성장주에는 보수적이라 멀티플 기준가와 병행 해석")
    h = v.get("history") or []
    if h:
        st.markdown("##### 📈 실적 서프라이즈 이력")
        _surprise_chart(h, "실적 서프라이즈 (최근)", key=f"{ticker}_val")
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
    if m.get("kr_consensus_source") == "naver":
        bits.append(f"포워드(Fwd PE·PEG·목표가) = {m.get('kr_consensus_year') or '차기연도'}E "
                    "네이버 증권사 컨센서스")
    return " · ".join(bits)


def _surprise_chart(history, caption, key=None):
    """서프라이즈 % 부호 막대 (오래된→최근) + 원표.

    key — 같은 페이지에서 두 번 렌더(스냅샷·상세)되므로 plotly_chart 고유 키 필수
    (없으면 StreamlitDuplicateElementId 크래시 — 라이브 실증).
    """
    hh = list(reversed(history))   # 최신순 → 시간순
    labels = [str(x.get("date", ""))[:10] for x in hh]
    vals = [x.get("surprise_pct") for x in hh]
    st.caption(caption)
    if any(x is not None for x in vals):
        st.plotly_chart(charts.signed_bars(labels, [float(x or 0) for x in vals]),
                        width="stretch", config=_NOBAR,
                        key=f"surprise_{key}" if key else None)
    st.dataframe(pd.DataFrame(history), hide_index=True, width="stretch",
                 key=f"surprise_tbl_{key}" if key else None)


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
        st.markdown(f"##### 🧾 내부자거래 (SEC Form 4) — 순매수 {ins.get('net_buy_shares', 0):,.0f}주 "
                    f"(매수 {ins.get('n_buys', 0)}·매도 {ins.get('n_sells', 0)})")
        rows = [{"일자": t["date"], "임원": t["owner"], "직책": t["role"],
                 "구분": {"P": "매수", "S": "매도", "A": "무상", "M": "행사"}.get(t["code"], t["code"]),
                 "수량": f"{t['shares']:,.0f}", "단가": data.f_usd(t["price"]) if t["price"] else "—"}
                for t in txs[:25]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=280)
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
        _surprise_chart(h, f"{ticker} 실적 서프라이즈 이력", key=f"{ticker}_earn")
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
        st.rerun(scope="app")   # fragment 밖(차트 마커·거래 이력)까지 갱신
    except Exception as e:
        st.error(f"실패: {e}")


_ACC_MONTHLY_MULT = {"매일": 21.0, "매주": 4.33, "매월": 1.0}


def _money_krw(x) -> str:
    try:
        return f"₩{float(x):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _entry_level_inputs(ticker, hist, price):
    """진입 레벨 입력 조립 — (supports, resists, fairs) 각 [(라벨, 가격)]. **순수 헬퍼**.

    🎯 섹션과 차트 진입존 오버레이가 공유 (같은 재료·같은 합류존). 재료 부족 시 None.
    (리팩터 시 구 섹션의 @st.fragment 가 여기 붙어있던 것을 제거 — 데이터 함수에
    fragment 가 붙으면 예외가 그레이스풀 계약을 깨고 빈 컨테이너를 삽입한다.)
    """
    if hist is None or getattr(hist, "empty", True):
        return None
    close = hist["Close"].dropna()
    if len(close) < 60 or not price:
        return None
    supports, resists = [], []
    for win in (60, 120, 200):                      # 주요 이동평균 (아래=지지·위=저항)
        if len(close) >= win:
            v = float(close.rolling(win).mean().iloc[-1])
            (supports if v < price else resists).append((f"MA{win}", v))
    if len(close) >= 20:                            # 볼린저 ±2σ
        ma20 = close.rolling(20).mean().iloc[-1]
        sd = close.rolling(20).std().iloc[-1]
        supports.append(("볼린저 하단", float(ma20 - 2 * sd)))
        resists.append(("볼린저 상단", float(ma20 + 2 * sd)))
    yr = close[close.index >= close.index[-1] - pd.Timedelta(days=365)]
    if len(yr) > 20:
        supports.append(("52주 저점", float(yr.min())))
        resists.append(("52주 고점", float(yr.max())))
    try:                                            # 자동 감지 추세 지지/저항선·채널 상하단
        for tl in cached.trendlines_for(ticker, "1d", True, ("long",)) or []:
            if tl.get("kind") == "support":
                supports.append(("추세 지지선", float(tl.get("y1"))))
            elif tl.get("kind") == "resistance":
                resists.append(("추세 저항선", float(tl.get("y1"))))
            elif tl.get("kind") == "channel":
                path = tl.get("path") or {}
                up = (path.get("upper") or [None])[-1] if path else (tl.get("upper") or [None])[-1]
                lo_ = (path.get("lower") or [None])[-1] if path else (tl.get("lower") or [None])[-1]
                if lo_:
                    supports.append(("채널 하단", float(lo_)))
                if up:
                    resists.append(("채널 상단", float(up)))
    except Exception:
        pass
    try:                                            # 매물대 — 거래량 상위 노드(HVN) = 강한 지지/저항
        vp = charts.volume_profile_bins(hist[hist.index >= hist.index[-1]
                                             - pd.Timedelta(days=730)])
        if vp:
            centers, vols = vp
            top = sorted(zip(centers, vols), key=lambda x: -x[1])[:4]
            for c, _vol in top:
                (supports if c < price else resists).append(("매물대(HVN)", float(c)))
    except Exception:
        pass
    try:                                            # 앵커드 VWAP (최근 1년) — 평균 보유단가 근사
        av = charts.anchored_vwap(hist, anchor=hist.index[-1] - pd.Timedelta(days=365))
        if av is not None and len(av):
            v_ = float(av.iloc[-1])
            (supports if v_ < price else resists).append(("앵커드 VWAP(1y)", v_))
    except Exception:
        pass
    try:                                            # 일목 구름 상/하단 (선행스팬 — 현재 시점)
        if {"High", "Low"} <= set(hist.columns) and len(close) >= 78:
            h9 = (hist["High"].rolling(9).max() + hist["Low"].rolling(9).min()) / 2
            h26 = (hist["High"].rolling(26).max() + hist["Low"].rolling(26).min()) / 2
            spa = float(((h9 + h26) / 2).shift(26).iloc[-1])
            spb = float(((hist["High"].rolling(52).max()
                          + hist["Low"].rolling(52).min()) / 2).shift(26).iloc[-1])
            c_lo, c_hi = min(spa, spb), max(spa, spb)
            (supports if c_lo < price else resists).append(("일목 구름 하단", c_lo))
            (supports if c_hi < price else resists).append(("일목 구름 상단", c_hi))
    except Exception:
        pass
    fairs = []
    v = cached.valuation(ticker) or {}
    m = v.get("metrics") or {}
    fv = data.fair_value_multiple(price, m.get("per"), m.get("forward_pe"),
                                  m.get("eps_fwd"))
    if fv and fv.get("fair"):
        fairs.append(("멀티플 기준가", fv["fair"]))
    iv = cached.intrinsic(ticker) or {}
    if (iv.get("rim") or {}).get("mid"):
        fairs.append(("RIM 적정가", iv["rim"]["mid"]))
    tgt = (v.get("consensus") or {}).get("target_median")
    if tgt:
        fairs.append(("목표가 중앙값", tgt))
    return supports, resists, fairs


def _chart_entry_zones(ticker, hist, price) -> list[dict]:
    """차트 진입존 오버레이 데이터 — 🎯 섹션과 동일 합류존 상위 3개. graceful []."""
    try:
        inp = _entry_level_inputs(ticker, hist, price)
        if not inp:
            return []
        lv = data.entry_levels(price, *inp) or {}
        out = []
        for i, z in enumerate((lv.get("zones") or [])[:3]):
            n = z.get("n", 1)
            out.append({"lo": z.get("lo"), "hi": z.get("hi"),
                        "label": f"🎯 {i + 1}차 존" + (f" ×{n}" if n > 1 else "")})
        return out
    except Exception:
        return []


@st.fragment
def _entry_levels_section(ticker, hist, price):
    """🎯 진입 레벨 가이드 — 추세 지지/저항·MA·볼린저·52주(기술) × 기준가·RIM·목표가(밸류).

    레벨 **후보 서술** — 예측·매매신호 아님 (실행 규칙은 Phase DCA·수동).
    fragment — 🔔 도달 알림 버튼 클릭이 페이지 전체가 아닌 섹션만 rerun.
    """
    inp = _entry_level_inputs(ticker, hist, price)
    if not inp:
        return
    supports, resists, fairs = inp

    lv = data.entry_levels(price, supports, resists, fairs)
    if not lv:
        return
    st.markdown("##### 🎯 진입 레벨 가이드 — 밸류 × 기술")
    mm = st.columns(4)
    zones = lv.get("zones") or []
    for i in range(3):
        if i < len(zones):
            z = zones[i]
            strong = f" ×{z['n']}" if z.get("n", 1) > 1 else ""
            val_txt = (f"{z['lo']:,.2f}~{z['hi']:,.2f}" if z["hi"] > z["lo"] * 1.0005
                       else f"{z['mid']:,.2f}")
            mm[i].metric(f"{i + 1}차 지지 존{strong}", val_txt,
                         delta=f"{z['pct']:+.1f}%", delta_color="off",
                         help=f"재료: {' + '.join(z['labels'])} — 겹칠수록(×n) 신뢰↑ · "
                              "분할 접근 참고용")
            if mm[i].button("🔔 도달 알림", key=f"_lvl_alert_{ticker}_{i}",
                            help="이 존 상단 도달 시 텔레그램 알림 (봇 /alert 공용)"):
                try:
                    from bot import price_alerts
                    price_alerts.add_alert(ticker, round(z["hi"], 2), "buy",
                                           note=f"진입 존{i + 1} ({'+'.join(z['labels'][:2])})")
                    st.toast(f"🔔 {ticker} {z['hi']:,.2f} 하락 도달 알림 등록")
                except Exception as e:
                    st.toast(f"알림 등록 실패: {e}")
        else:
            mm[i].metric(f"{i + 1}차 지지 존", "—")
    gap = lv.get("fair_gap_pct")
    mm[3].metric("밸류 기준가 평균 대비", f"{gap:+.1f}%" if gap is not None else "—",
                 help="멀티플 기준가·RIM·목표가 평균이 현재가보다 위(+)면 밸류 여유")
    ents = lv.get("entries") or []
    levels = ([("기술 지지", val, "support") for _, val, _ in ents]
              + [("기술 저항", val, "resist") for _, val, _ in (lv.get("resists") or [])]
              + [("밸류 기준", val, "fair") for _, val, _ in (lv.get("fairs") or [])])
    zone_bands = [("기술 지지", z["lo"], z["hi"]) for z in zones if z["hi"] > z["lo"]]
    if levels:
        st.plotly_chart(charts.price_levels(lv["price"], levels, zones=zone_bands),
                        width="stretch", config=_NOBAR)
    detail = " · ".join(f"{lab} {val:,.0f}({pct:+.1f}%)"
                        for lab, val, pct in (lv.get("fairs") or []))
    st.caption(("밸류 기준: " + detail + " · " if detail else "")
               + "레벨은 **후보 서술** — 예측·매매신호 아님 · 지지 이탈 가능 · "
                 "실행 규칙은 Phase DCA(자동 아님·수동)")


def _trade_history(ticker):
    """🧾 거래 이력 — 원장 최신순 · 행 선택 = 그 기록만 취소 (임의 시점·기록 전용)."""
    from lib import trade_events as te
    st.markdown("##### 🧾 거래 이력")
    trades = data.trade_events(ticker)
    ordered = list(reversed(trades))                   # 표시 순서(최신 위) = 선택 인덱스
    sel_ev = None
    if trades:
        rows = [{
            "일자": t.get("date"),
            "구분": "🟢 매수" if t.get("side") == "buy" else "🔴 매도",
            "수량": t.get("qty"),
            "체결가": t.get("price"),
            "평단": t.get("avg_price"),
            "출처": "수동" if t.get("source") == "manual_holding" else (t.get("source") or "—"),
            "메모": t.get("note") or "",
        } for t in ordered]
        event = st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                             height=min(302, 44 + 35 * len(rows)),
                             on_select="rerun", selection_mode="single-row",
                             key=f"_th_{ticker}",
                             column_config={
                                 "수량": st.column_config.NumberColumn(format="%.4f"),
                                 "체결가": st.column_config.NumberColumn(format="%.2f"),
                                 "평단": st.column_config.NumberColumn(format="%.2f"),
                                 "메모": st.column_config.TextColumn(width="medium"),
                             })
        try:
            sel = event.selection.rows
        except Exception:
            sel = []
        if sel and sel[0] < len(ordered):
            sel_ev = ordered[sel[0]]
    else:
        st.caption("기록 없음 — 좌측 적립/추가/축소 기록이 여기와 차트 ▲▼ 마커에 반영됩니다.")

    # 취소 대상: 선택 행 우선 · 미선택 시 최근 수동 기록 (임의 시점 취소는 rollback+replay)
    target = sel_ev if sel_ev is not None else te.latest_manual_event(ticker)
    if sel_ev is not None and (str(sel_ev.get("source") or "") != "manual_holding"
                               or str(sel_ev.get("account") or "") not in
                               ("overseas_general", "overseas_fractional")):
        st.caption("⚠️ 선택한 기록은 동기화/모의 기록 — 취소 불가 (수동 기록만 가능)")
    elif target:
        side_kr = "매수" if target.get("side") == "buy" else "매도"
        which = "선택 기록" if sel_ev is not None else "최근 기록"
        ok = st.checkbox("확인 — 포트폴리오 스냅샷 즉시 수정", key=f"_undo_ok_{ticker}")
        if st.button(f"↩️ {which} 취소 — {target.get('date')} {side_kr} "
                     f"{float(target.get('qty') or 0):g}주",
                     key=f"_undo_{ticker}", disabled=not ok, width="stretch"):
            _apply_action(lambda: _hm().undo_trade(target["event_id"]))
    elif trades:
        st.caption("취소할 수동 기록 없음 (동기화·모의 기록은 취소 불가)")
    st.caption("행 선택 = 그 기록만 취소(중간 기록도 가능 — 이후 기록은 자동 재계산·"
               "모순이면 정직 거부) · 평단 검증이 이중 실행 차단 · 실계좌 주문 없음")


@st.fragment
def _manage_position(ticker, cur_price, pos):
    cur = float(cur_price or 0.0)
    held = pos["shares"] if pos else 0.0
    st.divider()
    st.markdown("##### ⚙️ 내 포지션 관리")
    top = st.columns([1.15, 1, 1, 1])
    top[0].metric("현재가", data.f_usd(cur))
    top[1].metric("보유주수", f"{held:g}주" if held else "미보유")
    top[2].metric("평단", data.f_usd(pos.get("avg_price_usd")) if pos else "—")
    top[3].metric("평가액", data.f_usd(pos.get("value"), 0) if pos else "—")

    left, right = st.columns([1.25, 1], gap="large")
    with left:
        mode = st.segmented_control("작업", ["💧 적립", "➕ 추가", "➖ 축소"], default="💧 적립",
                                    key="mng_mode_v2", label_visibility="collapsed") or "💧 적립"
        if mode == "💧 적립":
            c1, c2, c3 = st.columns([1, 1, 1])
            currency = c1.segmented_control("입력 통화", ["₩ 원화", "$ 달러"], default="₩ 원화",
                                            key="acc_currency")
            freq = c2.segmented_control("주기", ["매일", "매주", "매월"], default="매주",
                                        key="acc_freq")
            _fx_live = cached.fx_now() or 1380.0        # 실시간 환율 자동 (2분 캐시)
            fx = _fx_live
            if currency == "₩ 원화":
                amt = c3.number_input("적립 금액 (₩)", min_value=1000.0, value=100_000.0,
                                      step=1000.0, format="%.0f", key="acc_amt_krw",
                                      help="키움 주식모으기 최소/단위 금액 = 1,000원")
                fx = st.number_input("적용 환율 (₩/$) — 실시간 자동", min_value=500.0,
                                     max_value=2500.0, value=round(float(_fx_live), 1),
                                     step=1.0, format="%.1f", key="acc_fx",
                                     help="실시간 USD/KRW 자동 채움 (2분 캐시·직접 수정 가능) — "
                                          "원화 예산을 USD 매수금액으로 환산")
                _ft = cached.fx_timing()
                if _ft.get("ok"):
                    st.caption(f"💱 환전 타이밍: {_ft.get('emoji', '')} {_ft.get('verdict', '')} · "
                               f"5y 위치 {_ft.get('pct_display', '—')}%ile · "
                               f"분할 환전 배율 {_ft.get('multiplier', 1):g}× — {_ft.get('action', '')}")
                amount_usd = amt / fx if fx > 0 else 0.0
                amount_label = _money_krw(amt)
            else:
                amt = c3.number_input("적립 금액 ($)", min_value=0.0, value=100.0,
                                      step=10.0, key="acc_amt_usd")
                amount_usd = amt
                amount_label = data.f_usd(amt)
            qty = (amount_usd / cur) if cur > 0 else 0.0
            monthly_usd = amount_usd * _ACC_MONTHLY_MULT.get(freq, 1.0)
            p = st.columns(4)
            p[0].metric(f"{freq} 금액", amount_label)
            p[1].metric("환산 USD", data.f_usd(amount_usd))
            p[2].metric("예상 수량", f"{qty:.4f}주")
            p[3].metric("월 환산", data.f_usd(monthly_usd, 0))
            st.caption(f"현재가 기준 1회 적립 수량 · {freq} 주기 메모 · 평단 자동 재계산")
            note = f"DCA {freq} {amount_label} ({amount_usd:.2f} USD"
            if currency == "₩ 원화":
                note += f", fx {fx:.1f}"
            note += ")"
            b1, b2 = st.columns(2)
            if b1.button(f"💧 {freq} 적립 1회 기록", key="acc_btn", type="primary",
                         disabled=(amount_usd <= 0 or qty <= 0 or cur <= 0), width="stretch"):
                _apply_action(lambda: _hm().buy_holding(
                    ticker, round(qty, 4), round(cur, 4), note=note))
            # 🔁 자동 모으기 — 등록해두면 크론이 매 세션 미 종가·확정 종가 환율로 자동 기록
            from lib import accumulation
            _plan = accumulation.plan_for(ticker)
            _amt_raw = amt
            _cur_code = "KRW" if currency == "₩ 원화" else "USD"
            if b2.button(f"🔁 자동 기록 등록 — {freq} 종가", key="acc_auto_btn",
                         disabled=(_amt_raw <= 0), width="stretch",
                         help="등록하면 매 미국 세션 마감 후 그날 종가·확정 종가 환율로 "
                              "자동 기록 (실주문 아님 — 키움 주식모으기 결과를 거울처럼 반영)"):
                st.success(accumulation.upsert_plan(ticker, _amt_raw, _cur_code, freq))
                st.rerun(scope="app")
            if _plan:
                _pa = (f"₩{_plan['amount']:,.0f}" if _plan.get("currency") == "KRW"
                       else f"${_plan['amount']:,.2f}")
                pc1, pc2 = st.columns([2.2, 1])
                pc1.caption(f"🔁 자동 모으기 활성: {_plan.get('freq')} {_pa} · "
                            f"마지막 기록 {_plan.get('last_run') or '아직 없음'} · "
                            f"{'ON' if _plan.get('enabled', True) else 'OFF'}")
                if pc2.button("해제", key="acc_auto_del", width="stretch"):
                    accumulation.remove_plan(ticker)
                    st.rerun(scope="app")
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
    with right:
        _trade_history(ticker)
