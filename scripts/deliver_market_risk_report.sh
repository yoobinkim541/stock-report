#!/usr/bin/env bash
# deliver_market_risk_report.sh — Generate + deliver cross-asset market risk report to @Stock_botbot
set -e

PROJECT_DIR="${STOCK_REPORT_PROJECT_DIR:-/home/ubuntu/projects/stock-report}"
cd "$PROJECT_DIR"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

STOCK_BOT_CHAT_ID=5771238245
START_TIME=$(date +%s)
PYTHON_BIN="${PYTHON_BIN:-python3}"
DATE=$("$PYTHON_BIN" -c "from datetime import datetime, timezone, timedelta; print(datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d'))")

"$PYTHON_BIN" reports/market_risk_report.py --date "$DATE" > /tmp/market_risk_report_stdout.txt 2>/tmp/market_risk_report_stderr.txt

MARKET_RISK_REPORT_FILE="$HOME/reports/market-risk-report-${DATE}.md"
MARKET_RISK_SUMMARY_FILE="$HOME/reports/market-risk-summary-${DATE}.txt"

if [ ! -f "$MARKET_RISK_REPORT_FILE" ] || [ ! -f "$MARKET_RISK_SUMMARY_FILE" ]; then
    echo "[FAIL] Market risk report not generated"
    cat /tmp/market_risk_report_stderr.txt
    exit 1
fi

send_telegram() {
    local method="$1"
    shift
    local response
    response=$(curl -sS -X POST "https://api.telegram.org/bot${STOCK_BOT_TOKEN}/${method}" "$@")
    "$PYTHON_BIN" - "$response" <<'PY'
import json
import sys

try:
    data = json.loads(sys.argv[1])
except Exception as exc:
    print(f"[FAIL] Telegram API response parse failed: {exc}", file=sys.stderr)
    sys.exit(1)

if not data.get("ok"):
    print(f"[FAIL] Telegram API returned error: {data}", file=sys.stderr)
    sys.exit(1)
PY
}

if [ -n "$STOCK_BOT_TOKEN" ]; then
    send_telegram sendMessage \
        -d "chat_id=${STOCK_BOT_CHAT_ID}" \
        --data-urlencode "text@${MARKET_RISK_SUMMARY_FILE}"

    send_telegram sendDocument \
        -F "chat_id=${STOCK_BOT_CHAT_ID}" \
        -F "document=@${MARKET_RISK_REPORT_FILE}" \
        -F "caption=시장 위험 보고서 (${DATE})"
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
REPORT_SIZE=$(wc -c < "$MARKET_RISK_REPORT_FILE")

echo "⚠️ 시장 위험 보고서 전송 완료"
echo "━━━━━━━━━━━━━━━━━━"
echo "날짜: ${DATE}"
echo "실행 시간: ${DURATION}초"
echo "리포트 크기: ${REPORT_SIZE} bytes"
echo "전송 대상: @Stock_botbot"
echo "문서: ${MARKET_RISK_REPORT_FILE}"
echo "요약: ${MARKET_RISK_SUMMARY_FILE}"
