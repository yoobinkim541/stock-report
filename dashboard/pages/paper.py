"""모의투자 — 자동 페이퍼트레이딩 계좌 현황 + 판단 근거 원장 (P-series).

국내(키움 모의)·미국(KIS 해외 모의) 자동 모의투자를 한 화면에: NAV·누적 vs 지수·MDD·보유·
거래비용 + **편입/퇴출 판단 근거**(append-only 원장 ⋈ 실현 결과) + 로직 평가(정직 verdict).
표시 전용(read-only) — 주문 집행 0 · 크론 리포트(kiwoom_mock_report·us_mock_report)와 동일 데이터원.
시장 전환·필터는 @st.fragment 라 섹션만 부분 rerun.
"""
from __future__ import annotations

import os
import re
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_names
from dashboard import cached, charts, data, ui_actions

_NOBAR = {"displayModeBar": False}
_SURFACES = ["kr_mock", "us_mock"]
_SURF_LABEL = {"kr_mock": "🇰🇷 국내 (키움 모의)", "us_mock": "🇺🇸 미국 (KIS 해외 모의)"}
_SIDE_ICON = {"편입": "📥", "증액": "➕", "퇴출": "📤", "감액": "➖"}
_EXIT_LABEL = {
    "stop": "손절",
    "target": "목표",
    "timestop": "시간 청산",
    "signal_collapse": "신호 붕괴",
    "eod_flat": "장마감 청산",
    "orphan_repair": "고아 포지션 정리",
    "partial_target": "부분 목표",
}
_STATE_LABEL = {"strong": "강함", "weak": "약함", "broken": "깨짐", "inactive": "꺼짐"}
_REGIME_LABEL = {"risk_on": "위험선호", "risk_off": "위험회피", "neutral": "중립"}
_AXIS_LABEL = {
    "orb": "시가범위", "vwap": "VWAP", "volspike": "거래량", "ofi": "주문흐름",
    "news": "뉴스", "ema": "이평", "rsi": "RSI", "bb": "볼린저",
}
_AXIS_COL = {"mom12": "12개월", "hi52": "52주고점", "lowvol": "저변동", "pead": "실적", "news": "뉴스"}
_REASON_TOKEN = {
    "mom12": "12개월", "hi52": "52주고점", "lowvol": "저변동", "pead": "실적",
    "value": "밸류", "quality": "퀄리티", "mom": "모멘텀", "news": "뉴스",
    "ranker": "랭커", "conf": "신뢰", "fund": "재무", "signal": "신호",
}


def _reason_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    text = text.replace("fail-closed", "안전 차단").replace("stale", "오래됨")
    for code, label in sorted(_REASON_TOKEN.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(code)}(?![A-Za-z0-9_])", label, text)
    return text




def _label(table: dict[str, str], value, fallback: str = "—") -> str:
    raw = str(value or "").strip()
    if not raw or raw == "—":
        return fallback
    return table.get(raw, raw)


def _money(x, cur: str) -> str:
    f = data._try_float(x)
    if f is None:
        return "—"
    return f"{cur}{f:,.0f}" if cur == "₩" else f"{cur}{f:,.2f}"



def _signed_money(x, cur: str) -> str:
    f = data._try_float(x)
    if f is None:
        return "—"
    text = _money(abs(f), cur)
    if f < 0:
        return f"-{text}"
    if f > 0:
        return f"+{text}"
    return text


def _analysis_symbol(raw) -> str:
    """모의 잔고 코드(005930·A005930)를 종목 분석 티커로 맞춘다."""
    text = str(raw or "").strip()
    return ticker_names.normalize_input(text) or text


