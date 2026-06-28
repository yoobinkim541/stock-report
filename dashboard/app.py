"""dashboard/app.py — 퀀트 터미널 Streamlit 엔트리 (골격, QT0).

실행: bash scripts/run_dashboard.sh  (프로젝트 .venv 의 streamlit)
모듈 탭은 QT1+ 에서 채운다. 지금은 인증·헤더·탭 골격.
"""
from __future__ import annotations

import streamlit as st

from dashboard import auth, data

st.set_page_config(page_title="퀀트 터미널", page_icon="📊", layout="wide")

if not auth.password_gate():
    st.stop()

# ── 헤더: 포트폴리오 + Phase ────────────────────────────────────────────────
summ = data.portfolio_summary()
ph = data.phase_badge()

st.title("📊 퀀트 터미널")
c1, c2, c3, c4 = st.columns(4)
c1.metric("내 포트 (USD)", f"${summ['total_usd']:,.0f}", f"{summ['return_pct']:+.1f}%")
c2.metric("Phase", f"{ph['emoji']} {ph['label']}")
c3.metric("QQQ 낙폭", f"{ph['drawdown']:+.1f}%")
c4.metric("DCA 배율", f"{ph['dca']}×")

ticker = st.text_input("종목 (예: MSFT, 005930.KS)", value="MSFT").strip().upper()
st.session_state["ticker"] = ticker

# ── 모듈 탭 (QT1+ 에서 구현) ───────────────────────────────────────────────
TABS = ["가치평가", "재무제표", "리스크", "기관 보유", "뉴스", "캘린더", "스크리너"]
for tab, name in zip(st.tabs(TABS), TABS):
    with tab:
        st.info(f"{name} — 준비 중 (다음 단계에서 구현)")

st.divider()
st.caption("표시·정직 우선 · 주문 집행 없음 · 무엣지 신호는 참고용 · 과거 기반, 미래 보장 아님")
