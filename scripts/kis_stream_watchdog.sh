#!/usr/bin/env bash
# kis_stream_watchdog.sh — kis_stream.py(실시간 시세 WS) 가 죽으면 자동 재시작.
# REALTIME_ENABLED=true 일 때만 기동(opt-in). 크론: * * * * * scripts/kis_stream_watchdog.sh >> /tmp/kis_stream_watchdog.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STREAM_SCRIPT="$PROJECT_DIR/kis_stream.py"
PID_FILE="${KIS_WATCHDOG_PID_FILE:-$HOME/.local/state/stock-report/kis_stream.pid}"
LOG_FILE="${KIS_WATCHDOG_LOG:-/tmp/kis_stream.log}"
WATCHDOG_LOCK="${KIS_WATCHDOG_LOCK:-/tmp/kis_stream_watchdog.lock}"
UV="${KIS_WATCHDOG_UV:-/home/ubuntu/.local/bin/uv}"

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

worker_pid() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE")
    [ -n "$pid" ] || return 1
    printf '%s' "$pid"
}

is_running() {
    local pid
    pid=$(worker_pid) || return 1
    kill -0 "$pid" 2>/dev/null
}

stop_worker() {
    local pid="$1"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$pid" 2>/dev/null || true
    fi
}

launch_stream() {
    cd "$PROJECT_DIR" || exit 1
    flock -u 9
    exec 9>&-
    nohup "$UV" run python "$STREAM_SCRIPT" >> "$LOG_FILE" 2>&1 &
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 완료 (PID $!)"
}

restart_stream() {
    local reason="$1" pid="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] kis_stream 재시작 — ${reason}"
    stop_worker "$pid"
    launch_stream
}

if ! is_running; then
    restart_stream "미실행 감지" "$(worker_pid 2>/dev/null || true)"
    exit 0
fi

# 살아 있어도 import 소스가 워커보다 최신이면 재기동한다. 시작시각을 읽지 못하면
# liveness-only 로 유지해 건강한 스트림을 잘못 끊지 않는다.
PID="$(worker_pid)"
START_EPOCH="$(date -d "$(ps -o lstart= -p "$PID" 2>/dev/null)" +%s 2>/dev/null)"
if [ -n "$START_EPOCH" ]; then
    THRESH=$((START_EPOCH + 5))
    SOURCES=(
        "$PROJECT_DIR/kis_stream.py"
        "$PROJECT_DIR/providers/kis_quote.py"
        "$PROJECT_DIR/providers/realtime_quotes.py"
        "$PROJECT_DIR/providers/orderflow_store.py"
        "$PROJECT_DIR/providers/intraday_bars.py"
    )
    NEWER=""
    for source in "${SOURCES[@]}"; do
        if [ -f "$source" ] && [ "$(stat -c %Y "$source" 2>/dev/null)" -gt "$THRESH" ]; then
            NEWER="$source"
            break
        fi
    done
    if [ -n "$NEWER" ]; then
        restart_stream "코드 변경 감지(stale): ${NEWER#"$PROJECT_DIR"/}" "$PID"
        exit 0
    fi
fi

exit 0
