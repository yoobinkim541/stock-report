#!/usr/bin/env bash
# dashboard_watchdog.sh — 퀀트 터미널 streamlit 이 죽으면 자동 재시작.
# DASHBOARD_ENABLED=true 일 때만 기동(opt-in·기본 no-op). streamlit health 로 생존 확인.
# 크론: * * * * * scripts/dashboard_watchdog.sh >> /tmp/dashboard_watchdog.log 2>&1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="/tmp/dashboard.log"
WATCHDOG_LOCK="/tmp/dashboard_watchdog.lock"

# 동시 실행 방지
exec 9>"$WATCHDOG_LOCK"
if ! flock -n 9; then
    exit 0
fi

# .env 로드 (DASHBOARD_ENABLED·PASSWORD·PORT 확인용)
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# opt-in 게이트: 꺼져 있으면 기동하지 않음 (크론 무해)
if [ "${DASHBOARD_ENABLED,,}" != "true" ]; then
    exit 0
fi

PORT="${DASHBOARD_PORT:-8501}"

# 살아있으면 no-op (streamlit health 엔드포인트)
if curl -fsS -m 3 "http://127.0.0.1:${PORT}/_stcore/health" 2>/dev/null | grep -q "ok"; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] dashboard 미실행 감지 — 재시작 (port ${PORT})"
cd "$PROJECT_DIR" || exit 1
flock -u 9
exec 9>&-
nohup bash "$SCRIPT_DIR/run_dashboard.sh" >> "$LOG_FILE" 2>&1 &
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 완료 (PID $!)"
