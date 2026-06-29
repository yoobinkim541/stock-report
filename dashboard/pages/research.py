"""리서치 — 종목 랭킹 스크리너 + ML 전략 백테스트 (무거움·이 페이지서만 계산)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import cached, data


def render():
    st.title("🔬 리서치")

    st.subheader("종목 랭킹 스크리너")
    st.caption("NASDAQ100 · LightGBM QQQ 초과수익 예측")
    topn = st.slider("상위 N", 10, 50, 20, 5)
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

    st.divider()
    st.subheader("ML 전략 백테스트")
    st.caption("QQQ 3년 실데이터 (nested OOS)")
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
                st.line_chart(eq)
            except Exception:
                pass
    st.caption("⚠️ 검증상 ML 종목선택·장중타이밍 무엣지 — 정보·표시용 (검증 통과 공격은 구조적 레버리지뿐)")
