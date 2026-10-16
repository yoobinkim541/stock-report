#!/usr/bin/env bash
# sync_server_watchdog.sh — portfolio_sync_server.py 자동 재시작
# 크론 등록: * * * * * /home/ubuntu/projects/stock-report/scripts/sync_server_watchdog.sh >> /tmp/sync_watchdog.log 2>&1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="/tmp/sync_server.pid"
LOG_FILE="/tmp/sync_server.log"
PORT="${SYNC_PORT:-8765}"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
WATCHDOG_LOCK="/tmp/sync_server_watchdog.lock"

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="/home/ubuntu/.local/bin/uv"
    PYTHON_ARGS=(run python)
else
    PYTHON_ARGS=()
fi

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

echo "$(date '+%Y-%m-%d %H:%M:%S') sync_server 재시작"
cd "$PROJECT_DIR"
rm -f "$PID_FILE"
# uv wrapper를 PID로 기록하면 wrapper 종료/재실행 때 상태를 오판할 수 있다.
# 프로젝트 venv의 실제 Python을 setsid로 분리해, 호출한 크론/헬스체크가 끝나도 유지한다.
nohup setsid "$PYTHON_BIN" "${PYTHON_ARGS[@]}" portfolio_sync_server.py \
    >> "$LOG_FILE" 2>&1 < /dev/null &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# 즉시 실패(환경변수 누락·포트 충돌)를 워치독 로그에서 확인 가능하게 한다.
sleep 1
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') sync_server 기동 실패 (PID $SERVER_PID)"
    rm -f "$PID_FILE"
    exit 1
fi

if ! curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') sync_server 기동 중 — health 응답 대기"
fi
