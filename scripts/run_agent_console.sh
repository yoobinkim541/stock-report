#!/usr/bin/env bash
# 로컬 에이전트 콘솔 실행. 기본은 127.0.0.1:8797 바인드.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${STOCK_REPORT_PROJECT_DIR:-"${SCRIPT_DIR}/.."}"

set -a
[ -f .env ] && source .env
set +a

export AGENT_CONSOLE_HOST="${AGENT_CONSOLE_HOST:-127.0.0.1}"
export AGENT_CONSOLE_PORT="${AGENT_CONSOLE_PORT:-8797}"

if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m agent_console.server
fi

exec python3 -m agent_console.server
