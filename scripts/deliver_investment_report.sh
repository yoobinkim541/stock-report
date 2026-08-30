#!/usr/bin/env bash
# deliver_investment_report.sh — Generate + deliver to @Stock_botbot
set -e

PROJECT_DIR="${STOCK_REPORT_PROJECT_DIR:-/home/ubuntu/projects/stock-report}"
cd "$PROJECT_DIR"

# Hermes와 구형 OS cron이 잠시 겹쳐도 같은 리포트를 병렬 전송하지 않는다.
# 날짜 마커는 프로세스가 끝난 뒤 성공적으로 전송했을 때만 남기므로, 실패한 실행은 재시도할 수 있다.
DELIVERY_STATE_DIR="${INVESTMENT_REPORT_STATE_DIR:-$HOME/.local/state/stock-report}"
# Hermes 래퍼는 investment-report.lock을 이미 보유한 채 이 스크립트를 호출한다.
# 같은 파일을 다시 flock하면 정상 실행까지 스킵되므로, 모든 호출자가 공유하는 별도 파일을 쓴다.
DELIVERY_LOCK="${INVESTMENT_REPORT_DELIVERY_LOCK:-$DELIVERY_STATE_DIR/investment-report-delivery.lock}"
mkdir -p "$DELIVERY_STATE_DIR" "$(dirname "$DELIVERY_LOCK")"
exec 8>"$DELIVERY_LOCK"
if ! flock -n 8; then
    echo "[SKIP] 일일 리포트가 이미 실행 중이어서 중복 전송을 건너뜁니다" >&2
    exit 0
fi

# Load bot token
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

STOCK_BOT_CHAT_ID=5771238245
START_TIME=$(date +%s)

