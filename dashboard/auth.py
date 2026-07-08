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


def reconnect_watchdog_html(interval_ms: int = 3000) -> str:
    """서버 재기동 감지 → 자동 새로고침 스크립트 (순수 HTML — components.html 로 주입).

    health 폴링이 '실패 → 회복' 전이를 보면 부모 창을 리로드한다. 새 세션은
    _authed 가 없으므로 자연히 로그인 게이트로 이동 (배포/재시작 후 좀비 탭 방지).
    Streamlit srcdoc 컴포넌트는 same-origin — 상대경로 fetch·parent reload 동작.
    """
    return f"""<script>
(function() {{
  let down = false;
  setInterval(async () => {{
    try {{
      const r = await fetch("/_stcore/health", {{cache: "no-store"}});
      if (r.ok) {{
        if (down) window.parent.location.reload();   // 서버 복귀 → 새 세션(로그인)
      }} else {{ down = true; }}
    }} catch (e) {{ down = true; }}
  }}, {int(interval_ms)});
}})();
</script>"""
