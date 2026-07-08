#!/usr/bin/env bash
# /tmp 위생 — 주간: 큰 크론 로그 꼬리 보존 트림 + 오래된 스모크 잔재 청소.
# 크론: deploy/crontab.stock-report (일요일 20:30 UTC)
set -u
# ① 5MB 초과 로그 → 마지막 5만 줄만 보존 (O_APPEND writer 안전: 재작성 후 truncate 없음)
for f in /tmp/*.log; do
    [ -f "$f" ] || continue
    sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$sz" -gt 5242880 ]; then
        tail -n 50000 "$f" > "$f.trim.$$" 2>/dev/null && cat "$f.trim.$$" > "$f"
        rm -f "$f.trim.$$"
        echo "trimmed: $f ($sz bytes → $(stat -c%s "$f"))"
    fi
done
# ② 2일 넘은 스모크 임시 잔재 제거 (atexit 정리 실패분 등 안전망)
find /tmp -maxdepth 1 -name "intraday_smoke_*" -mtime +2 -exec rm -rf {} + 2>/dev/null
echo "trim ok: $(date -u +%F)"