def _order_label(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return "—"
    symbol = _analysis_symbol(text)
    return ticker_names.label(symbol, maxlen=18) if symbol else text



def render():
    st.title("자동 모의투자")
    st.caption("신호 기반 자동 모의매매 — 모의 도메인 하드락 · 표시 전용 · **실거래 아님**")
    mk = st.segmented_control("시장", _SURFACES, default="kr_mock", key="paper_market",
                              format_func=lambda s: _SURF_LABEL.get(s, s),
                              label_visibility="collapsed") or "kr_mock"
    _account_section(mk)
    _intraday_section(mk)
    _decisions_section(mk)


@st.fragment
def _account_section(surface: str):
    d = cached.paper(surface)
    cur = d.get("currency", "₩")
    bench = d.get("bench_name", "지수")

    problem = d.get("balance_problem")
    if d.get("nav") is None:
        if problem:
            st.warning(f"⚠️ 잔고 조회 실패({problem}) — 표시할 유효 NAV 기록이 없습니다 "
                       "(세대 마감 직후이거나 모의투자 신청·연결 확인 필요).")
        else:
            st.info("계좌 데이터 없음 — 모의투자 크론(kiwoom_mock_track·us_mock_track)이 아직 "
                    "실행되지 않았거나 `*_MOCK_ENABLED` 미설정입니다.")
        return
    if not d.get("balance_ok"):
        st.caption(f"⚠️ 잔고 API 조회 불가{f'({problem})' if problem else ''} — 마지막 유효 EOD 스냅샷 기준 표시")

    # ── 계좌 KPI ──────────────────────────────────────────────────────────────
    excess = None
    if d.get("cum_ret") is not None and d.get("bench_ret") is not None:
        excess = d["cum_ret"] - d["bench_ret"]
    life_excess = None
    if d.get("lifetime_cum_ret") is not None and d.get("lifetime_bench_ret") is not None:
        life_excess = d["lifetime_cum_ret"] - d["lifetime_bench_ret"]
    m = st.columns(4)
    m[0].metric("순자산", _money(d["nav"], cur),
                delta=(f"{d['day_ret']:+.2f}% 전일" if d.get("day_ret") is not None else None))
    m[1].metric("세대 누적 수익률", data.f_pct_s(d.get("cum_ret"), 2),
                help=f"세대 {d.get('generation') or '—'} · 인셉션 {d.get('inception_date') or '—'} 이후")
    m[2].metric("전체 누적 수익률", data.f_pct_s(d.get("lifetime_cum_ret"), 2),
                help=f"최초 {d.get('lifetime_inception_date') or d.get('inception_date') or '—'} 이후")
    m[3].metric(f"세대 vs {bench}", (f"{excess:+.2f}%p" if excess is not None else "—"),
                delta=(f"{bench} {d['bench_ret']:+.2f}%" if d.get("bench_ret") is not None else None),
                delta_color="off", help="현재 세대 기준 지수 아웃퍼폼")

    m2 = st.columns(3)
    m2[0].metric(f"전체 vs {bench}", (f"{life_excess:+.2f}%p" if life_excess is not None else "—"),
                 delta=(f"{bench} {d['lifetime_bench_ret']:+.2f}%"
                        if d.get("lifetime_bench_ret") is not None else None),
                 delta_color="off", help="세대 경계를 이어붙인 전체 성과")
    mdd_ok = (d.get("strat_mdd") is not None and d.get("bench_mdd") is not None
              and d["strat_mdd"] <= d["bench_mdd"])
    m2[1].metric("MDD (세대)", data.f_pct(d.get("strat_mdd")),
                 delta=(f"지수 {data.f_pct(d.get('bench_mdd'))} {'✅' if mdd_ok else '⚠️'}"
                        if d.get("bench_mdd") is not None else None),
                 delta_color="off", help="현재 세대 최대낙폭")
    life_mdd_ok = (d.get("lifetime_strat_mdd") is not None and d.get("lifetime_bench_mdd") is not None
                   and d["lifetime_strat_mdd"] <= d["lifetime_bench_mdd"])
    m2[2].metric("MDD (전체)", data.f_pct(d.get("lifetime_strat_mdd")),
                 delta=(f"지수 {data.f_pct(d.get('lifetime_bench_mdd'))} {'✅' if life_mdd_ok else '⚠️'}"
                        if d.get("lifetime_bench_mdd") is not None else None),
                 delta_color="off", help="리셋 구간을 이어붙인 전체 최대낙폭")

    # ── NAV 곡선 ─────────────────────────────────────────────────────────────
    series = d.get("nav_series") or []
    # 인셉션 기록과 당일 스냅샷이 같은 날짜면 x 축이 밀리초 눈금으로 찢어진다 — 날짜가 2개 이상일 때만 그린다
    if len({str(p.get("date")) for p in series if p.get("nav") is not None}) >= 2:
        st.plotly_chart(charts.nav_curve(series, cur), width="stretch", config=_NOBAR)
    else:
        st.info("순자산 시계열이 아직 쌓이는 중입니다. 평일 마감 스냅샷마다 누적됩니다.")

    # ── 보유 + 현금 ───────────────────────────────────────────────────────────
    pos = d.get("positions") or []
    left, right = st.columns([3, 1])
    with left:
        st.markdown(f"##### 보유 {len(pos)}종목")
        if pos:
            event = st.dataframe(pd.DataFrame([{
                "종목": p["name"], "수량": p["shares"],
                "평단": _money(p["avg"], cur),
                "현재가": _money(p["cur"], cur),
                "평가액": _money(p["value"], cur), "수익률": data.f_pct_s(p.get("ret"), 2),
            } for p in pos]), hide_index=True, width="stretch",
                on_select="rerun", selection_mode="single-row", key=f"_paper_pos_{surface}")
            try:
                sel = event.selection.rows
            except Exception:
                sel = []
            if sel and sel[0] < len(pos):
                symbol = _analysis_symbol(pos[sel[0]].get("symbol"))
                if symbol:
                    ui_actions.open_ticker(
                        symbol,
                        toast=f"종목 분석 → {pos[sel[0]].get('name') or symbol}",
                    )
            st.caption("행을 누르면 종목 분석으로 이동합니다")
        elif d.get("balance_ok"):
            st.caption("보유 없음 — 현금 100%")
        else:
            st.caption("보유 내역을 불러오지 못했습니다.")
    with right:
        st.markdown("##### 현금")
        st.metric("예수금", _money(d.get("cash"), cur), label_visibility="collapsed")
        c = d.get("cost")
        if c:
            st.caption(f"💸 누적 거래비용 {_money(c['total'], cur)}\n\n"
                       f"회전율 {c['turnover']:.0f}% · 드래그 −{c['drag']:.2f}%p")

    orders = d.get("order_history") or []
    if orders:
        st.markdown("##### 최근 주문 내역")
        st.dataframe(pd.DataFrame([{
            "시각": r.get("date"),
            "종목": _order_label(r.get("code") or r.get("symbol") or r.get("ticker")),
            "방향": "매수" if r.get("side") == "buy" else ("매도" if r.get("side") == "sell" else r.get("side")),
            "수량": r.get("qty"),
            "사유": _reason_text(r.get("reason")),
            "상태": "✅ 성공" if r.get("ok") is True else ("❌ 실패" if r.get("ok") is False else "—"),
            "메시지": _reason_text(r.get("msg")) if r.get("msg") else "—",
        } for r in orders[:30]]), hide_index=True, width="stretch",
            column_config={"메시지": st.column_config.TextColumn(width="large")})

    # ── 🏗️ 구조레버 슬리브 (US — Tier3 게이트 GO 시 모의 라이브 검증) ──────────
    sl = d.get("sleeve")
    if sl:
        if sl.get("reco"):
            state = f"게이트 **통과 ×{sl['reco']:.2f}** → 목표 {(sl['reco'] - 1) * 100:.0f}%"
        elif sl.get("enabled"):
            state = "게이트 미통과/오래됨 → 목표 0% (청산 방향)"
        else:
            state = "슬리브 꺼짐 (보유 잔량만)"
        st.markdown(f"🏗️ **구조레버 슬리브** — {state} · 보유 {sl['symbol']} "
                    f"{sl['shares']}주 ({sl['frac']:.0f}%)")

    # ── 로직 평가 (정직 verdict — evolution 단일 소스) ─────────────────────────
    st.markdown("##### 📊 로직 평가")
    ev = cached.learning_evolution(surface)
    v, snap = ev.get("verdict") or {}, ev.get("snapshot") or {}
    if v:
        st.markdown(f"**{v.get('emoji', '')} {v.get('label', '')}** — {v.get('note', '')}")
    sc = d.get("scorecard") or {}
    k = st.columns(4)
    k[0].metric("편입 적중률", data.f_pct(sc.get("buy_hit")), help=f"판정 n={sc.get('n_buy', 0)}")
    k[1].metric("퇴출 적중률", data.f_pct(sc.get("sell_hit")), help=f"판정 n={sc.get('n_sell', 0)}")
    k[2].metric("순비용 IC", data.f_ratio(snap.get("realized_ic"), 3),
                help="정책점수↔실현 순초과수익 상관 (비용 차감)")
    k[3].metric("누적 엣지", data.f_frac_pct_s(snap.get("cum_net_excess")),
                help="매수 결정 평균 순초과수익")
    st.caption("※ 무엣지면 적중률 ~50%·IC ≈0 으로 그대로 표시(정직) · 상세 학습곡선은 리서치 → 정책 학습")


def _selected_intraday_trade(event, trades):
    """plotly 마커 클릭 → customdata[0]=event_id → 트레이드 레코드 (ticker.py 패턴)."""
    try:
        points = event.selection.points
    except Exception:
        try:
            points = (event or {}).get("selection", {}).get("points", [])
        except Exception:
            points = []
    if not points:
        return None
    point = points[0]
    custom = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
    event_id = custom[0] if custom else None
    return next((t for t in trades if t.get("event_id") == event_id), None)


@st.fragment
def _intraday_section(surface: str):
    """🕐 단기(1분봉) 트레이딩 — 분봉 캔들 + ▲▼ 체결 마커 + 판단근거. 미사용 시 숨김."""
    mkt = "KR" if surface == "kr_mock" else "US"
    ov = cached.intraday_overview(mkt)
    s, dates = ov.get("summary"), ov.get("dates") or []
    if not s and not dates:
        return                                  # 단기 서브시스템 미가동 — 섹션 자체 숨김
    st.markdown("##### 🕐 단기 트레이딩 (1분봉)")
    if s:
        cur = s["currency"]
        badge = "🌑 가상체결" if s["shadow"] else "🧪 모의 집행"
        k = st.columns(4)
        k[0].metric("오늘 트레이드", f"{s['today_n']}건",
                    delta=(f"승 {s['wins']}" if s["today_n"] else None), delta_color="off")
        k[1].metric("오늘 실현", _money(s["net_today"], cur))
        k[2].metric("누적 (슬리브)", _money(s["cum"], cur))
        k[3].metric("상태", "🛑 일손실 정지" if s["halt"] else badge)
    day_default = dates[-1] if dates else None
    c1, c2, c3 = st.columns([2, 1, 1])
    date = c1.selectbox("날짜", list(reversed(dates)) or [day_default],
                        key=f"intr_date_{surface}") if dates else day_default
    if not date:
        st.caption("분봉이 아직 없습니다.")
        return
    day = cached.intraday_day(mkt, date)
    syms = day.get("symbols") or []
    if not syms:
        st.caption("그날 트레이드·분봉 심볼 없음")
        return
    sym = c2.selectbox("종목", syms, key=f"intr_sym_{surface}",
                       format_func=lambda t: data.name_label(t) if hasattr(data, "name_label") else t)
    interval = c3.segmented_control(
        "봉", ["1m", "5m"], default="1m", key=f"intr_iv_{surface}",
        format_func=lambda value: {"1m": "1분", "5m": "5분"}.get(value, value),
    ) or "1m"

    ch = cached.intraday_chart(sym, mkt, date, interval)
    bars = ch.get("bars")
    if bars is None or getattr(bars, "empty", True):
        st.caption("이 날짜의 분봉이 없습니다.")
        return
    cur = (s or {}).get("currency") or ("₩" if surface == "kr_mock" else "$")
    # 그날 그 심볼 결정의 스톱/목표선 (최근 결정 기준)
    rows_sym = [r for r in (day.get("rows") or []) if r.get("ticker") == sym]
    levels = []
    if rows_sym:
        last = rows_sym[-1]
        levels = [{"y": last.get("stop"), "label": "스톱", "color": "#ef4444"},
                  {"y": last.get("target"), "label": "목표", "color": "#22c55e"}]
    fig = charts.intraday_candle(bars, sym, trades=ch.get("trades"),
                                 vwap=ch.get("vwap"), or_range=ch.get("or_range"),
                                 levels=levels, smooth=(interval == "5m"))
    event = None
    try:
        event = st.plotly_chart(fig, width="stretch", config=charts.PAN_CFG,
                                key=f"intr_chart_{surface}_{sym}_{date}_{interval}",
                                on_select="rerun", selection_mode="points")
    except TypeError:
        st.plotly_chart(fig, width="stretch", config=charts.PAN_CFG)
    src_note = {"store": "자체 1분봉(실시간 수집)", "yfinance": "시세 폴백(지연)"}.get(ch.get("src"), "")
    bar_count = ch.get("bar_count")
    bar_span = ""
    if ch.get("bar_first") and ch.get("bar_last"):
        bar_span = (f" · {str(ch.get('bar_first'))[:16].replace('T', ' ')}"
                    f"~{str(ch.get('bar_last'))[:16].replace('T', ' ')}")
    sess = ch.get("session_count")
    sess_note = f" · {sess}세션" if sess else ""
    st.caption(f"▲매수 ▼매도 마커 클릭 → 판단근거 · VWAP 점선 · 파란 박스=시가범위(OR 15분) · "
               f"{src_note}{f' · {bar_count}봉' if bar_count else ''}{sess_note}{bar_span}")
    st.caption(charts.PAN_HINT)

    sel = _selected_intraday_trade(event, ch.get("trades") or [])
    if sel:
        eid = str(sel.get("event_id") or "")
        did = eid[5:-3] if eid.startswith("intr-") else None    # intr-{decision_id}-in|out
        rec = next((r for r in rows_sym if r.get("id") == did), None)
        side_lbl = "매수(진입)" if sel.get("side") == "buy" else "매도(청산)"
        cc = st.columns(4)
        cc[0].metric("구분", side_lbl)
        cc[1].metric("수량·가격", f"{sel.get('qty')}주 @ {_money(sel.get('price'), cur)}")
        cc[2].metric("점수", data.f_ratio((rec or {}).get("score"), 2))
        result = (
            f"{_label(_EXIT_LABEL, rec.get('exit_reason'))} R{rec.get('realized_r'):+.2f}"
            if rec and rec.get("realized_r") is not None else "⏳/—"
        )
        cc[3].metric("결과", result)
        if rec and rec.get("features"):
            axes = {k: v for k, v in rec["features"].items()
                    if k in ("orb", "vwap", "volspike", "ofi", "news", "ema", "rsi", "bb")}
            st.caption("판단 축 점수: " + " · ".join(
                f"{_label(_AXIS_LABEL, k)} {data.f_ratio(v, 2)}" for k, v in axes.items() if v is not None))

    # 그날 트레이드 원장표 (결정⋈결과)
    rows = day.get("rows") or []
    if rows:
        st.dataframe(pd.DataFrame([{
            "시각": str(r.get("id", ""))[-6:], "종목": r.get("ticker"),
            "수량": r.get("qty"), "진입": _money(r.get("price"), cur),
            "점수": data.f_ratio(r.get("score"), 2),
            "청산": _label(_EXIT_LABEL, r.get("exit_reason"), "⏳오픈"),
            "순R": (f"{r['realized_r']:+.2f}" if r.get("realized_r") is not None else "—"),
            "손익": _signed_money(r.get("net_pnl"), cur) if r.get("net_pnl") is not None else "—",
            "모드": "가상" if r.get("shadow") else "모의",
        } for r in rows]), hide_index=True, width="stretch")
    st.caption("⚠️ 단기 슬리브 — 가상체결이 기본입니다. 트레이드 100건 이상·순R>0·검증 관문을 통과한 뒤에만 "
               "모의 집행으로 올라갑니다. 실거래 아님")


@st.fragment
def _decisions_section(surface: str):
    st.markdown("##### 🧾 판단 근거 (편입/퇴출 결정 원장)")
    st.caption("지우지 않는 원장 — 결정 시점의 근거와, 시간이 지난 뒤 채운 실현 결과")
    d = cached.paper(surface)
    rows = d.get("decisions") or []
    if not rows:
        st.info("편입·퇴출 기록이 아직 없습니다.")
        return
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    sides = fc1.multiselect("구분", ["편입", "퇴출", "증액", "감액", "레버슬리브"],
                            default=["편입", "퇴출"], key=f"paper_sides_{surface}")
    n = fc2.slider("표시 건수", 20, 200, 50, 10, key=f"paper_n_{surface}")
    show_axes = fc3.toggle("축 피처 보기", key=f"paper_axes_{surface}",
                           help="★새 수집 축(12개월·52주고점·저변동·실적·뉴스) — 원장 축적 현황")
    view = [r for r in rows if not sides or r.get("side") in sides][:n]
    if not view:
        st.caption("선택한 구분의 결정 없음")
        return

    def _row(r):
        overlay_active = bool(r.get("overlay_active"))
        mult = r.get("momentum_multiplier")
        base = {
            "날짜": r["date"],
            "구분": f"{_SIDE_ICON.get(r.get('side'), '')} {r.get('side', '')}",
            "종목": r.get("name") or r.get("ticker"),
            "수량": r.get("qty"),
            "정책점수": data.f_ratio(r.get("policy_score"), 3),
            "선택점수": data.f_ratio(r.get("selection_score"), 3),
            "오버레이": "켜짐" if overlay_active else "꺼짐",
            "상태": _label(_STATE_LABEL, r.get("momentum_state") or "inactive"),
            "모멘텀점수": data.f_ratio(r.get("momentum_score"), 3) if overlay_active else "—",
            "배수": f"×{float(mult):.2f}" if overlay_active and mult is not None else "—",
            "틸트": data.f_ratio(r.get("momentum_tilt"), 3),
            "레짐": _label(_REGIME_LABEL, r.get("regime")),
            "판단 근거": _reason_text(r.get("reason")),
            "체결": "✅" if r.get("ok") else ("❌" if r.get("ok") is False else "—"),
            "실현 순초과": data.f_frac_pct_s(r.get("fwd_excess"), 2),
            "적중": ("✅" if r["correct"] else "❌") if r.get("correct") is not None else "⏳",
        }
        if show_axes:
            f = r.get("features") or {}
            for ax in _AXIS_COL:
                base[_AXIS_COL[ax]] = data.f_ratio(f.get(ax), 2)
        return base

    st.dataframe(pd.DataFrame([_row(r) for r in view]), hide_index=True, width="stretch",
                 column_config={"판단 근거": st.column_config.TextColumn(width="large")})
    st.caption("실현 순초과 = 평가 기간이 지난 뒤 지수 대비 초과수익(거래비용 차감) · ⏳ = 결과 성숙 대기 · "
               "⚠️ 모의투자 — 실거래 아님")
