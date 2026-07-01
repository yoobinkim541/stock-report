"""시장·캘린더 — 경제 일정 + 뉴스."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ticker_names
from dashboard import cached


def render():
    st.title("🗓️ 시장 · 캘린더")
    ec = cached.econ(14)
    if ec:
        st.subheader("경제 일정 — 향후 2주")
        st.dataframe(
            pd.DataFrame([{"일시": e["date_str"], "중요도": e["marker"], "이벤트": e["title"]}
                         for e in ec[:40]]),
            hide_index=True, width="stretch")
    else:
        st.info("경제 일정 없음 (saveticker)")

    st.divider()
    ticker = st.session_state.get("ticker", "MSFT")
    st.subheader(f"뉴스 · {ticker_names.label(ticker)}")
    st.markdown(cached.news(ticker) or "_뉴스 없음_")
