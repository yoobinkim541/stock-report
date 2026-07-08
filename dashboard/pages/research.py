"""리서치 — 종목 랭킹 스크리너 + ML 전략 백테스트 + 정책 학습곡선.

무거운 계산(스크리너·백테스트 각 최대 1분)은 **진입 시 자동실행 안 함** — 섹션 셀렉터로
한 번에 하나만, 그 안에서 ▶실행 버튼을 눌러야 계산. 각 섹션은 @st.fragment 라 슬라이더·
버튼 조작이 페이지 전체가 아니라 그 섹션만 rerun(자연스러운 부분 갱신).
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import cached, charts, data

_NOBAR = {"displayModeBar": False}
_SECTIONS = ["종목 랭킹", "전략 백테스트", "정책 학습", "축 게이트"]


def render():
    st.title("🔬 리서치")
    sec = st.segmented_control("섹션", _SECTIONS, default="종목 랭킹",
                               key="research_section", label_visibility="collapsed") or "종목 랭킹"
    if sec == "전략 백테스트":
        _backtest_section()
    elif sec == "정책 학습":
        _learning_section()
    elif sec == "축 게이트":
        _axes_gate_section()
    else:
        _screener_section()


@st.fragment
def _screener_section():
    st.subheader("종목 랭킹 스크리너")
    st.caption("NASDAQ100 · LightGBM QQQ 초과수익 예측")
    topn = st.slider("상위 N", 10, 50, 20, 5, key="scr_topn")
    last = cached.screener_last()
    c_run, c_info = st.columns([1, 2.2], vertical_alignment="center")
    run = c_run.button("🔄 다시 실행 (최대 1분)" if last else "▶ 스크리너 실행 (최대 1분)",
                       key="scr_btn", type="primary", width="stretch")
    if run:
        cached.screener.clear()                       # 인메모리 캐시 무시 — 진짜 재계산
        sc = cached.screener(topn)
        cached.screener_last.clear()
        st.session_state["scr_done"] = True
    elif st.session_state.get("scr_done"):
        sc = cached.screener(topn)
    elif last:
        sc = last                                     # 💾 마지막 실행 결과 즉시 표시
        c_info.caption(f"💾 마지막 실행 {last.get('asof', '—')} · 상위 "
                       f"{last.get('topn', '?')} — 최신화는 다시 실행")
    else:
        st.info("버튼을 눌러 스크리너를 실행하세요 — 무거운 ML 계산이라 진입 시 자동 실행하지 않습니다. "
                "한 번 실행하면 결과가 저장돼 다음부터 즉시 표시됩니다.")
        return
    meta = sc.get("meta") or {}
    if meta:
        from dashboard import theme
        _ic = meta.get("ic")
        _ic_col = theme.GREEN if (_ic or 0) > 0 else theme.RED
        st.markdown(
            _chip(f"OOS IC {data.f_ratio(_ic, 3)}", _ic_col)
            + _chip(f"ICIR {data.f_ratio(meta.get('icir'), 2)}", theme.MUTED)
            + _chip(f"상위10% 초과 {data.f_frac_pct_s(meta.get('top_decile'))}",
                    theme.GREEN if (meta.get("top_decile") or 0) > 0 else theme.RED)
            + _chip(f"학습 {meta.get('train_end', '—')}", theme.MUTED),
            unsafe_allow_html=True)
    rows = sc.get("rows") or []
    if rows:
        from dashboard import theme
        table = [{
            "순위": r.get("rank"),
            "종목": (f"{r['name']} ({r['ticker']})" if r.get("name") else r.get("ticker", ""))
                    + (" ⚠️" if r.get("surv_flag") else ""),
            "점수": r.get("score"),
            "가격": r.get("price"),
            "기술등급": r.get("tech_rating") or "—",
            "RSI": r.get("rsi_14"),
            "52주고점比": (r["close_vs_52w_high"] * 100
                           if r.get("close_vs_52w_high") is not None else None),
            "6M 모멘텀%": (r["mom_126d"] * 100 if r.get("mom_126d") is not None else None),
            "QQQ대비%p": (r["excess_mom_60d"] * 100
                          if r.get("excess_mom_60d") is not None else None),
            "재무점수": r.get("fund_score"),
            "판단근거": r.get("reason") or "—",
        } for r in rows]
        df = pd.DataFrame(table)

        def _updown(v):                                  # 등락 색 (± 셀 텍스트)
            try:
                return (f"color: {theme.GREEN}" if v > 0
                        else f"color: {theme.RED}" if v < 0 else "")
            except TypeError:
                return ""

        sty = (df.style
               .map(_updown, subset=["6M 모멘텀%", "QQQ대비%p"])
               .map(lambda v: (f"color: {theme.RED}" if isinstance(v, (int, float)) and v >= 70
                               else f"color: {theme.GREEN}"
                               if isinstance(v, (int, float)) and v <= 30 else ""),
                    subset=["RSI"]))
        _smin, _smax = float(df["점수"].min()), float(df["점수"].max())
        event = st.dataframe(
            sty, hide_index=True, width="stretch",
            height=min(670, 44 + 35 * len(df)),
            on_select="rerun", selection_mode="single-row", key="_scr_tbl",
            column_config={
                "순위": st.column_config.NumberColumn(width="small", format="%d"),
                "종목": st.column_config.TextColumn(width="medium", pinned=True),
                "점수": st.column_config.ProgressColumn(
                    format="%.3f", min_value=_smin, max_value=_smax,
                    help="LGBM 상대순위 점수 (임의 스케일 — 바는 그룹 내 상대 위치)"),
                "가격": st.column_config.NumberColumn(format="$%.2f"),
                "RSI": st.column_config.NumberColumn(format="%.0f",
                                                     help="70↑ 과열(적)·30↓ 과매도(녹)"),
                "52주고점比": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=100,
                    help="52주 고점 대비 현재가 위치"),
                "6M 모멘텀%": st.column_config.NumberColumn(format="%+.1f"),
                "QQQ대비%p": st.column_config.NumberColumn(format="%+.1f"),
                "재무점수": st.column_config.ProgressColumn(
                    format="%.0f", min_value=0, max_value=100),
                "판단근거": st.column_config.TextColumn(width="large",
                                                        help="두드러진 특징 상위 3 (모델 기여도 아님)"),
            })
        _screener_detail(event, rows, sc.get("feats") or {},
                         (sc.get("meta") or {}).get("importance") or {})
    else:
        st.warning(f"랭킹 없음 ({sc.get('error', '')})")
    st.caption("⚠️ 생존편향(⚠️) + 검증상 종목선택 무엣지 — 정보·표시용, 매매신호 아님 · "
               "행 클릭 = 상세")


def _chip(text, color):
    return (f'<span style="display:inline-block;margin:2px 6px 2px 0;padding:3px 11px;'
            f'border:1px solid {color}44;border-radius:999px;color:{color};'
            f'font-size:0.78rem;background:{color}14">{text}</span>')


def _screener_detail(event, rows, feats, importance):
    """선택 행 상세 카드 — 선정 근거 칩·지표 밴드 2열·전체 피처(한글·카테고리) 표."""
    from dashboard import theme
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if not sel or sel[0] >= len(rows):
        return
    r = rows[sel[0]]
    t = r.get("ticker", "")
    f = feats.get(t) or {}

    # ── 헤더 카드: 순위·점수·기술등급 + 선정 근거 칩 ──
    reason_chips = "".join(_chip(x.strip(), theme.BLUE)
                           for x in (r.get("reason") or "").split("·") if x.strip() and x.strip() != "—")
    rating = r.get("tech_rating") or "—"
    r_col = theme.GREEN if "매수" in rating else theme.RED if "매도" in rating else theme.MUTED
    st.markdown(
        f'<div style="background:{theme.PANEL};border:1px solid {theme.BORDER};'
        f'border-left:4px solid {theme.BLUE};border-radius:12px;padding:12px 16px">'
        f'<div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap">'
        f'<b style="font-size:1.15rem">🔎 {r.get("name") or t} ({t})</b>'
        f'<span style="color:{theme.MUTED};font-size:0.82rem">랭킹 {r.get("rank")}위 · '
        f'점수 {r.get("score"):.3f}</span>'
        f'{_chip(rating, r_col)}</div>'
        f'<div style="margin-top:6px"><span style="color:{theme.MUTED};font-size:0.74rem;'
        f'margin-right:6px">선정 근거</span>{reason_chips or "—"}</div>'
        f'<div style="color:{theme.MUTED};font-size:0.7rem;margin-top:4px">'
        f'근거 = 두드러진 특징 서술(모델 기여도 아님) · 표시·참고용 · 매매신호 아님</div></div>',
        unsafe_allow_html=True)

    # ── 지표 밴드 — 1행: 스크리너 피처(무네트워크) · 2행: 밸류·매집(선택 티커만 조회) ──
    m1 = st.columns(5)
    m1[0].metric("RSI(14)", data.f_ratio(f.get("rsi_14"), 1) if f.get("rsi_14") is not None else "—")
    m1[1].metric("52주 고점比", f"{r['close_vs_52w_high'] * 100:.0f}%"
                 if r.get("close_vs_52w_high") is not None else "—")
    m1[2].metric("6M 모멘텀", f"{r['mom_126d'] * 100:+.1f}%"
                 if r.get("mom_126d") is not None else "—")
    m1[3].metric("QQQ 대비(60d)", f"{r['excess_mom_60d'] * 100:+.1f}%p"
                 if r.get("excess_mom_60d") is not None else "—")
    m1[4].metric("재무 점수", f"{r['fund_score']:.0f}" if r.get("fund_score") is not None else "—",
                 help="수익성·이익의 질·안정성·성장·자본배분 종합 (0~100)")
    v = cached.valuation(t) or {}
    m = v.get("metrics") or {}
    _pt = data.peg_textbook(m)
    _g = data.eps_growth_fwd(m)
    inst = cached.institutional(t) or {}
    ac = inst.get("accum") or {}
    sig = ac.get("signals") or {}
    cmf_val = sig.get("cmf", f.get("cmf_21"))            # 매집 조회 실패 시 피처 폴백
    m2 = st.columns(5)
    m2[0].metric("PER", data.f_ratio(m.get("per")))
    m2[1].metric("PEG (계산)", data.f_ratio((_pt or {}).get("peg")),
                 help="PER ÷ 예상 EPS 증가율 (Fwd/TTM 1년)")
    m2[2].metric("EPS 성장률", f"{_g:+.1f}%" if _g is not None else "—")
    m2[3].metric("매집 강도", data.f_ratio(ac.get("accum_score"), 1) if ac else "—",
                 help="OBV·CMF·상승/하락 거래량 종합 — 조회 실패 시 —")
    m2[4].metric("CMF 자금흐름", data.f_ratio(cmf_val, 2) if cmf_val is not None else "—",
                 help="+ 유입 / − 유출 (매집 조회 실패 시 스크리너 피처값)")

    # ── 🧠 모델이 보는 핵심 피처 — 중요도 상위 (값 병기) ──
    tb = data.top_feature_bars(f, importance, top=8)
    if tb:
        st.plotly_chart(charts.hbar(tb["labels"], tb["values"],
                                    "🧠 모델 중요도 상위 — 이 종목의 값", pct=False),
                        width="stretch", config=_NOBAR)

    # ── 전체 피처 — 한글 라벨·카테고리·스마트 포맷 (중요도 순) ──
    fr = data.format_screener_features(f, importance)
    tcol, bcol = st.columns([2.1, 0.9], vertical_alignment="bottom")
    with tcol:
        st.markdown("**전체 피처** — 모델 중요도 순 · 한글 라벨")
    with bcol:
        if st.button("🔍 종목 분석 열기", key=f"_scr_open_{t}", width="stretch"):
            st.session_state["ticker"] = t
            pg = st.session_state.get("_ticker_page")
            if pg:
                st.switch_page(pg)
            else:
                st.rerun()
    if fr:
        cats = ["전체"] + sorted({x["구분"] for x in fr})
        pick = st.pills("구분", cats, default="전체", selection_mode="single",
                        key=f"_scr_cat_{t}", label_visibility="collapsed") or "전체"
        show = fr if pick == "전체" else [x for x in fr if x["구분"] == pick]
        from dashboard import theme as _th

        def _valcolor(v):                            # 부호·플래그 시맨틱 색
            sv = str(v)
            if sv.startswith("+") or sv == "✓":
                return f"color: {_th.GREEN}"
            if sv.startswith("-"):
                return f"color: {_th.RED}"
            return ""

        sdf = pd.DataFrame(show).style.map(_valcolor, subset=["값"])
        st.dataframe(sdf, hide_index=True, width="stretch",
                     height=min(330, 44 + 35 * len(show)),
                     column_config={
                         "지표": st.column_config.TextColumn(width="medium"),
                         "값": st.column_config.TextColumn(width="small"),
                         "구분": st.column_config.TextColumn(width="small"),
                     })


@st.fragment
def _backtest_section():
    st.subheader("ML 전략 백테스트")
    st.caption("QQQ 3년 실데이터 (nested OOS)")
    run = st.button("▶ 백테스트 실행 (최대 1분)", key="bt_btn", type="primary")
    if not (run or st.session_state.get("bt_done")):
        st.info("버튼을 눌러 백테스트를 실행하세요 — 무거운 계산이라 진입 시 자동 실행하지 않습니다.")
        return
    st.session_state["bt_done"] = True
    bt = cached.backtest()
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
                st.plotly_chart(charts.equity_curve(eq), width="stretch", config=_NOBAR)
            except Exception:
                pass
    st.caption("⚠️ 검증상 ML 종목선택·장중타이밍 무엣지 — 정보·표시용 (검증 통과 공격은 구조적 레버리지뿐)")


@st.fragment
def _axes_gate_section():
    st.subheader("🚦 가격축 ★게이트")
    st.caption("주간 재검증(토) — 순비용 워크포워드 vs 지수 · KR=25년 marcap 무생존편향 · "
               "US=S&P500 시점멤버십(상폐 가격 부재 시 커버리지 강등)")
    g = cached.axes_gate()
    specs = (("kr", "🇰🇷 국내 — KOSPI 시총 top200 · top5 월리밸 vs 시총가중"),
             ("us", "🇺🇸 미국 — S&P500 멤버십 · top5 월리밸 vs QQQ"))
    for mk, title in specs:
        e = g.get(mk) or {}
        st.markdown(f"##### {title}")
        if not e.get("available"):
            st.info("검증 결과 없음 — 토요일 재검증 크론(kr/us_axes_eval) 실행 후 생성됩니다")
            continue
        v = e.get("verdict") or {}
        st.markdown(f"**{v.get('label', '')}**")
        oos, b = v.get("oos") or {}, v.get("bench") or {}
        m = st.columns(4)
        m[0].metric("OOS 순초과/년", data.f_frac_pct_s(v.get("net_excess_cagr"), 2),
                    help="워크포워드 OOS 연결 CAGR − 지수 CAGR (비용 차감)")
        m[1].metric("MDD 전략/지수",
                    f"{(oos.get('mdd') or 0)*100:.0f}%/{(b.get('mdd') or 0)*100:.0f}%",
                    help="★목적함수 2순위 제약: 전략 MDD ≤ 지수")
        m[2].metric("DSR", data.f_ratio(v.get("dsr"), 3), help="관문 ≥0.95 — 다중검정 deflate")
        m[3].metric("PBO", data.f_ratio(v.get("pbo"), 3), help="관문 <0.5 — 과적합확률(CSCV)")
        if mk == "us" and e.get("coverage") is not None:
            st.caption(f"멤버십 가격 커버리지 {e['coverage']*100:.0f}% — 90% 미만이면 GO 자동 강등")
        rec = e.get("recommendation")
        if rec:
            pw = rec.get("policy_weights") or {}
            w_str = " · ".join(f"{k[2:]} {val:.2f}" for k, val in sorted(pw.items()) if val > 0)
            st.write(f"📌 현재 권고 축: **{rec.get('chosen')}** → {w_str or '—'}")
        sh = e.get("shadow")
        if sh:
            st.caption(("✅ shadow **반영 중** (모의 선택 전용)" if sh.get("applied")
                        else "⏸️ shadow 기록됨 — env off/stale 로 미반영") + f" · {sh.get('asof', '')}")
        else:
            st.caption("shadow 미기록 (ADAPTIVE_*_AXES_ENABLED off — 평가·표시만)")
        ch = e.get("chosen_history") or {}
        if ch:
            st.caption("워크포워드 폴드 채택 이력: "
                       + " · ".join(f"{k} ×{cnt}" for k, cnt in list(ch.items())[:6]))

        # 🛡️ 레짐 방어 오버레이 (KR — 수익 아님·낙폭 방어 추적)
        ro = e.get("regime_overlay") or {}
        if ro and not ro.get("error"):
            with st.expander(f"🛡️ 레짐 방어 오버레이 — {ro.get('code', '')}"):
                st.caption("강세(지수>200MA)=고가모멘텀 · 약세=저변동 전환. **수익 엔진 아님 — 낙폭 방어용.**")
                ov, bn, of = ro.get("overlay") or {}, ro.get("bench") or {}, ro.get("offense_alone") or {}
                k = st.columns(4)
                k[0].metric("오버레이 MDD", data.f_frac_pct(ov.get("mdd")),
                            help="순공격(hi52 단독) 대비 낙폭")
                k[1].metric("순공격比 MDD", f"{ro.get('mdd_vs_offense_pp', 0):+.0f}%p",
                            help="음수 = 낙폭 개선")
                k[2].metric("약세해 방어", ro.get("bear_defend_years", "—"),
                            help="지수 하락 연도 중 방어 성공")
                k[3].metric("초과 DSR", data.f_ratio(ro.get("dsr"), 3),
                            help="관문 ≥0.95 — 미달이면 초과수익은 통계 무의미(위기집중·whipsaw)")
                st.caption("👀 방어 기전은 확인(약세해 다수 방어)이나 초과수익 통계 미달 — "
                           "V자 반등서 whipsaw 위험 · 추적 전용·자동집행 0")

        # 💸 비용·회전율 (확실한 실행 권고)
        cs = e.get("cost_sensitivity") or {}
        if cs and not cs.get("error"):
            with st.expander("💸 비용·회전율 최적화 — 확실한 실행 권고"):
                cur, best = cs.get("current") or {}, cs.get("best") or {}
                st.caption(f"축 {cs.get('axis')} · 월간 리밸 회전율 비용이 순수익을 **연 {cur.get('drag_pp')}%p** 갉아먹음")
                st.dataframe(pd.DataFrame([{
                    "스킴": r["scheme"], "순CAGR": data.f_frac_pct_s(r["net_cagr"]),
                    "드래그%p": r["drag_pp"], "회전율": r["turnover"],
                    "순초과%p": r["net_excess_pp"], "MDD": data.f_frac_pct(r["mdd"]),
                } for r in cs.get("rows", [])], ), hide_index=True, width="stretch")
                oos = cs.get("oos") or {}
                if oos:
                    reco = oos.get("live_reco") or {}
                    vc = {"ROBUST": "✅", "MIXED": "🟡", "IN-SAMPLE": "⚠️"}.get(oos.get("verdict"), "")
                    st.caption(f"**OOS 검증 {vc} {oos.get('verdict')}** — 반기가 월간 이긴 연도 "
                               f"{int((oos.get('year_win_rate') or 0)*100)}% · gross 보존 {oos.get('gross_preserved')}"
                               f"(월간 {(oos.get('gross_mo') or 0)*100:.1f}%/반기 {(oos.get('gross_semi') or 0)*100:.1f}%) · "
                               f"다른 축 확인 {oos.get('cross_axis_confirmed')}")
                    if reco.get("min_hold_days"):
                        st.caption(f"→ 라이브 권고: **최소 보유 {reco['min_hold_days']}일** "
                                   f"(고정 주기 위상위험 회피·연속) · ~{reco['expected_drag_save_pp']}%p 절감 · "
                                   f"{reco.get('caveat', '')} · env `KR_MOCK_MIN_HOLD_DAYS`")
                    else:
                        st.caption("→ 견고 미확인 — 현행 유지(과적합 회피)")
    st.caption("⚠️ OBSERVE = 엣지 단정 불가(정직) · 반영은 모의 한정 · 실계좌 자동집행 0")


@st.fragment
def _learning_section():
    st.subheader("🧬 정책 학습 곡선")
    st.caption("모의 자기개선 — 주별 OOS + 정직 verdict (순비용 기준 · 발전하면 보이고 안 되면 무엣지)")
    mk = st.radio("시장", ["kr_mock", "us_mock"], horizontal=True, key="evo_market",
                  format_func=lambda s: "국내 (KR)" if s == "kr_mock" else "미국 (US)")
    ev = cached.learning_evolution(mk)
    v, snap = ev.get("verdict") or {}, ev.get("snapshot") or {}
    if v:
        st.markdown(f"### {v.get('emoji', '')} {v.get('label', '')}")
        st.caption(v.get("note", ""))
    m = st.columns(4)
    m[0].metric("성숙 결정", int(snap.get("n", 0)))
    m[1].metric("순비용 IC", data.f_ratio(snap.get("realized_ic"), 3))
    m[2].metric("적중률", data.f_pct(snap.get("buy_hit")))
    m[3].metric("누적 엣지", data.f_frac_pct_s(snap.get("cum_net_excess")))
    series = ev.get("series") or []
    if len([s for s in series if s.get("excess") is not None]) >= 2:
        st.plotly_chart(charts.learning_curve(series), width="stretch", config=_NOBAR)
    else:
        st.info("학습 이력 축적 중 — 주간 재학습(토)마다 누적됩니다 (콜드스타트는 정상)")
    if ev.get("adoptions"):
        st.caption("채택 이력")
        st.dataframe(pd.DataFrame([{"주": a.get("date"), "OOS 초과": a.get("excess_challenger")}
                                   for a in ev["adoptions"]]), hide_index=True, width="stretch")
    st.caption("표시·모의 정책 · 실거래 미반영 · 무엣지면 정직 공개")
