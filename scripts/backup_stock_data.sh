#!/usr/bin/env bash
# 핵심 데이터 일일 백업 — store DB(원장·플랜·이력)·포트 스냅샷·설정 JSON.
# 보존 14일 rotate. 크론: deploy/crontab.stock-report (매일 20:40 UTC)
set -u
DEST="$HOME/backups/stock-report"
mkdir -p "$DEST"
STAMP=$(date -u +%Y%m%d)
tar czf "$DEST/stock-data-$STAMP.tar.gz" \
    -C "$HOME" \
    .local/share/stock-report/stock_report.db \
    projects/stock-report/portfolio_snapshot.json \
    projects/stock-report/dca_weights.json \
    projects/stock-report/target_weights.json 2>/dev/null
ls -t "$DEST"/stock-data-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "backup ok: $DEST/stock-data-$STAMP.tar.gz ($(du -h "$DEST/stock-data-$STAMP.tar.gz" | cut -f1))"
