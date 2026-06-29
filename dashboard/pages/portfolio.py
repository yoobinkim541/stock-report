"""포트폴리오 — 리스크·배분. U3 에서 위험기여/팩터 막대·게이지로 차트화."""
from __future__ import annotations

import streamlit as st

from dashboard import cached


def render():
    st.title("💼 포트폴리오")
    st.caption("포트폴리오 전체 (USD 북) · 표시 전용 · 배분 불변")
    st.code(cached.risk(), language=None)
    st.caption("과거 1년 실현 기반 · 미래 보장 아님")
