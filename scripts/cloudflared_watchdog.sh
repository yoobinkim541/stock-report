#!/usr/bin/env bash
# 대시보드 외부 접속 터널(cloudflared quick tunnel) 워치독 — 죽었을 때만 재기동.
# quick tunnel 은 재기동 시 URL 이 바뀌므로 새 URL 을 텔레그램으로 통지.
# 게이트: .env DASHBOARD_TUNNEL_ENABLED=true (opt-in). 크론: 매 5분.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
grep -qE "^DASHBOARD_TUNNEL_ENABLED=true" "$DIR/.env" 2>/dev/null || exit 0
pgrep -f "cloudflared tunnel" > /dev/null && exit 0        # 살아있으면 no-op

LOG=/tmp/cloudflared.log
echo "[watchdog] $(date -u +%FT%T) 터널 재기동" >> "$LOG"
nohup cloudflared tunnel --url http://localhost:8501 >> "$LOG" 2>&1 &
sleep 12
URL=$(grep -aoE "https://[a-z0-9-]+\.trycloudflare\.com" "$LOG" | tail -1)
if [ -n "$URL" ]; then
    cd "$DIR" && uv run python -c "
import notify
notify.send_telegram('🌐 대시보드 터널 재기동 — 새 주소:\n$URL\n(quick tunnel 은 재기동마다 URL 변경)')" >> "$LOG" 2>&1
fi
