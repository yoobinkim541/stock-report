#!/usr/bin/env bash
# run_investment_report.sh — long-running wrapper for deliver_investment_report.sh
#
# Avoids no_agent 120s subprocess limit via flock + background subshell + 90min timeout.
# Cron usage:  0 23 * * * /home/ubuntu/projects/stock-report/scripts/automation/run_investment_report.sh
# Debug usage: ./scripts/automation/run_investment_report.sh --foreground
#
# Logs: /tmp/invest_run_YYYYMMDD_HHMMSS.log

set -euo pipefail

PROJECT_DIR="${STOCK_REPORT_PROJECT_DIR:-/home/ubuntu/projects/stock-report}"
LOCK_FILE="/tmp/invest_report.lock"
LOG_DIR="/tmp"
LOG_FILE="${LOG_DIR}/invest_run_$(date +%Y%m%d_%H%M%S).log"
TIMEOUT=5400  # 90 minutes

cd "$PROJECT_DIR"

_run_locked() {
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "[run_investment_report] already running — lock held by $(cat "$LOCK_FILE" 2>/dev/null || echo unknown)" >&2
        exit 0
    fi
    echo "$$" >&9

    timeout "$TIMEOUT" bash deliver_investment_report.sh 2>&1
    local rc=$?
    echo "[run_investment_report] finished exit=$rc at $(date)"
    return $rc
}

case "${1:-}" in
    --help|-h)
        echo "Usage: $0 [--foreground]"
        echo "Default: start report in background with flock + 90min timeout."
        ;;
    --foreground)
        echo "[run_investment_report] foreground mode — log: $LOG_FILE"
        _run_locked | tee "$LOG_FILE"
        ;;
    *)
        echo "[run_investment_report] background mode — log: $LOG_FILE"
        (
            _run_locked | tee "$LOG_FILE"
        ) &
        echo "[run_investment_report] started PID=$! log=$LOG_FILE"
        ;;
esac
