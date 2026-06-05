#!/usr/bin/env bash
# bot_watchdog.sh — telegram_bot.py 가 죽으면 자동 재시작
# 크론 등록: * * * * * /path/to/bot_watchdog.sh >> /tmp/bot_watchdog.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_SCRIPT="$SCRIPT_DIR/telegram_bot.py"
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    PID_FILE="$XDG_RUNTIME_DIR/barbell_bot.pid"
else
    PID_FILE="$HOME/.local/state/stock-report/barbell_bot.pid"
fi
LOG_FILE="/tmp/barbell_bot.log"

mkdir -p "$(dirname "$PID_FILE")"

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

if is_running; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bot 미실행 감지 — 재시작"

# .env 로드
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

nohup python3 "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1 &
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 완료 (PID $!)"
