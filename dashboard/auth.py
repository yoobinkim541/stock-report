"""dashboard/auth.py — 단순 비밀번호 게이트 (소유자 단일 계정, fail-closed) + 쿠키 세션.

verify_password/issue_token/verify_token 은 순수 함수(테스트 가능). password_gate 는
streamlit 세션 래퍼. DASHBOARD_PASSWORD 미설정 시 접근 차단(절대 무인증 노출 금지).

쿠키 세션(2026-07): 매크로 카드 앵커(?tk=)·F5·재기동 워치독 리로드는 **브라우저
네비게이션 = 새 Streamlit 세션**이라 session_state 의 _authed 가 소실 → 매번 비번
재입력을 요구하던 문제. 로그인 성공 시 HMAC 서명 토큰(만료 포함)을 쿠키로 심고
`st.context.cookies` 로 자동 재인증한다. 토큰 키 = sha256(비밀번호 + 서버 salt) —
비밀번호 변경 또는 salt 파일 삭제(강제 로그아웃) 시 전 토큰 즉시 무효.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets
import time

_SALT_PATH = os.path.expanduser("~/.cache/dashboard_auth_salt")
COOKIE_NAME = "tn_auth"
TOKEN_TTL_S = 30 * 86400          # 30일 — 개인 서버·읽기전용 대시보드 기준


def verify_password(supplied: str, secret: str | None) -> bool:
    """상수시간 비교. secret 미설정(None/빈값) 시 항상 False (fail-closed)."""
    if not secret:
        return False
    return hmac.compare_digest(str(supplied), str(secret))


def _server_salt() -> str:
    """서버 영속 salt (없으면 생성). 삭제 = 전 기기 강제 로그아웃."""
    try:
        with open(_SALT_PATH, encoding="utf-8") as f:
            s = f.read().strip()
        if s:
            return s
    except FileNotFoundError:
        pass
    s = _secrets.token_hex(32)
    os.makedirs(os.path.dirname(_SALT_PATH), exist_ok=True)
    with open(_SALT_PATH, "w", encoding="utf-8") as f:
        f.write(s)
    return s


def _token_key(secret: str, salt: str) -> bytes:
    return hashlib.sha256(f"{secret}:{salt}".encode()).digest()


def issue_token(secret: str, salt: str, now: float | None = None,
                ttl_s: int = TOKEN_TTL_S) -> str:
    """서명 토큰 `exp.hmac` 발급 (순수 — now 주입 가능). 비밀번호 자체는 미포함."""
    exp = int((now if now is not None else time.time()) + ttl_s)
    sig = hmac.new(_token_key(secret, salt), f"tn-auth:{exp}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_token(token: str, secret: str | None, salt: str,
                 now: float | None = None) -> bool:
    """토큰 검증 (순수) — 서명 상수시간 비교 + 만료. 형식 불량/secret 없음 = False."""
    if not secret or not token or "." not in str(token):
        return False
    exp_s, _, sig = str(token).partition(".")
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < (now if now is not None else time.time()):
        return False
    want = hmac.new(_token_key(secret, salt), f"tn-auth:{exp}".encode(),
                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


def _set_cookie_html(token: str, ttl_s: int = TOKEN_TTL_S) -> str:
    """로그인 성공 → 부모 문서에 인증 쿠키 주입 (srcdoc iframe = same-origin)."""
    return (f"<script>window.parent.document.cookie = "
            f"'{COOKIE_NAME}={token}; path=/; max-age={int(ttl_s)}; SameSite=Lax';"
            f"</script>")


def password_gate() -> bool:
    """streamlit 비번 게이트. 인증되면 True. (env DASHBOARD_PASSWORD)

    순서: 세션 → 쿠키(서명 토큰 자동 재인증 — 카드 앵커/F5/재기동 리로드에도 유지)
    → 비번 입력(성공 시 쿠키 발급).
    """
    import streamlit as st

    if st.session_state.get("_authed"):
        return True
    secret = os.getenv("DASHBOARD_PASSWORD")

    # 쿠키 자동 재인증 — st.context.cookies 는 read-only·요청 시점 스냅샷 (graceful)
    try:
        tok = st.context.cookies.get(COOKIE_NAME)
    except Exception:
        tok = None
    if tok and verify_token(tok, secret, _server_salt()):
        st.session_state["_authed"] = True
        return True

    pw = st.text_input("비밀번호", type="password", key="_pw")
    if pw:
        if verify_password(pw, secret):
            st.session_state["_authed"] = True
            # 서명 토큰 쿠키 발급 — 이후 네비게이션/리로드는 무입력 재인증
            try:
                st.components.v1.html(
                    _set_cookie_html(issue_token(secret, _server_salt())), height=0)
            except Exception:
                pass                             # 쿠키 실패해도 이번 세션은 인증됨
            return True
        st.error("비밀번호가 틀렸습니다." if secret
                 else "DASHBOARD_PASSWORD 미설정 — 접근 차단(.env 에 설정 필요)")
    return False


def reconnect_watchdog_html(interval_ms: int = 3000) -> str:
    """서버 재기동 감지 → 자동 새로고침 스크립트 (순수 HTML — components.html 로 주입).

    health 폴링이 '실패 → 회복' 전이를 보면 부모 창을 리로드한다. 쿠키 세션 도입으로
    리로드 후에도 서명 토큰이 유효하면 무입력 재인증(비번 화면으로 안 튕김).
    hair-trigger 방어: fetch 에 2.5s 타임아웃 + **연속 3회 실패**부터 down 판정
    (일시적 커넥션 리셋 1회로 리로드되던 문제 — 조사 확정).
    Streamlit srcdoc 컴포넌트는 same-origin — 상대경로 fetch·parent reload 동작.
    """
    return f"""<script>
(function() {{
  let down = false, fails = 0;
  setInterval(async () => {{
    const ctl = new AbortController();
    const tm = setTimeout(() => ctl.abort(), 2500);
    try {{
      const r = await fetch("/_stcore/health", {{cache: "no-store", signal: ctl.signal}});
      clearTimeout(tm);
      if (r.ok) {{
        if (down) window.parent.location.reload();   // 서버 복귀 → 쿠키로 자동 재인증
        fails = 0;
      }} else if (++fails >= 3) {{ down = true; }}
    }} catch (e) {{
      clearTimeout(tm);
      if (++fails >= 3) down = true;                 // 단발 리셋/타임아웃은 무시
    }}
  }}, {int(interval_ms)});
}})();
</script>"""
