"""dashboard/auth.py — 단순 비밀번호 게이트 (소유자 단일 계정, fail-closed).

verify_password 는 순수 함수(테스트 가능). password_gate 는 streamlit 세션 래퍼.
DASHBOARD_PASSWORD 미설정 시 접근 차단(절대 무인증 노출 금지).
"""
from __future__ import annotations

import hmac
import os


def verify_password(supplied: str, secret: str | None) -> bool:
    """상수시간 비교. secret 미설정(None/빈값) 시 항상 False (fail-closed)."""
    if not secret:
        return False
    return hmac.compare_digest(str(supplied), str(secret))


def password_gate() -> bool:
    """streamlit 비번 게이트. 인증되면 True. (env DASHBOARD_PASSWORD)"""
    import streamlit as st

    if st.session_state.get("_authed"):
        return True
    secret = os.getenv("DASHBOARD_PASSWORD")
    pw = st.text_input("비밀번호", type="password", key="_pw")
    if pw:
        if verify_password(pw, secret):
            st.session_state["_authed"] = True
            return True
        st.error("비밀번호가 틀렸습니다." if secret
                 else "DASHBOARD_PASSWORD 미설정 — 접근 차단(.env 에 설정 필요)")
    return False
