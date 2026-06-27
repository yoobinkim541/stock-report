#!/usr/bin/env bash
# kis_stream_watchdog.sh — kis_stream.py(실시간 시세 WS) 가 죽으면 자동 재시작.
# REALTIME_ENABLED=true 일 때만 기동(opt-in). 크론: * * * * * scripts/kis_stream_watchdog.sh >> /tmp/kis_stream_watchdog.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STREAM_SCRIPT="$PROJECT_DIR/kis_stream.py"
PID_FILE="$HOME/.local/state/stock-report/kis_stream.pid"
LOG_FILE="/tmp/kis_stream.log"
WATCHDOG_LOCK="/tmp/kis_stream_watchdog.lock"

mkdir -p "$(dirname "$PID_FILE")"

# 동시 실행 방지
exec 9>"$WATCHDOG_LOCK"
if ! flock -n 9; then
    exit 0
fi

# .env 로드 (REALTIME_ENABLED 확인용)
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# opt-in 게이트: 꺼져 있으면 기동하지 않음(크론 무해)
if [ "${REALTIME_ENABLED,,}" != "true" ]; then
    exit 0
fi

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

if is_running; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] kis_stream 미실행 감지 — 재시작"

UV="/home/ubuntu/.local/bin/uv"
cd "$PROJECT_DIR" || exit 1
flock -u 9
exec 9>&-
nohup "$UV" run python "$STREAM_SCRIPT" >> "$LOG_FILE" 2>&1 &
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 완료 (PID $!)"
