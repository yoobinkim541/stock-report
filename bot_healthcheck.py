#!/usr/bin/env python3
"""
bot_healthcheck.py — 봇·서버 상태 자동 점검

문제가 있을 때만 텔레그램으로 알림 전송.

크론 등록 (매 30분):
    */30 * * * * cd /home/ubuntu/projects/stock-report && uv run python bot_healthcheck.py >> /tmp/healthcheck.log 2>&1
"""

import os
import sys
import json
import time
import subprocess
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR    = os.getenv("STOCK_REPORT_PROJECT_DIR", os.path.dirname(os.path.abspath(__file__)))
BOT_TOKEN      = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID        = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")
PORTFOLIO_PATH = os.path.join(PROJECT_DIR, "portfolio_snapshot.json")

BOT_PID_FILE  = "/tmp/barbell_bot.pid"
SYNC_PORT     = int(os.getenv("SYNC_PORT", "8765"))

# 마지막 알림 쿨다운 파일 — 같은 문제를 30분마다 계속 알리는 걸 방지
_ALERT_STATE_FILE = "/tmp/healthcheck_last_alert.json"
_ALERT_COOLDOWN   = 3600  # 동일 문제는 1시간에 1회만 재알림


def _is_process_running(name: str) -> bool:
    """프로세스 이름으로 실행 중인지 확인."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", name],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_pid_alive(pid_file: str) -> bool:
    """PID 파일 기반 프로세스 확인."""
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ProcessLookupError, ValueError, PermissionError):
        return False


def _send_alert(msg: str):
    if not BOT_TOKEN:
        print("STOCK_BOT_TOKEN 미설정 — 알림 전송 불가")
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    CHAT_ID,
                "text":       f"🚨 헬스체크 경고 [{datetime.now().strftime('%m/%d %H:%M')}]\n━━━━━━━━━━━━━━━━━━\n{msg}",
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"알림 전송 실패: {e}")


def _load_alert_state() -> dict:
    try:
        with open(_ALERT_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_alert_state(state: dict):
    try:
        with open(_ALERT_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _should_alert(key: str, state: dict) -> bool:
    """쿨다운 내 동일 키 알림 중복 방지."""
    last = state.get(key, 0)
    return time.time() - last > _ALERT_COOLDOWN


# ── 개별 체크 함수 ──────────────────────────────────────────────────────

def check_telegram_bot() -> tuple[str, str] | None:
    """telegram_bot.py 프로세스 실행 여부."""
    if _is_pid_alive(BOT_PID_FILE) or _is_process_running("telegram_bot.py"):
        return None
    return ("bot_down", "❌ <b>telegram_bot.py</b> 프로세스가 없습니다\n  → bot_watchdog.sh 확인 필요")


def check_sync_server() -> tuple[str, str] | None:
    """portfolio_sync_server 헬스체크."""
    if not _is_process_running("portfolio_sync_server"):
        return ("sync_down", "❌ <b>portfolio_sync_server</b> 프로세스가 없습니다\n  → sync_server_watchdog.sh 확인 필요")
    try:
        import requests
        resp = requests.get(f"http://localhost:{SYNC_PORT}/health", timeout=5)
        if resp.status_code != 200:
            return ("sync_unhealthy", f"⚠️ sync_server HTTP {resp.status_code}")
    except Exception as e:
        return ("sync_unreachable", f"⚠️ sync_server 응답 없음: {e}")
    return None


def check_portfolio_age() -> tuple[str, str] | None:
    """portfolio_snapshot.json 최근 갱신 확인 (72시간 초과 시 경고)."""
    try:
        mtime = os.path.getmtime(PORTFOLIO_PATH)
    except FileNotFoundError:
        return ("portfolio_missing", "❌ portfolio_snapshot.json 파일이 없습니다")
    age_h = (time.time() - mtime) / 3600
    if age_h > 72:
        return ("portfolio_stale", f"⚠️ portfolio_snapshot.json 마지막 갱신 {age_h:.0f}시간 전")
    return None


def check_investment_report_cron() -> tuple[str, str] | None:
    """투자 리포트 크론 마지막 실행 확인."""
    log_file = "/tmp/stock_cron.log"
    if not os.path.exists(log_file):
        return None  # 처음 설치 시 로그 없음
    age_h = (time.time() - os.path.getmtime(log_file)) / 3600
    weekday = datetime.now().weekday()  # 0=월, 6=일
    threshold = 72 if weekday >= 4 else 26  # 금~일은 72h 허용
    if age_h > threshold:
        return ("cron_stale", f"⚠️ 투자 리포트 크론 마지막 실행 {age_h:.0f}시간 전")
    return None


def check_barbell_state_age() -> tuple[str, str] | None:
    """barbell_state.json (Phase 상태) 최근 갱신 확인."""
    state_file = os.path.expanduser("~/.cache/barbell_state.json")
    if not os.path.exists(state_file):
        return None
    age_h = (time.time() - os.path.getmtime(state_file)) / 3600
    weekday = datetime.now().weekday()
    threshold = 72 if weekday >= 4 else 26
    if age_h > threshold:
        return ("barbell_stale", f"⚠️ barbell_state.json 마지막 갱신 {age_h:.0f}시간 전")
    return None


# ── 메인 ───────────────────────────────────────────────────────────────

def main():
    checks = [
        check_telegram_bot,
        check_sync_server,
        check_portfolio_age,
        check_investment_report_cron,
        check_barbell_state_age,
    ]

    state   = _load_alert_state()
    issues  = []
    now     = time.time()

    for check in checks:
        result = check()
        if result is None:
            continue
        key, msg = result
        if _should_alert(key, state):
            issues.append(msg)
            state[key] = now
        else:
            remaining = int((_ALERT_COOLDOWN - (now - state.get(key, 0))) / 60)
            print(f"  [{key}] 쿨다운 중 (재알림까지 {remaining}분)")

    _save_alert_state(state)

    if issues:
        full_msg = "\n\n".join(issues)
        print(f"[{datetime.now()}] 🚨 {len(issues)}개 문제:\n{full_msg}")
        _send_alert(full_msg)
        sys.exit(1)
    else:
        print(f"[{datetime.now()}] ✅ 모든 체크 정상")


if __name__ == "__main__":
    main()
