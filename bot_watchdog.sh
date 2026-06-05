#!/usr/bin/env bash
# bot_watchdog.sh — telegram_bot.py 가 죽으면 자동 재시작
# 크론 등록: * * * * * /home/ubuntu/projects/stock-report/bot_watchdog.sh >> /tmp/bot_watchdog.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_SCRIPT="$SCRIPT_DIR/telegram_bot.py"
PID_FILE="$HOME/.local/state/stock-report/barbell_bot.pid"
LOG_FILE="/tmp/barbell_bot.log"
WATCHDOG_LOCK="/tmp/bot_watchdog.lock"

mkdir -p "$(dirname "$PID_FILE")"

# 동시 실행 방지 (cron이 겹치면 skip)
exec 9>"$WATCHDOG_LOCK"
if ! flock -n 9; then
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

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bot 미실행 감지 — 재시작"

# .env 로드
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

UV="/home/ubuntu/.local/bin/uv"
cd "$SCRIPT_DIR" || exit 1
flock -u 9
exec 9>&-
nohup "$UV" run python "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1 &
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 완료 (PID $!)"
