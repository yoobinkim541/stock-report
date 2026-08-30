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
NOTIFY_STATE_FILE="${CLOUDFLARED_NOTIFY_STATE_FILE:-$HOME/.local/state/stock-report/cloudflared_notify.state}"
NOTIFY_COOLDOWN_SECONDS="${CLOUDFLARED_NOTIFY_COOLDOWN_SECONDS:-3600}"
PROBE_RETRIES="${CLOUDFLARED_PROBE_RETRIES:-3}"
PROBE_RETRY_DELAY="${CLOUDFLARED_PROBE_RETRY_DELAY:-5}"
NEW_PROBE_RETRIES="${CLOUDFLARED_NEW_PROBE_RETRIES:-12}"
STARTUP_GRACE_SECONDS="${CLOUDFLARED_STARTUP_GRACE_SECONDS:-120}"
mkdir -p "$(dirname "$NOTIFY_STATE_FILE")"

# quick tunnel이 연속 재기동될 때 URL은 갱신하되 Telegram 알림은 쿨다운한다.
# 상태는 안내 알림의 중복만 억제하며, 터널 감시·재기동·Vercel 갱신에는 영향을 주지 않는다.
should_notify_tunnel_restart() {
    local now last state_tmp
    now="$(date +%s)"
    if ! [[ "$NOTIFY_COOLDOWN_SECONDS" =~ ^[0-9]+$ ]]; then
        NOTIFY_COOLDOWN_SECONDS=3600
    fi
    last="$(cut -d'|' -f1 "$NOTIFY_STATE_FILE" 2>/dev/null || true)"
    if [[ "$last" =~ ^[0-9]+$ ]] && (( now >= last )) && (( now - last < NOTIFY_COOLDOWN_SECONDS )); then
        return 1
    fi
    state_tmp="${NOTIFY_STATE_FILE}.tmp.$$"
    printf '%s|%s\n' "$now" "$NEW" > "$state_tmp"
    mv -f "$state_tmp" "$NOTIFY_STATE_FILE"
    return 0
}

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

# 외부 edge probe는 순간적으로 실패할 수 있다. 한 번의 실패만으로 PID를 죽이지 않는다.
probe_tunnel_with_retries() {
    local url="$1" retries="${2:-$PROBE_RETRIES}" delay="$PROBE_RETRY_DELAY" attempt
    if ! [[ "$retries" =~ ^[0-9]+$ ]] || (( retries < 1 )); then retries=3; fi
    if ! [[ "$delay" =~ ^[0-9]+([.][0-9]+)?$ ]]; then delay=5; fi
    for ((attempt = 1; attempt <= retries; attempt++)); do
        if probe_tunnel "$url"; then return 0; fi
        [ "$attempt" -lt "$retries" ] && sleep "$delay"
    done
    return 1
}

CUR="$(cat "$URL_FILE" 2>/dev/null || true)"
PID="$(cat "$PID_FILE" 2>/dev/null || true)"
NEW=""
TUNNEL_RUNNING="false"
if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    if probe_tunnel_with_retries "$CUR"; then
        NEW="$CUR"
        TUNNEL_RUNNING="true"
    else
        PID_AGE_SECONDS="$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')"
        if ! [[ "$STARTUP_GRACE_SECONDS" =~ ^[0-9]+$ ]]; then STARTUP_GRACE_SECONDS=120; fi
        if [ -n "$CUR" ] && [[ "$PID_AGE_SECONDS" =~ ^[0-9]+$ ]] && (( PID_AGE_SECONDS < STARTUP_GRACE_SECONDS )); then
            echo "[$(date '+%F %T')] cloudflared 시작 유예 중 — probe 재실패지만 PID 유지 (${PID_AGE_SECONDS}/${STARTUP_GRACE_SECONDS}초)"
            NEW="$CUR"
            TUNNEL_RUNNING="true"
        else
            echo "[$(date '+%F %T')] cloudflared PID는 살아 있지만 연속 터널 probe 실패 — 재기동"
            kill "$PID" 2>/dev/null || true
            for _ in 1 2 3 4 5; do
                kill -0 "$PID" 2>/dev/null || break
                sleep 1
            done
            kill -9 "$PID" 2>/dev/null || true
        fi
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
    if ! probe_tunnel_with_retries "$NEW" "$NEW_PROBE_RETRIES"; then
        echo "  새 URL probe 실패(${NEW_PROBE_RETRIES}회): $NEW"
        exit 1
    fi

    echo "$NEW" > "$URL_FILE"
    echo "  새 URL: $NEW (이전: ${CUR:-없음})"
