"""리서치 — 종목 랭킹 스크리너 + ML 전략 백테스트 + 정책 학습곡선.

무거운 계산(스크리너·백테스트 각 최대 1분)은 **진입 시 자동실행 안 함** — 섹션 셀렉터로
한 번에 하나만, 그 안에서 ▶실행 버튼을 눌러야 계산. 각 섹션은 @st.fragment 라 슬라이더·
버튼 조작이 페이지 전체가 아니라 그 섹션만 rerun(자연스러운 부분 갱신).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import cached, charts, data

_NOBAR = {"displayModeBar": False}
_SECTIONS = ["종목 랭킹", "전략 백테스트", "정책 학습"]


def render():
    st.title("🔬 리서치")
    sec = st.segmented_control("섹션", _SECTIONS, default="종목 랭킹",
                               key="research_section", label_visibility="collapsed") or "종목 랭킹"
    if sec == "전략 백테스트":
        _backtest_section()
    elif sec == "정책 학습":
        _learning_section()
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
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.warning(f"랭킹 없음 ({sc.get('error', '')})")
    st.caption("⚠️ 생존편향 + 검증상 종목선택 무엣지 — 정보·표시용, 매매신호 아님")


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
