"""모의투자 — 자동 페이퍼트레이딩 계좌 현황 + 판단 근거 원장 (P-series).

국내(키움 모의)·미국(KIS 해외 모의) 자동 모의투자를 한 화면에: NAV·누적 vs 지수·MDD·보유·
거래비용 + **편입/퇴출 판단 근거**(append-only 원장 ⋈ 실현 결과) + 로직 평가(정직 verdict).
표시 전용(read-only) — 주문 집행 0 · 크론 리포트(kiwoom_mock_report·us_mock_report)와 동일 데이터원.
시장 전환·필터는 @st.fragment 라 섹션만 부분 rerun.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import cached, charts, data

_NOBAR = {"displayModeBar": False}
_SURFACES = ["kr_mock", "us_mock"]
_SURF_LABEL = {"kr_mock": "🇰🇷 국내 (키움 모의)", "us_mock": "🇺🇸 미국 (KIS 해외 모의)"}
_SIDE_ICON = {"편입": "📥", "증액": "➕", "퇴출": "📤", "감액": "➖"}


def _money(x, cur: str) -> str:
    f = data._try_float(x)
    if f is None:
        return "—"
    return f"{cur}{f:,.0f}" if cur == "₩" else f"{cur}{f:,.2f}"


def render():
    st.title("🧪 자동 모의투자")
    st.caption("신호 기반 자동 페이퍼트레이딩 — 모의 도메인 하드락 · 표시 전용 · **실거래 아님**")
    mk = st.segmented_control("시장", _SURFACES, default="kr_mock", key="paper_market",
                              format_func=lambda s: _SURF_LABEL.get(s, s),
                              label_visibility="collapsed") or "kr_mock"
    _account_section(mk)
    _decisions_section(mk)


@st.fragment
def _account_section(surface: str):
    d = cached.paper(surface)
    cur = d.get("currency", "₩")
    bench = d.get("bench_name", "지수")

    if d.get("nav") is None:
        st.info("계좌 데이터 없음 — 모의투자 크론(kiwoom_mock_track·us_mock_track)이 아직 "
                "실행되지 않았거나 `*_MOCK_ENABLED` 미설정입니다.")
        return
    if not d.get("balance_ok"):
        st.caption("⚠️ 잔고 API 조회 불가 — 마지막 EOD 스냅샷 기준 표시")

    # ── 계좌 KPI ──────────────────────────────────────────────────────────────
    excess = None
    if d.get("cum_ret") is not None and d.get("bench_ret") is not None:
        excess = d["cum_ret"] - d["bench_ret"]
    m = st.columns(4)
    m[0].metric("NAV", _money(d["nav"], cur),
                delta=(f"{d['day_ret']:+.2f}% 전일" if d.get("day_ret") is not None else None))
    m[1].metric("누적 수익률", data.f_pct_s(d.get("cum_ret"), 2),
                help=f"인셉션 {d.get('inception_date') or '—'} 이후")
    m[2].metric(f"vs {bench}", (f"{excess:+.2f}%p" if excess is not None else "—"),
                delta=(f"{bench} {d['bench_ret']:+.2f}%" if d.get("bench_ret") is not None else None),
                delta_color="off", help="1순위 목표: 지수 아웃퍼폼")
    mdd_ok = (d.get("strat_mdd") is not None and d.get("bench_mdd") is not None
              and d["strat_mdd"] <= d["bench_mdd"])
    m[3].metric("MDD (전략)", data.f_pct(d.get("strat_mdd")),
                delta=(f"지수 {data.f_pct(d.get('bench_mdd'))} {'✅' if mdd_ok else '⚠️'}"
                       if d.get("bench_mdd") is not None else None),
                delta_color="off", help="2순위 목표: 최대낙폭 ≤ 지수")

    # ── NAV 곡선 ─────────────────────────────────────────────────────────────
    series = d.get("nav_series") or []
    if len(series) >= 2:
        st.plotly_chart(charts.nav_curve(series, cur), width="stretch", config=_NOBAR)
    else:
        st.info("NAV 시계열 축적 중 — EOD 스냅샷(평일 크론)마다 누적됩니다")

    # ── 보유 + 현금 ───────────────────────────────────────────────────────────
    pos = d.get("positions") or []
    left, right = st.columns([3, 1])
    with left:
        st.markdown(f"##### 보유 {len(pos)}종목")
        if pos:
            st.dataframe(pd.DataFrame([{
                "종목": p["name"], "수량": p["shares"],
                "평단": f"{p['avg']:,.0f}" if cur == "₩" else f"{p['avg']:,.2f}",
                "현재가": f"{p['cur']:,.0f}" if cur == "₩" else f"{p['cur']:,.2f}",
                "평가액": _money(p["value"], cur), "수익률": data.f_pct_s(p.get("ret"), 2),
            } for p in pos]), hide_index=True, width="stretch")
        elif d.get("balance_ok"):
            st.caption("보유 없음 — 현금 100%")
        else:
            st.caption("보유 상세는 잔고 API 연결 시 표시")
    with right:
        st.markdown("##### 현금")
        st.metric("예수금", _money(d.get("cash"), cur), label_visibility="collapsed")
        c = d.get("cost")
        if c:
            st.caption(f"💸 누적 거래비용 {_money(c['total'], cur)}\n\n"
                       f"회전율 {c['turnover']:.0f}% · 드래그 −{c['drag']:.2f}%p")

    # ── 🏗️ 구조레버 슬리브 (US — Tier3 게이트 GO 시 모의 라이브 검증) ──────────
    sl = d.get("sleeve")
    if sl:
        if sl.get("reco"):
            state = f"게이트 **GO ×{sl['reco']:.2f}** → 목표 {(sl['reco'] - 1) * 100:.0f}%"
        elif sl.get("enabled"):
            state = "게이트 미통과/stale → 목표 0% (청산 방향)"
        else:
            state = "슬리브 off (보유 잔량만)"
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


@st.fragment
def _decisions_section(surface: str):
    st.markdown("##### 🧾 판단 근거 (편입/퇴출 결정 원장)")
    st.caption("append-only 불변 원장 — 결정 시점 근거(point-in-time) ⋈ 실현 결과(성숙 후 백필)")
    d = cached.paper(surface)
    rows = d.get("decisions") or []
    if not rows:
        st.info("결정 이력 없음 — 자동 모의투자 크론 실행 후 누적됩니다")
        return
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    sides = fc1.multiselect("구분", ["편입", "퇴출", "증액", "감액", "레버슬리브"],
                            default=["편입", "퇴출"], key=f"paper_sides_{surface}")
    n = fc2.slider("표시 건수", 20, 200, 50, 10, key=f"paper_n_{surface}")
    show_axes = fc3.toggle("축 피처 보기", key=f"paper_axes_{surface}",
                           help="★새 수집 축(mom12·hi52·lowvol·pead·news) — 원장 축적 현황")
    view = [r for r in rows if not sides or r.get("side") in sides][:n]
    if not view:
        st.caption("선택한 구분의 결정 없음")
        return

    def _row(r):
        base = {
            "날짜": r["date"],
            "구분": f"{_SIDE_ICON.get(r.get('side'), '')} {r.get('side', '')}",
            "종목": r.get("name") or r.get("ticker"),
            "수량": r.get("qty"),
            "정책점수": data.f_ratio(r.get("policy_score"), 3),
            "판단 근거": r.get("reason") or "—",
            "체결": "✅" if r.get("ok") else ("❌" if r.get("ok") is False else "—"),
            "실현 순초과": data.f_frac_pct_s(r.get("fwd_excess"), 2),
            "적중": ("✅" if r["correct"] else "❌") if r.get("correct") is not None else "⏳",
        }
        if show_axes:
            f = r.get("features") or {}
            for ax in ("mom12", "hi52", "lowvol", "pead", "news"):
                base[ax] = data.f_ratio(f.get(ax), 2)
        return base

    st.dataframe(pd.DataFrame([_row(r) for r in view]), hide_index=True, width="stretch",
                 column_config={"판단 근거": st.column_config.TextColumn(width="large")})
    st.caption("실현 순초과 = horizon 경과 후 지수 대비 초과수익(거래비용 차감) · ⏳ = 결과 성숙 대기 · "
               "⚠️ 모의투자 — 실거래 아님")
