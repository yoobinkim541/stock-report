#!/usr/bin/env bash
# 퀀트 터미널 Streamlit 실행 (프로젝트 .venv — 풀 ML 스택).
# 127.0.0.1 바인드: 외부 노출은 reverse proxy(caddy, TLS+auth)가 담당 (QT4).
set -euo pipefail
cd "${STOCK_REPORT_PROJECT_DIR:-/home/ubuntu/projects/stock-report}"
set -a; [ -f .env ] && source .env; set +a
# 핫리로드 비활성화(결정론적): 장수 프로세스에서 디스크 코드가 바뀌면 Streamlit 의
# 부분 핫리로드가 옛 서브모듈을 메모리에 붙들어 'module has no attribute ...' 렌더
# 크래시가 났었다. 코드 변경 반영은 오직 워치독의 full 재시작으로만 이뤄진다.
exec .venv/bin/streamlit run dashboard/app.py \
  --server.port "${DASHBOARD_PORT:-8501}" \
  --server.address 127.0.0.1 \
  --server.headless true \
  --server.runOnSave false \
  --server.fileWatcherType none \
  --browser.gatherUsageStats false
