#!/usr/bin/env bash
# cloudflared_watchdog.sh — 퀀트 터미널 quick 터널 유지 + URL 변경 시 Vercel 현관 자동 갱신.
#
# DASHBOARD_ENABLED=true 일 때만 기동(opt-in·기본 no-op). cloudflared 프로세스가
# 죽으면 재시작 → 새 trycloudflare URL 확보 → src/lib/gateway.ts 의 상수를 교체하고
# git push → Vercel(Next.js 앱)이 자동 재배포.
# 그래서 Vercel 현관 주소는 항상 고정이고, 그 뒤 터널만 자동 추적된다(도메인 불요).
#
# ⚠️ pkill -f 금지(자기 cmdline 자기매치 함정) → cloudflared 종료는 PID 파일로만.
# 크론: * * * * * scripts/cloudflared_watchdog.sh >> /tmp/cloudflared_watchdog.log 2>&1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG="/tmp/cloudflared.log"
LOCK="/tmp/cloudflared_watchdog.lock"
PID_FILE="$HOME/.local/state/stock-report/cloudflared.pid"
URL_FILE="$HOME/.cache/dashboard_tunnel_url.txt"
LANDING="$PROJECT_DIR/src/lib/gateway.ts"
mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$URL_FILE")"

# 동시 실행 방지
exec 9>"$LOCK"
if ! flock -n 9; then exit 0; fi

# .env 로드 (게이트·포트)
if [ -f "$PROJECT_DIR/.env" ]; then set -a; source "$PROJECT_DIR/.env"; set +a; fi
[ "${DASHBOARD_ENABLED,,}" = "true" ] || exit 0          # opt-in

PORT="${DASHBOARD_PORT:-8501}"

# cloudflared 프로세스 살아있으면 no-op (스트림릿 일시중단엔 터널 재시작 안 함)
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    exit 0
fi

echo "[$(date '+%F %T')] cloudflared 미실행 — 터널 재시작"
: > "$LOG"
nohup cloudflared tunnel --url "http://localhost:${PORT}" >> "$LOG" 2>&1 &
echo $! > "$PID_FILE"

# 새 trycloudflare URL 확보 — 로그 파일 폴링(최대 60초). cloudflared precheck 가
# ~15초 걸리고 URL 은 그 뒤 찍히므로 짧은 타임아웃은 놓친다. URL 은 로그에 남으므로
# 파일을 반복 grep(고정 tail -f 보다 견고).
NEW=""
for _ in $(seq 1 30); do
    NEW=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$LOG" 2>/dev/null | tail -1)
    [ -n "$NEW" ] && break
    sleep 2
done
if [ -z "$NEW" ]; then echo "  URL 확보 실패(60s)"; exit 1; fi
CUR=$(cat "$URL_FILE" 2>/dev/null)
echo "$NEW" > "$URL_FILE"
echo "  새 URL: $NEW (이전: ${CUR:-없음})"

# URL 변경 시 Vercel 현관(landing) 링크 갱신 → master push → Vercel 자동배포.
# 메인 트리가 feature 브랜치일 수 있으므로(라이브 실증: feat/llm-decision-layer 체크아웃
# 중이면 커밋이 엉뚱한 브랜치에 감) master 고정 전용 워크트리에서 커밋한다.
WT="$HOME/.cache/landing_master_wt"
if [ "$NEW" != "$CUR" ]; then
    cd "$PROJECT_DIR" || exit 1
    if [ ! -e "$WT/.git" ]; then
        git worktree add "$WT" master 2>>"$LOG" || { echo "  워크트리 생성 실패"; exit 1; }
    fi
    cd "$WT" || exit 1
    git fetch -q origin master && git reset -q --hard origin/master
    sed -i -E "s#https://[a-z0-9-]+\.trycloudflare\.com#${NEW}#g" src/lib/gateway.ts
    if ! git diff --quiet -- src/lib/gateway.ts 2>/dev/null; then
        git add src/lib/gateway.ts
        git commit -q -m "chore(dashboard): 터널 URL 자동 갱신 (${NEW})" && \
        git push -q origin HEAD:master && echo "  Vercel 현관 갱신 push 완료"
    fi
    # 텔레그램 통지 (Vercel 현관은 고정이라 안내용 — 실패해도 무해)
    cd "$PROJECT_DIR" && uv run python -c "
import notify
notify.send_telegram('🌐 대시보드 터널 재기동 — Vercel 현관 주소는 그대로, 새 터널 URL 자동 반영됨\n(직통: ${NEW})')" >> "$LOG" 2>&1 || true
fi
