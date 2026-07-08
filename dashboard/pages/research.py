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
    run = st.button("▶ 스크리너 실행 (최대 1분)", key="scr_btn", type="primary")
    if not (run or st.session_state.get("scr_done")):
        st.info("버튼을 눌러 스크리너를 실행하세요 — 무거운 ML 계산이라 진입 시 자동 실행하지 않습니다.")
        return
    st.session_state["scr_done"] = True
    sc = cached.screener(topn)
    meta = sc.get("meta") or {}
    if meta:
        st.caption(f"OOS IC {data.f_ratio(meta.get('ic'), 3)} · ICIR {data.f_ratio(meta.get('icir'), 2)} · "
                   f"상위10% 초과 {data.f_frac_pct_s(meta.get('top_decile'))} · 학습 {meta.get('train_end', '')}")
    rows = sc.get("rows") or []
    if rows:
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
        event = st.dataframe(
            pd.DataFrame(table), hide_index=True, width="stretch",
            on_select="rerun", selection_mode="single-row", key="_scr_tbl",
            column_config={
                "점수": st.column_config.NumberColumn(format="%.3f",
                                                      help="LGBM 상대순위 점수 (임의 스케일)"),
                "가격": st.column_config.NumberColumn(format="$%.2f"),
                "RSI": st.column_config.NumberColumn(format="%.0f"),
                "52주고점比": st.column_config.NumberColumn(format="%.0f%%"),
                "6M 모멘텀%": st.column_config.NumberColumn(format="%.1f"),
                "QQQ대비%p": st.column_config.NumberColumn(format="%.1f"),
                "재무점수": st.column_config.NumberColumn(format="%.0f"),
                "판단근거": st.column_config.TextColumn(width="large",
                                                        help="두드러진 특징 상위 3 (모델 기여도 아님)"),
            })
        _screener_detail(event, rows, sc.get("feats") or {},
                         (sc.get("meta") or {}).get("importance") or {})
    else:
        st.warning(f"랭킹 없음 ({sc.get('error', '')})")
    st.caption("⚠️ 생존편향(⚠️) + 검증상 종목선택 무엣지 — 정보·표시용, 매매신호 아님 · "
               "행 클릭 = 상세")


def _screener_detail(event, rows, feats, importance):
    """선택 행 상세 카드 — 선택 티커만 네트워크(밸류·매집) + 전체 피처 표 (중요도 순)."""
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if not sel or sel[0] >= len(rows):
        return
    r = rows[sel[0]]
    t = r.get("ticker", "")
    st.markdown(f"##### 🔎 {r.get('name') or t} ({t}) — 상세")
    c1, c2, c3 = st.columns([1, 1, 0.6])
    with c1:
        v = cached.valuation(t) or {}
        m = v.get("metrics") or {}
        st.markdown("**밸류에이션**")
        vv = st.columns(3)
        vv[0].metric("PER", data.f_ratio(m.get("per")))
        vv[1].metric("PEG", data.f_ratio(m.get("peg")))
        vv[2].metric("ROE", data.f_frac_pct(m.get("roe")) if m.get("roe") is not None else "—")
    with c2:
        inst = cached.institutional(t) or {}
        ac = inst.get("accum") or {}
        st.markdown("**기관 매집**")
        ii = st.columns(2)
        ii[0].metric("매집 강도", data.f_ratio(ac.get("accum_score"), 1) if ac else "—",
                     help="OBV·CMF·상승/하락 거래량 종합")
        sig = ac.get("signals") or {}
        ii[1].metric("CMF", data.f_ratio(sig.get("cmf"), 2) if sig else "—")
    with c3:
        st.markdown("**바로가기**")
        if st.button("🔍 종목 분석 열기", key=f"_scr_open_{t}"):
            st.session_state["ticker"] = t
            pg = st.session_state.get("_ticker_page")
            if pg:
                st.switch_page(pg)
            else:
                st.rerun()
    f = feats.get(t) or {}
    if f:
        ordered = sorted(f.items(), key=lambda x: -float(importance.get(x[0], 0) or 0))
        st.markdown("**전체 피처** — 모델 중요도 순")
        st.dataframe(pd.DataFrame([{"피처": k, "값": v} for k, v in ordered]),
                     hide_index=True, width="stretch", height=240)


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
