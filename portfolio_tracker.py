#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_tracker.py — 포트폴리오 일일 히스토리 추적 + QQQI 배당 기록

매일 자동 실행: 포트폴리오 가치 스냅샷 저장 → 1d/7d/30d/3m 수익률 계산
배당 수령 시 수동 기록 → 누적 배당 수익 추적

Usage:
  python3 portfolio_tracker.py              # 오늘 스냅샷 기록 + 성과 리포트
  python3 portfolio_tracker.py --report     # 리포트만 출력 (기록 없음)
  python3 portfolio_tracker.py --dividend 22.15 ORCL "5월 배당"
  python3 portfolio_tracker.py --send       # 텔레그램 발송
"""

import os, sys, json, argparse, logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

from barbell_strategy import (
    fetch_portfolio_value, fetch_exchange_rate, fetch_qqq_data,
    classify_market, fetch_rsi, fetch_vix,
    _bar, BULL_PHASES, BEAR_PHASES,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── 파일 경로 ─────────────────────────────────────────────────────────
DATA_DIR      = Path.home() / ".local" / "share" / "stock-report"
HISTORY_FILE  = DATA_DIR / "portfolio_history.json"
DIVIDEND_FILE = DATA_DIR / "qqqi_dividends.json"


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════
#  히스토리 저장/로드
# ══════════════════════════════════════════════════════════════════════

def load_history() -> list:
    _ensure_dir()
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(records: list):
    _ensure_dir()
    HISTORY_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def record_daily(dry_run: bool = False) -> dict:
    """오늘 포트폴리오 가치를 히스토리에 기록. dry_run=True면 저장 생략."""
    today = datetime.now().strftime("%Y-%m-%d")
    records = load_history()

    # 오늘 이미 기록됐으면 덮어쓰기 (하루 1회만 유효)
    records = [r for r in records if r.get("date") != today]

    port = fetch_portfolio_value()
    fx   = fetch_exchange_rate()
    qqq  = fetch_qqq_data()
    rsi  = fetch_rsi("QQQ")
    vix  = fetch_vix()
    mt, pk = classify_market(qqq, rsi, vix)

    entry = {
        "date":           today,
        "total_usd":      port["total_usd"],
        "total_krw":      int(port["total_usd"] * fx),
        "exchange_rate":  fx,
        "sgov_usd":       port["sgov_usd"],
        "qqqi_usd":       port["qqqi_usd"],
        "qqq_price":      qqq.get("current", 0),
        "drawdown_pct":   qqq.get("drawdown_pct", 0),
        "rsi":            rsi,
        "vix":            vix,
        "phase":          str(pk),
        "market_type":    mt,
    }

    if not dry_run:
        records.append(entry)
        # 최대 2년치(730일) 유지
        records = records[-730:]
        save_history(records)
        logger.info(f"히스토리 기록: {today}  ${port['total_usd']:,.2f}")

    return entry


# ══════════════════════════════════════════════════════════════════════
#  성과 계산
# ══════════════════════════════════════════════════════════════════════

def _find_ago(records: list, days: int) -> dict | None:
    """records에서 오늘로부터 약 days일 전 기록을 찾는다."""
    target = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    # 정확히 없으면 가장 가까운 과거 기록
    past = [r for r in records if r["date"] <= target]
    return past[-1] if past else None


def calc_performance(records: list) -> dict:
    """1d / 7d / 30d / 90d / 전체 수익률 계산."""
    if not records:
        return {}

    latest = records[-1]
    now_val = latest["total_usd"]

    def ret(old: dict | None) -> float | None:
        if old is None or old["total_usd"] <= 0:
            return None
        return (now_val / old["total_usd"] - 1) * 100

    first = records[0]
    d1  = _find_ago(records, 1)
    d7  = _find_ago(records, 7)
    d30 = _find_ago(records, 30)
    d90 = _find_ago(records, 90)

    # 역대 최고/최저
    all_vals = [r["total_usd"] for r in records]
    peak    = max(all_vals)
    trough  = min(all_vals)
    peak_date   = records[all_vals.index(peak)]["date"]
    trough_date = records[all_vals.index(trough)]["date"]
    drawdown_from_peak = (now_val / peak - 1) * 100

    return {
        "current":           round(now_val, 2),
        "current_krw":       latest.get("total_krw", int(now_val * 1380)),
        "ret_1d":            round(ret(d1),  2) if ret(d1)  is not None else None,
        "ret_7d":            round(ret(d7),  2) if ret(d7)  is not None else None,
        "ret_30d":           round(ret(d30), 2) if ret(d30) is not None else None,
        "ret_90d":           round(ret(d90), 2) if ret(d90) is not None else None,
        "ret_all":           round(ret(first), 2) if ret(first) is not None else None,
        "first_date":        first["date"],
        "first_val":         first["total_usd"],
        "peak":              round(peak, 2),
        "peak_date":         peak_date,
        "trough":            round(trough, 2),
        "trough_date":       trough_date,
        "drawdown_from_peak": round(drawdown_from_peak, 2),
        "n_days":            len(records),
    }


# ══════════════════════════════════════════════════════════════════════
#  QQQI 배당 기록
# ══════════════════════════════════════════════════════════════════════

def load_dividends() -> list:
    _ensure_dir()
    if not DIVIDEND_FILE.exists():
        return []
    try:
        return json.loads(DIVIDEND_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_dividends(records: list):
    _ensure_dir()
    DIVIDEND_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def record_dividend(amount_usd: float, reinvested_in: str, note: str = "") -> dict:
    """QQQI 배당 수령 기록."""
    records = load_dividends()
    today   = datetime.now().strftime("%Y-%m-%d")
    entry = {
        "date":             today,
        "amount_usd":       round(float(amount_usd), 2),
        "reinvested_in":    reinvested_in.upper(),
        "note":             note,
    }
    records.append(entry)
    save_dividends(records)
    logger.info(f"배당 기록: {today}  ${amount_usd:.2f} → {reinvested_in}")
    return entry


def get_dividend_summary() -> dict:
    """배당 수령 누적 통계."""
    records = load_dividends()
    if not records:
        return {"total": 0, "count": 0, "avg_monthly": 0, "records": []}

    total = sum(r["amount_usd"] for r in records)
    count = len(records)

    # 월 평균
    first_date = datetime.strptime(records[0]["date"], "%Y-%m-%d")
    months = max(1, (datetime.now() - first_date).days / 30)
    avg_monthly = total / months

    # 재투자 대상별 합계
    by_ticker: dict = {}
    for r in records:
        t = r["reinvested_in"]
        by_ticker[t] = by_ticker.get(t, 0) + r["amount_usd"]

    return {
        "total":       round(total, 2),
        "count":       count,
        "avg_monthly": round(avg_monthly, 2),
        "by_ticker":   {k: round(v, 2) for k, v in sorted(by_ticker.items(), key=lambda x: -x[1])},
        "records":     records,
    }


# ══════════════════════════════════════════════════════════════════════
#  리포트 생성
# ══════════════════════════════════════════════════════════════════════

def _ret_str(val: float | None) -> str:
    if val is None:
        return " N/A  "
    sign = "▲" if val > 0 else ("▼" if val < 0 else "─")
    return f"{sign}{abs(val):5.1f}%"


def build_performance_report(perf: dict, latest: dict) -> str:
    if not perf:
        return "⚠️ 히스토리 데이터 없음\n`python3 portfolio_tracker.py` 로 첫 기록 생성"

    mt  = latest.get("market_type", "neutral")
    pk  = latest.get("phase", "0")
    p_info = BULL_PHASES.get(pk) if mt == "bull" else BEAR_PHASES.get(int(pk) if str(pk).lstrip("-").isdigit() else 0)
    phase_label = p_info["label"] if p_info else f"{mt}/{pk}"
    phase_emoji = p_info["emoji"] if p_info else "❓"

    # 수익률 바 (1d 기준)
    ret1d = perf.get("ret_1d") or 0
    bar   = _bar(abs(ret1d) / 3, 8)  # 3% = 풀바

    lines = [
        f"📈 포트폴리오 성과  ({latest['date']})",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  현재가치  ${perf['current']:>9,.2f}  (₩{perf['current_krw']:,})",
        f"  {phase_emoji} {phase_label}",
        "",
        "━━━ 수익률 ━━━━━━━━━━━━━━━━━━━━━━━",
        f"  1일   {_ret_str(perf['ret_1d'])}  {bar}",
        f"  7일   {_ret_str(perf['ret_7d'])}",
        f"  30일  {_ret_str(perf['ret_30d'])}",
        f"  90일  {_ret_str(perf['ret_90d'])}",
        f"  전체  {_ret_str(perf['ret_all'])}  ({perf['first_date']} 이후)",
        "",
        "━━━ 포트폴리오 기록 ━━━━━━━━━━━━━━━━",
        f"  역대 최고  ${perf['peak']:>9,.2f}  ({perf['peak_date']})",
        f"  역대 최저  ${perf['trough']:>9,.2f}  ({perf['trough_date']})",
        f"  고점 대비  {perf['drawdown_from_peak']:>+.2f}%",
        f"  추적 일수  {perf['n_days']}일",
    ]

    # 배당 요약
    div_sum = get_dividend_summary()
    if div_sum["count"] > 0:
        lines += [
            "",
            "━━━ QQQI 배당 수령 기록 ━━━━━━━━━━━━",
            f"  누적 배당  ${div_sum['total']:,.2f}  ({div_sum['count']}회)",
            f"  월 평균    ${div_sum['avg_monthly']:.2f}",
        ]
        for ticker, amt in list(div_sum["by_ticker"].items())[:4]:
            lines.append(f"  → {ticker:<6}  ${amt:.2f}")

    return "\n".join(lines)


def build_benchmark_report(perf: dict, benchmarks: dict) -> str:
    lines = [
        "📊 벤치마크 비교",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  내 포트폴리오",
        f"    1일   {_ret_str(perf.get('ret_1d'))}",
        f"    7일   {_ret_str(perf.get('ret_7d'))}",
        f"    30일  {_ret_str(perf.get('ret_30d'))}",
        f"    90일  {_ret_str(perf.get('ret_90d'))}",
        f"    전체  {_ret_str(perf.get('ret_all'))}",
    ]

    for ticker, data in benchmarks.items():
        name = data.get("name", ticker)
        lines += [
            "",
            f"  {name}",
            f"    1일   {_ret_str(data.get('ret_1d'))}",
            f"    7일   {_ret_str(data.get('ret_7d'))}",
            f"    30일  {_ret_str(data.get('ret_30d'))}",
            f"    90일  {_ret_str(data.get('ret_90d'))}",
            f"    전체  {_ret_str(data.get('ret_all'))}",
        ]

    return "\n".join(lines)


def build_dividend_calendar(dividends: list, shares: float = 1.0) -> str:
    if not dividends:
        return (
            "📅 배당 캘린더\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  기록 없음"
        )

    items = sorted(dividends, key=lambda x: x.get("date", ""))
    dates = [datetime.strptime(item["date"], "%Y-%m-%d") for item in items if item.get("date")]
    intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates)) if (dates[i] - dates[i - 1]).days > 0]
    avg_interval = round(sum(intervals) / len(intervals)) if intervals else 30
    next_date = dates[-1] + timedelta(days=avg_interval)

    last_amount = items[-1].get("amount_usd", 0)
    est_payment = last_amount * shares

    lines = [
        "📅 배당 캘린더",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  보유 주수    {shares:g}주",
        f"  평균 간격    {avg_interval}일",
        f"  다음 예상    {next_date.strftime('%Y-%m-%d')}",
        f"  예상 배당    ${est_payment:.2f}",
        "",
        "  최근 배당:",
    ]
    for item in items[-5:]:
        lines.append(f"    {item['date']}  ${item.get('amount_usd', 0):.2f}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  텔레그램 발송
# ══════════════════════════════════════════════════════════════════════

def send_telegram(text: str):
    if not TELEGRAM_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[i:i+4000]}, timeout=10)
        except Exception as e:
            logger.error(f"텔레그램 오류: {e}")


# ══════════════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="포트폴리오 히스토리 추적")
    parser.add_argument("--report",   action="store_true", help="리포트만 출력 (기록 없음)")
    parser.add_argument("--send",     action="store_true", help="텔레그램 발송")
    parser.add_argument("--dividend", nargs="+",
                        metavar=("AMOUNT", "TICKER"),
                        help="배당 기록: --dividend 22.15 ORCL '5월 배당'")
    args = parser.parse_args()

    # 배당 기록 모드
    if args.dividend:
        if len(args.dividend) < 2:
            print("사용법: --dividend <금액> <재투자종목> [메모]")
            sys.exit(1)
        amount = float(args.dividend[0])
        ticker = args.dividend[1]
        note   = " ".join(args.dividend[2:]) if len(args.dividend) > 2 else ""
        entry  = record_dividend(amount, ticker, note)
        print(f"✅ 배당 기록 완료: ${entry['amount_usd']} → {entry['reinvested_in']}  ({entry['date']})")
        return

    # 스냅샷 기록 (--report가 아닐 때)
    dry_run = args.report
    latest  = record_daily(dry_run=dry_run)

    # 성과 계산
    records = load_history()
    perf    = calc_performance(records)

    report = build_performance_report(perf, latest)
    print(report)

    if args.send:
        send_telegram(report)
        logger.info("텔레그램 발송 완료")


if __name__ == "__main__":
    main()
