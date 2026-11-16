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

# ── 파일 경로 (레거시 — store 마이그레이션 원본) ──────────────────────
DATA_DIR      = Path(os.getenv("STOCK_REPORT_DATA_DIR") or (Path.home() / ".local" / "share" / "stock-report"))
HISTORY_FILE  = DATA_DIR / "portfolio_history.json"
DIVIDEND_FILE = DATA_DIR / "qqqi_dividends.json"

import store
import fmt

_HISTORY_COLLECTION  = "portfolio_history"
_DIVIDEND_COLLECTION = "qqqi_dividends"


# ══════════════════════════════════════════════════════════════════════
#  히스토리 저장/로드  (SQLite store 컬렉션)
# ══════════════════════════════════════════════════════════════════════

def load_history() -> list:
    return store.load_collection(_HISTORY_COLLECTION, HISTORY_FILE)


def save_history(records: list):
    store.replace_all(_HISTORY_COLLECTION, records)


def _suspicious_jump_pct(prev_usd: float | None, new_usd: float | None,
                          threshold: float = 0.25) -> float | None:
    """전일 대비 총액 급변 감지 — |변화율| > threshold(기본 25%) 면 %(부호 포함) 반환.

    2026-06-03~05 사이 미기록 현금 입출금(브로커 동기화 오류로 추정)이 TWR 그래프를
    영구 왜곡시킨 사고 재발 방지용 — portfolio_flows() 는 주식 매수/매도만 추적해
    이런 급변을 못 잡는다. 정상적인 일일 변동은 이 데이터셋 기준 10% 를 넘지 않았다.
    """
    if not prev_usd or prev_usd <= 0 or new_usd is None:
        return None
    pct = (new_usd / prev_usd - 1) * 100
    return pct if abs(pct) > threshold * 100 else None


def record_daily(dry_run: bool = False) -> dict:
    """오늘 포트폴리오 가치를 히스토리에 기록. dry_run=True면 저장 생략."""
    today = datetime.now().strftime("%Y-%m-%d")
    records = load_history()

    # 오늘 이미 기록됐으면 덮어쓰기 (하루 1회만 유효)
    records = [r for r in records if r.get("date") != today]
    prior = records[-1] if records else None

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

    jump = _suspicious_jump_pct((prior or {}).get("total_usd"), entry["total_usd"])
    if jump is not None:
        logger.warning(f"포트폴리오 총액 급변 감지: {jump:+.1f}% (전일 대비) — 데이터 오류 가능성")
        try:
            send_telegram(
                f"⚠️ 포트폴리오 총액이 전일 대비 {jump:+.1f}% 급변했습니다.\n"
                f"기록된 매수/매도로 설명 안 되면 브로커 동기화 오류일 수 있으니 확인해주세요.")
        except Exception:
            logger.warning("급변 알림 텔레그램 발송 실패 (기록은 계속 진행)")

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

def load_account() -> dict | None:
    """거래내역으로 다시 만든 계좌 궤적(reports.account_performance). 거래내역이나 시세가 없으면 None."""
    try:
        from reports import account_performance, report_pdf_data

        transactions = account_performance.load_transactions()
        if not transactions:
            return None
        symbols = {str(r.get("ticker") or "") for r in transactions} | {report_pdf_data.BENCHMARK}
        return account_performance.daily_account(transactions, report_pdf_data.fetch_price_history(sorted(symbols)))
    except Exception as exc:
        logger.warning(f"계좌 수익률 계산 실패: {type(exc).__name__}: {exc}")
        return None


def calc_performance(records: list, account: dict | None = None) -> dict:
    """현재 가치와 1d / 7d / 30d / 90d / 전체 수익률.

    수익률은 account(load_account) 의 시간가중 수익률이다. 히스토리의 총액 비율은 쓰지 않는다 —
    매수한 만큼 총액이 커져서 수익률이 아니다. account 가 없으면 수익률 칸은 비운다.
    """
    if not records:
        return {}
    from reports import account_performance

    latest = records[-1]
    now_val = latest["total_usd"]
    summary = account_performance.trailing_summary(account) if account else {}

    def rounded(key: str) -> float | None:
        return round(summary[key], 2) if summary.get(key) is not None else None

    return {
        "current":           round(now_val, 2),
        "current_krw":       latest.get("total_krw", int(now_val * 1380)),
        "ret_1d":            rounded("ret_1d"),
        "ret_7d":            rounded("ret_7d"),
        "ret_30d":           rounded("ret_30d"),
        "ret_90d":           rounded("ret_90d"),
        "ret_all":           rounded("ret_all"),
        "first_date":        summary.get("first_date"),
        "drawdown_from_peak": rounded("drawdown_from_peak"),
        "peak_date":         summary.get("peak_date"),
        "net_invested":      rounded("net_invested"),
        "income":            rounded("income"),
        "n_days":            len(records),
    }


