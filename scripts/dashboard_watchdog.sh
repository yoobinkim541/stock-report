#!/usr/bin/env bash
# dashboard_watchdog.sh — 퀀트 터미널 streamlit 자동 재시작(2중 감시).
# DASHBOARD_ENABLED=true 일 때만 기동(opt-in·기본 no-op).
#   ① liveness  : _stcore/health 로 프로세스 생존 확인 (죽었으면 재시작)
#   ② freshness : 대시보드가 import 하는 소스 .py 의 mtime > 프로세스 기동시각 이면
#                 'stale' 로 보고 재시작. 장수 프로세스 + 디스크 코드 변경 시 Streamlit
#                 부분 핫리로드가 옛 서브모듈을 메모리에 붙들어
#                 'AttributeError: module has no attribute ...' 렌더 크래시가 나던 문제 방지.
#                 (run_dashboard.sh 는 핫리로드 off — 코드 반영은 오직 이 재시작 경로로만)
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
# streamlit 프로세스 식별 패턴 — .venv 경로까지 포함해 무관 프로세스 오매칭 방지
STREAMLIT_PAT='[.]venv/bin/streamlit run dashboard/app.py'

# 재시작 — 대상 프로세스 종료(포트 확보) 후 run_dashboard.sh 를 detach 기동
restart_dashboard() {
    local reason="$1" kpid="$2"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] dashboard 재시작 — ${reason} (port ${PORT})"
    cd "$PROJECT_DIR" || exit 1
    # 대상 PID 만 정확히 종료 — 패턴 pkill 은 cmdline 에 이 문자열을 포함한 무관
    # 프로세스(다른 셸·grep 등)까지 죽일 수 있어 금지. TERM→(최대 5s)→KILL 로 포트 확보.
    if [ -n "$kpid" ] && kill -0 "$kpid" 2>/dev/null; then
        kill "$kpid" 2>/dev/null
        for _ in 1 2 3 4 5; do kill -0 "$kpid" 2>/dev/null || break; sleep 1; done
        kill -9 "$kpid" 2>/dev/null || true
    fi
    flock -u 9
    exec 9>&-
    nohup bash "$SCRIPT_DIR/run_dashboard.sh" >> "$LOG_FILE" 2>&1 &
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 완료 (PID $!)"
}

# ① liveness — 죽었으면 재시작
if ! curl -fsS -m 3 "http://127.0.0.1:${PORT}/_stcore/health" 2>/dev/null | grep -q "ok"; then
    restart_dashboard "미실행/헬스실패" "$(pgrep -f "$STREAMLIT_PAT" | head -1)"
    exit 0
fi

# ② freshness — 살아있어도 코드가 프로세스보다 최신이면 stale 로 재시작
PID="$(pgrep -f "$STREAMLIT_PAT" | head -1)"
if [ -n "$PID" ]; then
    START_EPOCH="$(date -d "$(ps -o lstart= -p "$PID" 2>/dev/null)" +%s 2>/dev/null)"
    if [ -n "$START_EPOCH" ]; then
        # grace 버퍼: date +%s 는 기동시각을 초단위 내림 → 같은 초에 sub-second 먼저
        # 수정된 파일이 '더 최신'으로 오판돼 재시작 직후 1회 여분 재시작하던 경합 제거.
        # streamlit 이 fork 후 실제 모듈 import 까지 수 초 걸리는 창도 함께 흡수(+5s).
        # 배포는 항상 직전 재시작보다 수 초+ 뒤에 안착하므로 stale 탐지엔 영향 없음.
        THRESH=$((START_EPOCH + 5))
        # 대시보드 import 트리(+루트 공용 모듈)에서 기동 이후 변경된 첫 .py 를 탐색
        NEWER="$(find "$PROJECT_DIR"/dashboard "$PROJECT_DIR"/ml "$PROJECT_DIR"/providers \
                      "$PROJECT_DIR"/bot "$PROJECT_DIR"/lib "$PROJECT_DIR"/config \
                      "$PROJECT_DIR"/*.py \
                      -name '*.py' -not -path '*/__pycache__/*' \
                      -newermt "@${THRESH}" -print -quit 2>/dev/null)"
        if [ -n "$NEWER" ]; then
            restart_dashboard "코드 변경 감지(stale): ${NEWER#"$PROJECT_DIR"/}" "$PID"
            exit 0
        fi
    fi
fi

exit 0