# 인터프리터 — 프로젝트 .venv 우선. 맨 `python3`(시스템) 는 matplotlib/lightgbm 이 없어
# 차트 생성 블록이 ModuleNotFoundError 로 조용히 죽는다(try/except 격리 + 노이즈 많은
# stdout 이라 무증상). 실제로 2026-07-01 이후 PNG 가 한 장도 안 만들어졌고, 노션은
# _latest_chart_png() 폴백으로 6/30 차트를 7주간 매일 올렸다(감사 2026-08-21).
# 다른 크론 52개와 동일하게 프로젝트 인터프리터를 쓴다.
if [ -z "${PYTHON_BIN:-}" ]; then
    if [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
        PYTHON_BIN="$PROJECT_DIR/.venv/bin/python3"
    else
        PYTHON_BIN="python3"
    fi
fi

# User-requested full scan sizes. Override env vars can still lower these if needed.
export INVESTMENT_REPORT_MAX_NASDAQ_SCAN="${INVESTMENT_REPORT_MAX_NASDAQ_SCAN:-100}"
export INVESTMENT_REPORT_MAX_KOSPI_SCAN="${INVESTMENT_REPORT_MAX_KOSPI_SCAN:-30}"
export INVESTMENT_REPORT_ARCA_PAGES="${INVESTMENT_REPORT_ARCA_PAGES:-1}"
export INVESTMENT_REPORT_LLM_ENABLED="${INVESTMENT_REPORT_LLM_ENABLED:-1}"
export INVESTMENT_REPORT_LLM_DECISION_ENABLED="${INVESTMENT_REPORT_LLM_DECISION_ENABLED:-1}"
export INVESTMENT_REPORT_LLM_DECISION_MODE="${INVESTMENT_REPORT_LLM_DECISION_MODE:-shadow}"

DATE=$("$PYTHON_BIN" -c "from datetime import datetime, timezone, timedelta; print(datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d'))")
DELIVERY_MARKER="${INVESTMENT_REPORT_SENT_MARKER:-$DELIVERY_STATE_DIR/investment-report-${DATE}.sent}"
if [ "${INVESTMENT_REPORT_FORCE_DELIVERY:-0}" != "1" ] && [ -f "$DELIVERY_MARKER" ]; then
    echo "[SKIP] ${DATE} 일일 리포트는 이미 전송되었습니다: ${DELIVERY_MARKER}"
    exit 0
fi

# Generate report (silent progress → stderr, keep stdout clean)
"$PYTHON_BIN" reports/investment_report.py > /tmp/invest_report_stdout.txt 2>/tmp/invest_report_stderr.txt
REPORT_EXIT=$?

# Generate CSV from JSON summary
"$PYTHON_BIN" reports/save_csv.py 2>>/tmp/invest_report_stderr.txt

# Generate market/news report for the unified daily delivery.
# Market data sources are best-effort; the combined report records missing files instead of failing the investment report.
if "$PYTHON_BIN" reports/market_report.py > /tmp/market_report_stdout.txt 2>/tmp/market_report_stderr.txt; then
    MARKET_EXIT=0
else
    MARKET_EXIT=$?
    echo "[WARN] 시장 리포트 생성 실패 (exit ${MARKET_EXIT})" >&2
    cat /tmp/market_report_stderr.txt >&2 || true
fi

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
MARKET_REPORT_FILE="$HOME/reports/daily-report-${DATE}.md"
MARKET_SUMMARY_FILE="$HOME/reports/daily-summary-${DATE}.txt"
COMBINED_REPORT_FILE="$HOME/reports/combined-daily-report-${DATE}.md"
COMBINED_SUMMARY_FILE="$HOME/reports/combined-daily-summary-${DATE}.txt"

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

"$PYTHON_BIN" reports/combined_daily_report.py \
    --date "$DATE" \
    --investment-report "$REPORT_FILE" \
    --investment-summary "$SUMMARY_FILE" \
    --market-report "$MARKET_REPORT_FILE" \
    --market-summary "$MARKET_SUMMARY_FILE" \
    --barbell-report /tmp/barbell_report.txt \
    --tracker-report /tmp/tracker_report.txt \
    --out-report "$COMBINED_REPORT_FILE" \
    --out-summary "$COMBINED_SUMMARY_FILE" \
    2>>/tmp/invest_report_stderr.txt

if [ ! -f "$COMBINED_REPORT_FILE" ] || [ ! -f "$COMBINED_SUMMARY_FILE" ]; then
    echo "[FAIL] Combined daily report not generated"
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
    HEADER="📊 통합 데일리 투자 리포트 - ${DATE}"
    send_telegram sendMessage \
        -d "chat_id=${STOCK_BOT_CHAT_ID}" \
        -d "text=${HEADER}"

    send_telegram sendMessage \
        -d "chat_id=${STOCK_BOT_CHAT_ID}" \
        --data-urlencode "text@${COMBINED_SUMMARY_FILE}"

    # 시각화 대시보드 (생성된 경우에만) — 수익률·RSI·매집강도 4분할 그래프
    if [ -f "$CHART_FILE" ]; then
        send_telegram sendPhoto \
            -F "chat_id=${STOCK_BOT_CHAT_ID}" \
            -F "photo=@${CHART_FILE}" \
            -F "caption=📊 포트폴리오 대시보드 (${DATE})"
    fi

    send_telegram sendDocument \
        -F "chat_id=${STOCK_BOT_CHAT_ID}" \
        -F "document=@${COMBINED_REPORT_FILE}" \
        -F "caption=통합 데일리 리포트 (${DATE})"

    marker_tmp="${DELIVERY_MARKER}.tmp.$$"
    printf 'sent_at=%s\ndate=%s\n' "$(date -Is)" "$DATE" > "$marker_tmp"
    mv -f "$marker_tmp" "$DELIVERY_MARKER"
fi

# ── stdout: compact delivery report (this goes to Hermes cron output) ──
REPORT_SIZE=$(wc -c < "$COMBINED_REPORT_FILE")
PORTFOLIO_COUNT=$("$PYTHON_BIN" -c "
import json
snap = json.load(open('portfolio_snapshot.json'))
tickers = {h['ticker'] for s in ('overseas_general', 'overseas_fractional')
           for h in snap.get(s, {}).get('holdings_usd', []) if h.get('ticker')}
print(len(tickers))
" 2>/dev/null || echo "?")

echo "📊 통합 데일리 투자 리포트 전송 완료"
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
echo "  - 시장/뉴스 리포트: exit ${MARKET_EXIT}"
echo "  - 통합 문서: ${COMBINED_REPORT_FILE}"
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
