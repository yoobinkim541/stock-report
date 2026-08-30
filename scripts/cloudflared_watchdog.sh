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

# PID가 살아 있어도 quick tunnel의 control stream이 끊기면 프로세스만 남을 수 있다.
# 저장된 URL의 Streamlit health endpoint를 확인해야 죽은 터널을 정상으로 오판하지 않는다.
probe_tunnel() {
    local url="$1" host ip
    [ -n "$url" ] || return 1
    host="${url#*://}"
    host="${host%%/*}"
    if curl -fsS --connect-timeout 3 --max-time 8 "${url%/}/_stcore/health" 2>/dev/null | grep -q "ok"; then
        return 0
    fi
    # 서버 기본 resolver가 trycloudflare.com을 늦게/실패하는 환경에서도 public DNS로
    # edge IP를 얻어 SNI Host를 보존한 채 확인한다.
    command -v dig >/dev/null 2>&1 || return 1
    while IFS= read -r ip; do
        [ -n "$ip" ] || continue
        if curl -fsS --resolve "${host}:443:${ip}" --connect-timeout 3 --max-time 8 \
            "${url%/}/_stcore/health" 2>/dev/null | grep -q "ok"; then
            return 0
        fi
    done < <(dig +short +time=2 +tries=1 @1.1.1.1 "$host" 2>/dev/null | grep -E '^[0-9.]+$')
    return 1
}

CUR="$(cat "$URL_FILE" 2>/dev/null || true)"
PID="$(cat "$PID_FILE" 2>/dev/null || true)"
NEW=""
TUNNEL_RUNNING="false"
if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    if probe_tunnel "$CUR"; then
        NEW="$CUR"
        TUNNEL_RUNNING="true"
    else
        echo "[$(date '+%F %T')] cloudflared PID는 살아 있지만 터널 probe 실패 — 재기동"
        kill "$PID" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$PID" 2>/dev/null || true
    fi
fi

if [ "$TUNNEL_RUNNING" != "true" ]; then
    echo "[$(date '+%F %T')] cloudflared 미실행 — 터널 재시작"
    : > "$LOG"
    # watchdog lock FD는 자식이 상속하지 않게 닫는다. 상속되면 cloudflared 수명만큼
    # flock이 유지되어 이후 watchdog 실행이 모두 조용히 skip 된다.
    nohup cloudflared tunnel --url "http://localhost:${PORT}" 9>&- >> "$LOG" 2>&1 &
    echo $! > "$PID_FILE"

    # 새 trycloudflare URL 확보 — cloudflared precheck가 끝날 때까지 최대 60초 대기한다.
    for _ in $(seq 1 30); do
        NEW=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$LOG" 2>/dev/null | tail -1)
        [ -n "$NEW" ] && break
        sleep 2
    done
    if [ -z "$NEW" ]; then echo "  URL 확보 실패(60s)"; exit 1; fi
    LIVE=""
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if probe_tunnel "$NEW"; then
            LIVE="true"
            break
        fi
        sleep 2
    done
    if [ "$LIVE" != "true" ]; then
        echo "  새 URL probe 실패(20s): $NEW"
        exit 1
    fi

    echo "$NEW" > "$URL_FILE"
    echo "  새 URL: $NEW (이전: ${CUR:-없음})"
fi

# URL 변경 시 Vercel 현관(landing) 링크 갱신 → master push → Vercel 자동배포.
# 메인 트리가 feature 브랜치일 수 있으므로(라이브 실증: feat/llm-decision-layer 체크아웃
# 중이면 커밋이 엉뚱한 브랜치에 감) master 고정 전용 워크트리에서 커밋한다.
WT="$HOME/.cache/landing_master_wt"
CURRENT_GATEWAY="$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$LANDING" 2>/dev/null | tail -1)"
if [ "$CURRENT_GATEWAY" != "$NEW" ]; then
    cd "$PROJECT_DIR" || exit 1
    if [ ! -e "$WT/.git" ]; then
        # 이 스크립트는 보통 master checkout에서 실행된다. 브랜치 worktree는
        # 같은 브랜치가 이미 사용 중이면 생성할 수 없으므로 detached로 만든다.
        git worktree add --detach "$WT" master 2>>"$LOG" || { echo "  워크트리 생성 실패"; exit 1; }
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
