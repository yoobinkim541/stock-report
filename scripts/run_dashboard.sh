#!/usr/bin/env bash
# 퀀트 터미널 Streamlit 실행 (프로젝트 .venv — 풀 ML 스택).
# 127.0.0.1 바인드: 외부 노출은 reverse proxy(caddy, TLS+auth)가 담당 (QT4).
set -euo pipefail
cd "${STOCK_REPORT_PROJECT_DIR:-/home/ubuntu/projects/stock-report}"
set -a; [ -f .env ] && source .env; set +a
exec .venv/bin/streamlit run dashboard/app.py \
  --server.port "${DASHBOARD_PORT:-8501}" \
  --server.address 127.0.0.1 \
  --server.headless true \
  --browser.gatherUsageStats false
