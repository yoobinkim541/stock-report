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
import re
import secrets as _secrets
import time

_SALT_PATH = os.path.expanduser("~/.cache/dashboard_auth_salt")
COOKIE_NAME = "tn_auth"
# 세션 수명 — 기본 30일(개인 서버·읽기전용 대시보드). DASHBOARD_SESSION_DAYS 로 조정.
# 강제 로그아웃 = salt 파일(~/.cache/dashboard_auth_salt) 삭제 또는 비밀번호 변경.
def _session_days() -> int:
    try:
        return max(1, int(os.getenv("DASHBOARD_SESSION_DAYS", "30")))
    except ValueError:
        return 30


TOKEN_TTL_S = _session_days() * 86400


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
    # 서명 형식 선검증 — compare_digest 는 비ASCII 문자열에 TypeError 를 던진다.
    # 임의(손상·조작) 쿠키가 로그인 게이트 자체를 크래시시키면 안 됨 (적대 리뷰 확정 DoS)
    if not re.fullmatch(r"[0-9a-f]{64}", sig or ""):
        return False
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < (now if now is not None else time.time()):
        return False
    want = hmac.new(_token_key(secret, salt), f"tn-auth:{exp}".encode(),
                    hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(sig, want)
    except TypeError:
        return False


def _set_cookie_html(token: str, ttl_s: int = TOKEN_TTL_S) -> str:
    """로그인 성공 → 부모 문서에 인증 쿠키 주입 (srcdoc iframe = same-origin).

    HttpOnly 는 JS 로 쓰는 쿠키라 불가(Streamlit 이 서버측 Set-Cookie 를 제공하지 않음).
    https 로 서비스될 때는 Secure 를 붙여 평문 전송을 막는다."""
    return (f"<script>(function(){{var s='{COOKIE_NAME}={token}; path=/; max-age={int(ttl_s)}; SameSite=Lax';"
            f"if(window.parent.location.protocol==='https:')s+='; Secure';"
            f"window.parent.document.cookie=s;}})();</script>")


# ── 로그인 실패 제한 (IP별 점증 대기) ────────────────────────────────────────
# 전역(계정 단위) 잠금은 쓰지 않는다: 소유자 한 명뿐인 계정이라, 아무나 틀린 비번을
# 세 번 넣는 것만으로 소유자를 잠가 버리는 서비스 거부가 된다. 그래서 IP별로 세고,
# 고정 잠금 대신 실패할수록 대기 시간을 늘린다(3회까지 무료, 이후 1분→2분→4분…최대 1시간).
# 대시보드는 127.0.0.1 에만 바인딩되고 Cloudflare 터널로만 들어오므로 앱에는 모든 요청이
# 127.0.0.1 로 보인다 → 실제 IP 는 Cloudflare 가 덮어쓰는 CF-Connecting-IP 헤더로 본다.
# Streamlit 세션은 새로고침마다 새로 생기므로 횟수는 세션이 아니라 서버 파일에 둔다.
_FAIL_PATH = os.path.expanduser("~/.cache/dashboard_auth_failures.json")
FREE_ATTEMPTS = 3
BASE_LOCK_S = 60
MAX_LOCK_S = 3600
FAIL_MEMORY_S = 86400          # 마지막 실패 후 하루가 지나면 그 IP 기록을 잊는다


def lockout_seconds(fail_count: int) -> int:
    """실패 누적 횟수 → 다음 시도까지 기다릴 초 (순수). 3회 미만은 0."""
    if fail_count < FREE_ATTEMPTS:
        return 0
    return min(MAX_LOCK_S, BASE_LOCK_S * 2 ** (fail_count - FREE_ATTEMPTS))


def throttle_remaining(state: dict, ip: str, now: float) -> float:
    """이 IP 가 지금 더 기다려야 하는 초 (순수). 기록 없음·대기 끝남 = 0."""
    entry = state.get(ip)
    if not entry:
        return 0.0
    wait_until = float(entry.get("last", 0)) + lockout_seconds(int(entry.get("count", 0)))
    return max(0.0, wait_until - now)


def record_failure(state: dict, ip: str, now: float) -> int:
    """실패 1회 기록 (state 갱신) → 이 IP 의 누적 실패 횟수."""
    prune_failures(state, now)
    entry = state.setdefault(ip, {"count": 0, "last": 0.0})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last"] = now
    return entry["count"]


def record_success(state: dict, ip: str) -> None:
    """로그인 성공 → 이 IP 의 실패 기록을 지운다."""
    state.pop(ip, None)


def prune_failures(state: dict, now: float) -> None:
    """마지막 실패가 FAIL_MEMORY_S 보다 오래된 IP 를 지운다 (파일이 끝없이 커지지 않게)."""
    for ip in [k for k, v in state.items() if now - float(v.get("last", 0)) > FAIL_MEMORY_S]:
        state.pop(ip, None)


def client_ip(headers) -> str:
    """요청 헤더 → 클라이언트 IP. CF-Connecting-IP(대소문자 무관)가 없으면 'local',
    IP 형식이 아니면 'unknown' — 둘 다 하나의 묶음으로 세어 우회 통로가 되지 않게 한다."""
    import ipaddress

    value = None
    try:
        for k, v in dict(headers or {}).items():
            if str(k).lower() == "cf-connecting-ip":
                value = str(v).strip()
                break
    except Exception:
        value = None
    if not value:
        return "local"
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return "unknown"


def _with_failures(update, path: str = _FAIL_PATH):
    """실패 기록 파일을 잠근 채 읽고 update(state) 결과를 돌려준다. state 가 바뀌면 원자적으로 쓴다.
    파일이 깨졌으면 빈 기록으로 본다(실패 제한이 풀릴 뿐, 비밀번호 검사는 그대로다)."""
    import fcntl
    import json

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                state = {}
        except (FileNotFoundError, ValueError):
            state = {}
        before = json.dumps(state, sort_keys=True)
        result = update(state)
        if json.dumps(state, sort_keys=True) != before:
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, path)
        return result


