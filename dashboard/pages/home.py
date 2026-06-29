"""홈 — 글랜스 랜딩 (포트폴리오 중심). 히어로 KPI + 배분 도넛 + 클릭 보유표 + Phase + 오늘 일정."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import cached, charts, data


def render():
    summ = data.portfolio_summary()
    ph = data.phase_badge()

    st.title("🏠 홈")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("내 포트 (USD)", f"${summ['total_usd']:,.0f}", f"{summ['return_pct']:+.1f}%")
    c2.metric("Phase", f"{ph['emoji']} {ph['label']}")
    c3.metric("QQQ 낙폭", f"{ph['drawdown']:+.1f}%")
    c4.metric("DCA 배율", f"{ph['dca']}×")

    rows = data.load_holdings()
    if rows:
        left, right = st.columns([1, 1.3])
        with left:
            st.caption("배분")
            st.plotly_chart(charts.allocation_donut(rows), width="stretch",
                            config={"displayModeBar": False})
        with right:
            st.caption("보유 종목 — 행 클릭 시 종목 분석 대상 변경")
            df = pd.DataFrame([{
                "종목": r["ticker"], "이름": (r["name"] or "")[:18],
                "평가액($)": round(r["value"]), "손익%": round(r["ret"], 1), "비중%": round(r["weight"], 1),
            } for r in rows])
            ev = st.dataframe(df, hide_index=True, width="stretch",
                              on_select="rerun", selection_mode="single-row")
            sel = ev.selection.rows if hasattr(ev, "selection") else []
            if sel:
                picked = df.iloc[sel[0]]["종목"]
                if picked and picked != st.session_state.get("ticker"):
                    st.session_state["ticker"] = picked
                    st.toast(f"종목 분석 → {picked}")
                    _tp = st.session_state.get("_ticker_page")
                    if _tp is not None:
                        st.switch_page(_tp)   # 종목 분석 페이지로 자동 이동
                    else:
                        st.rerun()            # 단독 렌더(테스트) 폴백
    else:
        st.warning("보유 데이터 없음 — portfolio_snapshot 확인")

    # Phase 행동 지침 (표시 전용)
    st.info(f"**이번 국면 {ph['emoji']} {ph['label']}** · 권장 DCA 배율 **{ph['dca']}×** "
            f"(QQQ 낙폭 {ph['drawdown']:+.1f}%) — 표시·참고용, 자동집행 없음")

    # 오늘/임박 경제 일정 (상위 5)
    ec = cached.econ(7)
    if ec:
        st.caption("📅 임박 경제 일정")
        for e in ec[:5]:
            st.write(f"{e['marker']} `{e['date_str']}` {e['title']}")

    st.caption("표시·정보용 · 주문 집행 없음 · 과거 기반, 미래 보장 아님")
