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

# User-requested full scan sizes. Override env vars can still lower these if needed.
export INVESTMENT_REPORT_MAX_NASDAQ_SCAN="${INVESTMENT_REPORT_MAX_NASDAQ_SCAN:-100}"
export INVESTMENT_REPORT_MAX_KOSPI_SCAN="${INVESTMENT_REPORT_MAX_KOSPI_SCAN:-30}"
export INVESTMENT_REPORT_ARCA_PAGES="${INVESTMENT_REPORT_ARCA_PAGES:-1}"
export INVESTMENT_REPORT_LLM_ENABLED="${INVESTMENT_REPORT_LLM_ENABLED:-1}"
export INVESTMENT_REPORT_LLM_DECISION_ENABLED="${INVESTMENT_REPORT_LLM_DECISION_ENABLED:-1}"
export INVESTMENT_REPORT_LLM_DECISION_MODE="${INVESTMENT_REPORT_LLM_DECISION_MODE:-shadow}"

DATE=$("$PYTHON_BIN" -c "from datetime import datetime, timezone, timedelta; print(datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d'))")

# Generate report (silent progress → stderr, keep stdout clean)
"$PYTHON_BIN" reports/investment_report.py > /tmp/invest_report_stdout.txt 2>/tmp/invest_report_stderr.txt
REPORT_EXIT=$?

# Generate CSV from JSON summary
"$PYTHON_BIN" reports/save_csv.py 2>>/tmp/invest_report_stderr.txt

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
SUMMARY_FILE="$HOME/reports/investment-summary-${DATE}.txt"
CHART_FILE="$HOME/reports/investment-chart-${DATE}.png"

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

if [ ! -f "$REPORT_FILE" ]; then
    echo "[FAIL] Report not generated"
    cat /tmp/invest_report_stderr.txt
    exit 1
fi

if [ ! -f "$SUMMARY_FILE" ]; then
    echo "[FAIL] Summary not generated"
    cat /tmp/invest_report_stderr.txt
    exit 1
fi

# Send to Telegram via Bot API (응답 검증: ok=false 면 실패 처리)
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
    HEADER="📊 주식 투자 자동화 레포트 - ${DATE}"
    send_telegram sendMessage \
        -d "chat_id=${STOCK_BOT_CHAT_ID}" \
        -d "text=${HEADER}"

    send_telegram sendMessage \
        -d "chat_id=${STOCK_BOT_CHAT_ID}" \
        --data-urlencode "text@${SUMMARY_FILE}"

    # 시각화 대시보드 (생성된 경우에만) — 수익률·RSI·매집강도 4분할 그래프
    if [ -f "$CHART_FILE" ]; then
        send_telegram sendPhoto \
            -F "chat_id=${STOCK_BOT_CHAT_ID}" \
            -F "photo=@${CHART_FILE}" \
            -F "caption=📊 포트폴리오 대시보드 (${DATE})"
    fi

    send_telegram sendDocument \
        -F "chat_id=${STOCK_BOT_CHAT_ID}" \
        -F "document=@${REPORT_FILE}" \
        -F "caption=전체 레포트 (${DATE})"
fi

# ── stdout: compact delivery report (this goes to Hermes cron output) ──
REPORT_SIZE=$(wc -c < "$REPORT_FILE")
PORTFOLIO_COUNT=$("$PYTHON_BIN" -c "
import json
snap = json.load(open('portfolio_snapshot.json'))
tickers = {h['ticker'] for s in ('overseas_general', 'overseas_fractional')
           for h in snap.get(s, {}).get('holdings_usd', []) if h.get('ticker')}
print(len(tickers))
" 2>/dev/null || echo "?")

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
echo "  - KOSPI 상위: ${INVESTMENT_REPORT_MAX_KOSPI_SCAN}종목 스캔"
echo "  - LLM overlay: ${INVESTMENT_REPORT_LLM_MODEL:-gpt-5-mini} (fact guard 통과 시만 리포트에 추가)"
echo "  - LLM decision: ${INVESTMENT_REPORT_LLM_DECISION_MODEL:-${INVESTMENT_REPORT_LLM_MODEL:-gpt-5-mini}} (${INVESTMENT_REPORT_LLM_DECISION_MODE}, schema guard)"
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
