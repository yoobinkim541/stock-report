#!/usr/bin/env bash
# bot_watchdog.sh — telegram_bot.py 생존·코드 freshness 감시
# 크론 등록: * * * * * /home/ubuntu/projects/stock-report/bot_watchdog.sh >> /tmp/bot_watchdog.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BOT_SCRIPT="$PROJECT_DIR/telegram_bot.py"
PID_FILE="$HOME/.local/state/stock-report/barbell_bot.pid"
LOG_FILE="/tmp/barbell_bot.log"
WATCHDOG_LOCK="/tmp/bot_watchdog.lock"

mkdir -p "$(dirname "$PID_FILE")"

# 동시 실행 방지 (cron이 겹치면 skip)
exec 9>"$WATCHDOG_LOCK"
if ! flock -n 9; then
    exit 0
fi

# .env 로드
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

is_running() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    kill -0 "$pid" 2>/dev/null
}

UV="/home/ubuntu/.local/bin/uv"

restart_bot() {
    local reason="$1" pid="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bot 재시작 — ${reason}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$pid" 2>/dev/null || true
    fi
    cd "$PROJECT_DIR" || exit 1
    flock -u 9
    exec 9>&-
    nohup "$UV" run python "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1 &
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 완료 (PID $!)"
}

if is_running; then
    PID="$(cat "$PID_FILE")"
    START_EPOCH="$(date -d "$(ps -o lstart= -p "$PID" 2>/dev/null)" +%s 2>/dev/null)"
    if [ -n "$START_EPOCH" ]; then
        # 재기동 직후 파일시각 반올림으로 인한 한 번 더 재시작을 흡수한다.
        THRESH=$((START_EPOCH + 5))
        NEWER="$(find "$PROJECT_DIR"/agent_console "$PROJECT_DIR"/bot \
                      "$PROJECT_DIR"/telegram_bot.py \
                      -name '*.py' -not -path '*/__pycache__/*' \
                      -newermt "@${THRESH}" -print -quit 2>/dev/null)"
        if [ -n "$NEWER" ]; then
            restart_bot "코드 변경 감지(stale): ${NEWER#"$PROJECT_DIR"/}" "$PID"
        fi
    fi
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bot 미실행 감지 — 재시작"
restart_bot "미실행" ""
