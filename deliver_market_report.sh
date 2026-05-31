#!/usr/bin/env bash
# deliver_market_report.sh — Generate + deliver market report to @Stock_botbot
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Load bot token
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

DATE=$(python3 -c "from datetime import datetime, timezone, timedelta; print(datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d'))")

STOCK_BOT_CHAT_ID=5771238245
START_TIME=$(date +%s)

# Generate report (silent progress -> file)
python3 market_report.py > /tmp/market_report_stdout.txt 2>/tmp/market_report_stderr.txt
REPORT_EXIT=$?

REPORT_FILE="$HOME/reports/daily-report-${DATE}.md"

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ ! -f "$REPORT_FILE" ]; then
    echo "[FAIL] Market report not generated"
    cat /tmp/market_report_stderr.txt
    exit 1
fi

# Send to Telegram via Bot API
if [ -n "$STOCK_BOT_TOKEN" ]; then
    HEADER="📈 주식 시장 일일 리포트 - ${DATE}"
    curl -s -X POST "https://api.telegram.org/bot${STOCK_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${STOCK_BOT_CHAT_ID}" \
        -d "text=${HEADER}" > /dev/null

    curl -s -X POST "https://api.telegram.org/bot${STOCK_BOT_TOKEN}/sendDocument" \
        -F "chat_id=${STOCK_BOT_CHAT_ID}" \
        -F "document=@${REPORT_FILE}" \
        -F "caption=📊 시장 리포트 (${DATE})" > /dev/null
fi

# ── stdout: compact delivery report ──
REPORT_SIZE=$(wc -c < "$REPORT_FILE")

echo "📈 주식 시장 일일 리포트 전송 완료"
echo "━━━━━━━━━━━━━━━━━━"
echo "날짜: ${DATE}"
echo "실행 시간: ${DURATION}초"
echo "레포트 크기: ${REPORT_SIZE} bytes"
echo "전송 대상: @Stock_botbot"
echo ""
echo "📋 실행 통계"
echo "  - LLM 토큰 소비: 0 (순수 Python 만 사용)"
echo "  - API 비용: yfinance 무료 + SaveTicker 무료 + Yahoo free"
echo ""
echo "✅ @Stock_botbot 으로 전송 완료"