# ══════════════════════════════════════════════════════════════════════
#  QQQI 배당 기록
# ══════════════════════════════════════════════════════════════════════

def load_dividends() -> list:
    return store.load_collection(_DIVIDEND_COLLECTION, DIVIDEND_FILE)


def save_dividends(records: list):
    store.replace_all(_DIVIDEND_COLLECTION, records)


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
    return fmt.spct(val)   # ▲1.5% / ▼0.5% / ─ (공통 포맷)


def build_performance_report(perf: dict, latest: dict,
                             value_series: list | None = None, html: bool = False) -> str:
    if not perf:
        return "⚠️ 히스토리 데이터 없음\n`python3 portfolio_tracker.py` 로 첫 기록 생성"

    mt  = latest.get("market_type", "neutral")
    pk  = latest.get("phase", "0")
    p_info = BULL_PHASES.get(pk) if mt == "bull" else BEAR_PHASES.get(int(pk) if str(pk).lstrip("-").isdigit() else 0)
    phase_label = p_info["label"] if p_info else f"{mt}/{pk}"
    phase_emoji = p_info["emoji"] if p_info else "❓"
    _B = fmt.b if html else (lambda x: x)     # html=True 일 때만 굵게(텔레그램), 크론은 평문

    lines = [
        # 헤드라인 — 전체 수익률 + 고점대비(MDD) 먼저 (모바일 첫 화면)
        fmt.headline(f"📈 성과 {_B(fmt.spct(perf.get('ret_all')))}(전체)",
                     f"고점 {fmt.pct(perf['drawdown_from_peak'])}" if perf["drawdown_from_peak"] is not None else "",
                     f"{phase_emoji} {phase_label}"),
    ]
    sl = fmt.spark(value_series) if value_series else ""
    if sl:
        lines.append(f"추이 {sl}")           # 스파크라인(최근 가치 흐름)
    lines += [
        fmt.sep(),
        f"현재가치 {_B(fmt.money(perf['current'], digits=2))} ({fmt.money(perf['current_krw'], '₩', abbrev=True)})",
        fmt.sep("수익률 (입출금 제외)"),
        f"1일   {_ret_str(perf['ret_1d'])}",
        f"7일   {_ret_str(perf['ret_7d'])}",
        f"30일  {_ret_str(perf['ret_30d'])}",
        f"90일  {_ret_str(perf['ret_90d'])}",
        f"전체  {_ret_str(perf['ret_all'])}" + (f"  ({perf['first_date']} 이후)" if perf["first_date"] else ""),
        fmt.sep("기록"),
    ]
    if perf["ret_all"] is None:
        lines.append("거래내역이나 시세가 없어 수익률을 계산하지 못했습니다")
    else:
        lines += [
            f"누적 순투입 {fmt.money(perf['net_invested'], digits=2)} (매수 − 매도)",
            f"누적 배당 {fmt.money(perf['income'], digits=2)} (원천징수 후 추정)",
            f"고점 대비 {fmt.pct(perf['drawdown_from_peak'], 2)} (수익률 기준, 고점 {perf['peak_date']})",
        ]
    lines.append(f"추적 일수 {perf['n_days']}일")

    # 배당 요약
    div_sum = get_dividend_summary()
    if div_sum["count"] > 0:
        lines += [
            fmt.sep("QQQI 배당 수령"),
            f"누적 {fmt.money(div_sum['total'], digits=2)} ({div_sum['count']}회)",
            f"월 평균 {fmt.money(div_sum['avg_monthly'], digits=2)}",
        ]
        for ticker, amt in list(div_sum["by_ticker"].items())[:4]:
            lines.append(f"  → {fmt.name(ticker)} {fmt.money(amt, digits=2)}")

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
    """notify 단일 진실원에 위임 (4096 분할·토큰 마스킹 공통)."""
    import notify
    notify.send_telegram(text, token=TELEGRAM_TOKEN, chat_id=TELEGRAM_CHAT_ID)


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
    perf    = calc_performance(records, load_account())

    report = build_performance_report(perf, latest)
    print(report)

    if args.send:
        send_telegram(report)
        logger.info("텔레그램 발송 완료")


if __name__ == "__main__":
    main()
