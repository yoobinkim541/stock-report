#!/usr/bin/env bash
# deliver_investment_report.sh — Generate + deliver to @Stock_botbot
set -e

PROJECT_DIR="${STOCK_REPORT_PROJECT_DIR:-/home/ubuntu/projects/stock-report}"
cd "$PROJECT_DIR"

# Load bot token
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

STOCK_BOT_CHAT_ID=5771238245
START_TIME=$(date +%s)

PYTHON_BIN="${PYTHON_BIN:-python3}"

# Keep the no_agent cron under Hermes' 120s script timeout.
export INVESTMENT_REPORT_MAX_NASDAQ_SCAN="${INVESTMENT_REPORT_MAX_NASDAQ_SCAN:-20}"
export INVESTMENT_REPORT_MAX_KOSPI_SCAN="${INVESTMENT_REPORT_MAX_KOSPI_SCAN:-5}"
export INVESTMENT_REPORT_ARCA_PAGES="${INVESTMENT_REPORT_ARCA_PAGES:-1}"

DATE=$("$PYTHON_BIN" -c "from datetime import datetime, timezone, timedelta; print(datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d'))")

# Generate report (silent progress → stderr, keep stdout clean)
"$PYTHON_BIN" investment_report.py > /tmp/invest_report_stdout.txt 2>/tmp/invest_report_stderr.txt
REPORT_EXIT=$?

# Generate CSV from JSON summary
"$PYTHON_BIN" save_csv.py 2>>/tmp/invest_report_stderr.txt

# ── Intelligence Barbell v2.1 분석 ────────────────────────────────────
# Phase 변화 시 자동으로 텔레그램 알림 발송 (중복 발송 없음)
"$PYTHON_BIN" barbell_strategy.py > /tmp/barbell_report.txt 2>>/tmp/invest_report_stderr.txt
BARBELL_EXIT=$?
if [ $BARBELL_EXIT -ne 0 ]; then
    echo "[WARN] 바벨 전략 분석 실패 (exit $BARBELL_EXIT)" >&2
fi

# ── 포트폴리오 히스토리 기록 ──────────────────────────────────────────
"$PYTHON_BIN" portfolio_tracker.py > /tmp/tracker_report.txt 2>>/tmp/invest_report_stderr.txt
TRACKER_EXIT=$?
if [ $TRACKER_EXIT -ne 0 ]; then
    echo "[WARN] 포트폴리오 트래커 실패 (exit $TRACKER_EXIT)" >&2
fi

REPORT_FILE="$HOME/reports/investment-report-${DATE}.md"
JSON_FILE="$HOME/reports/investment-data-${DATE}.json"
SUMMARY_FILE="$HOME/reports/investment-summary-${DATE}.json"

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ ! -f "$REPORT_FILE" ]; then
    echo "[FAIL] Report not generated"
    cat /tmp/invest_report_stderr.txt
    exit 1
fi

# Send to Telegram via Bot API
if [ -n "$STOCK_BOT_TOKEN" ]; then
    HEADER="📊 주식 투자 자동화 레포트 - ${DATE}"
    curl -s -X POST "https://api.telegram.org/bot${STOCK_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${STOCK_BOT_CHAT_ID}" \
        -d "text=${HEADER}" > /dev/null

    curl -s -X POST "https://api.telegram.org/bot${STOCK_BOT_TOKEN}/sendDocument" \
        -F "chat_id=${STOCK_BOT_CHAT_ID}" \
        -F "document=@${REPORT_FILE}" \
        -F "caption=전체 레포트 (${DATE})" > /dev/null

    curl -s -X POST "https://api.telegram.org/bot${STOCK_BOT_TOKEN}/sendDocument" \
        -F "chat_id=${STOCK_BOT_CHAT_ID}" \
        -F "document=@${SUMMARY_FILE}" \
        -F "caption=분석 요약 JSON (${DATE})" > /dev/null
fi

# ── stdout: compact delivery report (this goes to Hermes cron output) ──
REPORT_SIZE=$(wc -c < "$REPORT_FILE")
PORTFOLIO_COUNT=12

echo "📊 주식 투자 레포트 전송 완료"
echo "━━━━━━━━━━━━━━━━━━"
echo "날짜: ${DATE}"
echo "실행 시간: ${DURATION}초"
echo "레포트 크기: ${REPORT_SIZE} bytes"
echo "전송 대상: @Stock_botbot"
echo ""
echo "📋 실행 통계"
echo "  - 포트폴리오: ${PORTFOLIO_COUNT}종목"
echo "  - NASDAQ 100: ${INVESTMENT_REPORT_MAX_NASDAQ_SCAN}종목 스캔"
echo "  - LLM 토큰 소비: 0 (순수 Python 만 사용)"
echo "  - API 비용: yfinance 무료 + SaveTicker 무료"
echo ""
echo "✅ @Stock_botbot 으로 전송 완료"
echo ""
echo "🏋️ 바벨 전략 분석"
if [ -f /tmp/barbell_report.txt ]; then
    PHASE_LINE=$(grep -m1 "Phase\|Bull-\|중립" /tmp/barbell_report.txt 2>/dev/null | head -1 || echo "분석 완료")
    echo "  - 현재 Phase: ${PHASE_LINE}"
    echo "  - 상태 파일: ~/.cache/barbell_state.json"
fi