fi

# URL 변경 시 Vercel 현관(landing) 링크 갱신 → master push → Vercel 자동배포.
# 메인 트리가 feature 브랜치일 수 있으므로(라이브 실증: feat/llm-decision-layer 체크아웃
# 중이면 커밋이 엉뚱한 브랜치에 감) master 고정 전용 워크트리에서 커밋한다.
WT="$HOME/.cache/landing_master_wt"
LANDING_STATE_FILE="$HOME/.cache/dashboard_tunnel_landing_url.txt"
write_landing_state() {
    local value="$1" state_tmp="${LANDING_STATE_FILE}.tmp.$$"
    printf '%s\n' "$value" > "$state_tmp" && mv -f "$state_tmp" "$LANDING_STATE_FILE"
}

# detached worktree에서 push한 뒤 원래 checkout은 낡은 URL을 계속 가질 수 있다.
# 상태 파일이 없으면 worktree의 master 사본을 우선 사용해 이미 적용된 URL을 재공지하지 않는다.
CURRENT_GATEWAY="$(cat "$LANDING_STATE_FILE" 2>/dev/null || true)"
LANDING_ALREADY_CANONICAL="false"
if [ -z "$CURRENT_GATEWAY" ] && [ -f "$WT/src/lib/gateway.ts" ]; then
    CURRENT_GATEWAY="$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$WT/src/lib/gateway.ts" 2>/dev/null | tail -1)"
    [ -n "$CURRENT_GATEWAY" ] && [ "$CURRENT_GATEWAY" = "$NEW" ] && LANDING_ALREADY_CANONICAL="true"
fi
if [ -z "$CURRENT_GATEWAY" ]; then
    CURRENT_GATEWAY="$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$LANDING" 2>/dev/null | tail -1)"
fi

if [ "$CURRENT_GATEWAY" != "$NEW" ]; then
    cd "$PROJECT_DIR" || exit 1
    if [ ! -e "$WT/.git" ]; then
        # 이 스크립트는 보통 master checkout에서 실행된다. 브랜치 worktree는
        # 같은 브랜치가 이미 사용 중이면 생성할 수 없으므로 detached로 만든다.
        git worktree add --detach "$WT" master 2>>"$LOG" || { echo "  워크트리 생성 실패"; exit 1; }
    fi
    cd "$WT" || exit 1
    if ! git fetch -q origin master || ! git reset -q --hard origin/master; then
        echo "  master 현관 worktree 동기화 실패"
        exit 1
    fi
    sed -i -E "s#https://[a-z0-9-]+\.trycloudflare\.com#${NEW}#g" src/lib/gateway.ts
    APPLIED="false"
    if ! git diff --quiet -- src/lib/gateway.ts 2>/dev/null; then
        git add src/lib/gateway.ts
        if git commit -q -m "chore(dashboard): 터널 URL 자동 갱신 (${NEW})" && \
            git push -q origin HEAD:master; then
            echo "  Vercel 현관 갱신 push 완료"
            APPLIED="true"
        else
            echo "  Vercel 현관 갱신 push 실패"
        fi
    else
        # origin/master가 이미 NEW를 가리키고 있었던 경우도 적용 완료로 간주한다.
        APPLIED="true"
    fi
    if [ "$APPLIED" = "true" ]; then
        write_landing_state "$NEW"
        # 텔레그램 통지는 실제 URL 변경을 push한 경우에만 보낸다.
        if [ "$LANDING_ALREADY_CANONICAL" != "true" ] && should_notify_tunnel_restart; then
            cd "$PROJECT_DIR" && uv run python -c "
import notify
notify.send_telegram('🌐 대시보드 터널 재기동 — Vercel 현관 주소는 그대로, 새 터널 URL 자동 반영됨\n(직통: ${NEW})')" >> "$LOG" 2>&1 || true
        elif [ "$LANDING_ALREADY_CANONICAL" != "true" ]; then
            echo "  터널 재기동 Telegram 알림 억제 (${NOTIFY_COOLDOWN_SECONDS}초 쿨다운)"
        else
            echo "  현관 URL은 이미 canonical worktree에 적용됨 — 알림 생략"
        fi
    fi
elif [ -n "$NEW" ]; then
    # 상태 파일이 없던 최초 실행도 현재 canonical URL을 기억해 다음 분의 재공지 방지.
    write_landing_state "$NEW"
fi
