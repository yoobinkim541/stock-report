#!/usr/bin/env bash
# sync_server_watchdog.sh — portfolio_sync_server.py 자동 재시작
# 크론 등록: * * * * * /home/ubuntu/projects/stock-report/sync_server_watchdog.sh >> /tmp/sync_watchdog.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="/tmp/sync_server.pid"
LOG_FILE="/tmp/sync_server.log"

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

if is_running; then
    exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') sync_server 재시작"
cd "$PROJECT_DIR"
nohup uv run python portfolio_sync_server.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
