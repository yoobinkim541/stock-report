"""홈 — 글랜스 랜딩 (히어로 KPI + 보유종목). U2 에서 도넛·클릭·신호·체온계 추가."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import data


def render():
    summ = data.portfolio_summary()
    ph = data.phase_badge()
    st.title("🏠 홈")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("내 포트 (USD)", f"${summ['total_usd']:,.0f}", f"{summ['return_pct']:+.1f}%")
    c2.metric("Phase", f"{ph['emoji']} {ph['label']}")
    c3.metric("QQQ 낙폭", f"{ph['drawdown']:+.1f}%")
    c4.metric("DCA 배율", f"{ph['dca']}×")

    st.divider()
    st.subheader("보유 종목")
    rows = data.load_holdings()
    if rows:
        df = pd.DataFrame([{
            "종목": r["ticker"], "이름": (r["name"] or "")[:20],
            "평가액($)": round(r["value"]), "손익%": round(r["ret"], 1), "비중%": round(r["weight"], 1),
        } for r in rows])
        st.dataframe(df, hide_index=True, width="stretch")
    else:
        st.warning("보유 데이터 없음 — portfolio_snapshot 확인")
    st.caption("표시·정보용 · 주문 집행 없음 · 과거 기반, 미래 보장 아님")