def _alert_lockout(ip: str, count: int) -> None:
    """IP 가 처음 잠기면 텔레그램으로 알린다. 로그인 화면을 붙잡지 않게 백그라운드로 보낸다."""
    import threading

    def send():
        try:
            from lib.cron_common import send_cron_telegram
            send_cron_telegram(f"🔒 퀀트 대시보드 로그인 {count}회 실패 — IP {ip} 대기 시작", timeout=5)
        except Exception:
            pass

    threading.Thread(target=send, daemon=True).start()


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

    ip = "local"
    try:
        ip = client_ip(st.context.headers)
    except Exception:
        pass
    wait = _with_failures(lambda s: throttle_remaining(s, ip, time.time()))
    if wait > 0:
        st.error(f"로그인 실패가 많아 잠시 막았습니다. {max(1, int(-(-wait // 60)))}분 뒤에 다시 시도하세요.")
        return False

    pw = st.text_input("비밀번호", type="password", key="_pw")
    if pw:
        if verify_password(pw, secret):
            _with_failures(lambda s: record_success(s, ip))
            st.session_state["_authed"] = True
            # 서명 토큰 쿠키 발급 — 이후 네비게이션/리로드는 무입력 재인증
            try:
                st.iframe(
                    _set_cookie_html(issue_token(secret, _server_salt())), height=1)
            except Exception:
                pass                             # 쿠키 실패해도 이번 세션은 인증됨
            return True
        if not secret:
            st.error("DASHBOARD_PASSWORD 미설정 — 접근 차단(.env 에 설정 필요)")
            return False
        # text_input 값은 rerun 마다 남아 있으므로, 같은 입력을 rerun 마다 다시 세지 않는다.
        attempt = hashlib.sha256(pw.encode()).hexdigest()
        if st.session_state.get("_pw_counted") != attempt:
            st.session_state["_pw_counted"] = attempt
            count = _with_failures(lambda s: record_failure(s, ip, time.time()))
            if count == FREE_ATTEMPTS:
                _alert_lockout(ip, count)
            if lockout_seconds(count) > 0:
                st.error(f"비밀번호가 {count}회 틀렸습니다. "
                         f"{max(1, lockout_seconds(count) // 60)}분 뒤에 다시 시도하세요.")
                return False
        st.error("비밀번호가 틀렸습니다.")
    return False


def reconnect_watchdog_html(interval_ms: int = 3000) -> str:
    """서버 재기동 감지 → 자동 새로고침 스크립트 (순수 HTML — st.iframe 으로 주입).

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
