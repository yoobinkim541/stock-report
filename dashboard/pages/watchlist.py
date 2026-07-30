"""dashboard/pages/watchlist.py — 관심종목 (읽기 전용). 삭제는 봇 /watch remove 에서만."""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import data


def render():
    st.title("⭐ 관심종목")
    st.caption("유명 투자자(현재: 버크셔 해서웨이) 13F 신규편입 자동 감지 + 수동 추가 "
              "· 표시 전용 · 삭제는 텔레그램 봇 /watch remove")

    rows = data.load_watchlist()
    if not rows:
        st.info("관심종목이 비어 있습니다 — 봇에서 `/watch add TICKER 메모` 로 추가하거나 "
                "버핏 13F 신규편입 크론(매주 월요일)을 기다리세요.")
        return

    df = pd.DataFrame([{
        "티커": r["ticker"], "종목": r["name"],
        "현재가": r["price"] if r["price"] is not None else None,
        "추가 사유": r["reason"], "추가일": r["added_at"][:10] if r["added_at"] else "",
    } for r in rows])

    st.caption("🔍 **행을 클릭**하면 해당 종목 상세 분석으로 이동")
    event = st.dataframe(
        df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={
            "현재가": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if sel and sel[0] < len(rows):
        st.session_state["ticker"] = rows[sel[0]]["ticker"]
        pg = st.session_state.get("_ticker_page")
        if pg:
            st.switch_page(pg)
        else:
            st.rerun()
