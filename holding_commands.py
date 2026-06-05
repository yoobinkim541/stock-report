"""Holdings-related Telegram bot commands."""
import json
import shutil
import logging
from datetime import datetime, timedelta

from barbell_strategy import fetch_exchange_rate, fetch_portfolio_value, PORTFOLIO_PATH
from holding_manager import (
    list_holdings, buy_holding, sell_holding,
    show_dca_weights, set_dca_weights,
    refresh_portfolio_prices, set_target_weight, show_target_weights,
)
from portfolio_tracker import get_dividend_summary, record_dividend
from attachment_parser import (
    load_pending_snapshot, clear_pending_snapshot, build_pending_snapshot_summary,
)

logger = logging.getLogger(__name__)


def cmd_dividend(chat_id: str, args: list, send_fn):
    """QQQI 배당 기록 및 누적 통계."""
    if not args:
        # 통계 조회
        summary = get_dividend_summary()
        if summary["count"] == 0:
            send_fn(chat_id,
                    "💰 QQQI 배당 기록 없음\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "배당 수령 시 기록:\n"
                    "/dividend 22.15 ORCL 5월배당\n\n"
                    "형식: /dividend <금액> <재투자종목> [메모]")
            return

        lines = [
            "💰 QQQI 배당 기록",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"  누적 배당  ${summary['total']:,.2f}  ({summary['count']}회)",
            f"  월 평균    ${summary['avg_monthly']:.2f}",
            "",
            "  재투자 대상별:",
        ]
        for ticker, amt in summary["by_ticker"].items():
            lines.append(f"    {ticker:<6}  ${amt:.2f}")

        lines += ["", "  최근 기록:"]
        for r in summary["records"][-5:]:
            lines.append(f"  {r['date']}  ${r['amount_usd']:.2f} → {r['reinvested_in']}  {r.get('note','')}")

        # 다음 배당 예상일 추가
        records = summary["records"]
        if len(records) >= 2:
            try:
                rdates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in records if r.get("date")]
                if len(rdates) >= 2:
                    intervals = [(rdates[i] - rdates[i-1]).days for i in range(1, len(rdates)) if (rdates[i] - rdates[i-1]).days > 0]
                    if intervals:
                        avg_iv = round(sum(intervals) / len(intervals))
                        next_dt = rdates[-1] + timedelta(days=avg_iv)
                        try:
                            with open(PORTFOLIO_PATH, encoding="utf-8") as _pf:
                                _snap = json.load(_pf)
                            qqqi_sh = next(
                                (h.get("shares", 1.0) for h in _snap.get("overseas_fractional", {}).get("holdings", [])
                                 if h.get("ticker") == "QQQI"),
                                1.0
                            )
                        except Exception as _e:
                            logger.debug("QQQI 보유수량 조회 실패: %s", _e)
                            qqqi_sh = 1.0
                        est_pay = records[-1].get("amount_usd", 0) * qqqi_sh
                        lines += [
                            "",
                            "  📅 다음 배당 예상:",
                            f"    {next_dt.strftime('%Y-%m-%d')}  ≈ ${est_pay:.2f}  (간격 {avg_iv}일)",
                        ]
            except Exception as _e:
                logger.debug("다음 배당 예상일 계산 실패: %s", _e)

        send_fn(chat_id, "\n".join(lines))
        return

    # 기록 모드: /dividend 22.15 ORCL 메모
    if len(args) < 2:
        send_fn(chat_id, "사용법: /dividend <금액> <재투자종목> [메모]\n예: /dividend 22.15 ORCL 5월배당")
        return
    try:
        amount = float(args[0])
    except ValueError:
        send_fn(chat_id, f"❌ 금액 오류: {args[0]}")
        return
    ticker = args[1].upper()
    note = " ".join(args[2:]) if len(args) > 2 else ""
    entry = record_dividend(amount, ticker, note)
    send_fn(chat_id,
            f"✅ 배당 기록 완료\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  날짜    {entry['date']}\n"
            f"  금액    ${entry['amount_usd']:.2f}\n"
            f"  재투자  {entry['reinvested_in']}\n"
            f"  메모    {entry.get('note','─')}")


def _holding_buy(chat_id: str, args: list, send_fn):
    if len(args) < 4:
        send_fn(chat_id,
                "사용법:\n"
                "/holding buy TICKER 주수 평단가\n"
                "/holding buy TICKER 주수 평단가 frac  ← 소수점 계좌\n\n"
                "예시:\n"
                "/holding buy ORCL 2 200.50\n"
                "/holding buy NOW 0.5 120.30 frac")
        return
    try:
        ticker = args[1].upper()
        shares = float(args[2])
        price = float(args[3])
        frac = len(args) > 4 and args[4].lower() == "frac"
    except (ValueError, IndexError):
        send_fn(chat_id, "❌ 형식 오류: /holding buy TICKER 주수 평단가")
        return
    result = buy_holding(ticker, shares, price, fractional=frac)
    send_fn(chat_id, result)


def _holding_target(chat_id: str, args: list, send_fn):
    remaining = args[1:]
    if not remaining:
        # 목표 비중 현황 — fetch_portfolio_value() directly (no circular import)
        try:
            port = fetch_portfolio_value()
        except Exception:
            port = None
        send_fn(chat_id, show_target_weights(port))
        return

    # /holding target TICKER 비중% TICKER 비중% ...
    if len(remaining) % 2 != 0:
        send_fn(chat_id,
                "사용법: /holding target TICKER 비중% TICKER 비중% ...\n\n"
                "예시:\n"
                "/holding target AMD 5 AMZN 4 PLTR 3\n"
                "/holding target ORCL 7 UNH 5\n\n"
                "• 기존 목표와 병합됩니다 (삭제: 0 입력)")
        return

    updates = {}
    try:
        for i in range(0, len(remaining), 2):
            t = remaining[i].upper()
            w = float(remaining[i + 1])
            updates[t] = w
    except (ValueError, IndexError):
        send_fn(chat_id, "❌ 형식 오류: TICKER와 비중%를 번갈아 입력")
        return

    result = set_target_weight(updates)
    send_fn(chat_id, result)


def _holding_refresh(chat_id: str, args: list, send_fn):
    send_fn(chat_id, "⏳ 전 종목 현재가 갱신 중...")
    result = refresh_portfolio_prices()
    send_fn(chat_id, result)


def _holding_sell(chat_id: str, args: list, send_fn):
    if len(args) < 2:
        send_fn(chat_id, "사용법: /holding sell TICKER [주수]\n전량 청산 시 주수 생략")
        return
    ticker = args[1].upper()
    shares = float(args[2]) if len(args) > 2 else None
    result = sell_holding(ticker, shares)
    send_fn(chat_id, result)


def _holding_dca(chat_id: str, args: list, send_fn):
    remaining = args[1:]

    if not remaining:
        send_fn(chat_id, show_dca_weights())
        return

    mode = "normal"
    if remaining and remaining[0].lower() == "bear":
        mode = "bear"
        remaining = remaining[1:]

    if len(remaining) % 2 != 0:
        send_fn(chat_id,
                "사용법:\n"
                "/holding dca TICKER 비중% TICKER 비중% ...\n"
                "/holding dca bear TICKER 비중% ...  ← 하락장 비중\n\n"
                "예시:\n"
                "/holding dca NOW 18 ORCL 18 CRM 10 NVDA 14 MSFT 14 GOOGL 10 UNH 10 SAP 3 SPMO 3\n"
                "(비중 합계가 100%가 아니어도 자동 정규화)")
        return

    updates = {}
    try:
        for i in range(0, len(remaining), 2):
            updates[remaining[i].upper()] = float(remaining[i + 1])
    except (ValueError, IndexError):
        send_fn(chat_id, "❌ 형식 오류: TICKER와 비중(%)을 번갈아 입력")
        return

    result = set_dca_weights(updates, mode=mode)
    send_fn(chat_id, result)


def _holding_dividend(chat_id: str, args: list, send_fn):
    cmd_dividend(chat_id, args[1:], send_fn)


def _holding_apply(chat_id: str, args: list, send_fn):
    cmd_apply_snapshot(chat_id, send_fn)


_HOLDING_HANDLERS = {
    "buy": _holding_buy,
    "target": _holding_target,
    "refresh": _holding_refresh,
    "sell": _holding_sell,
    "dca": _holding_dca,
    "dividend": _holding_dividend,
    "apply": _holding_apply,
}


def cmd_holding(chat_id: str, args: list, send_fn):
    """
    /holding                              → 보유 종목 목록
    /holding buy TICKER 주수 평단가        → 매수 기록 + 가격 자동 갱신
    /holding buy TICKER 주수 평단가 frac  → 소수점 계좌 매수
    /holding sell TICKER [주수]            → 매도 기록 (주수 생략 시 전량)
    /holding target                       → 목표 비중 현황
    /holding target TICKER 비중% ...      → 목표 비중 설정/변경
    /holding dca                          → DCA 비중 현황
    /holding dca NOW 18 ORCL 18 CRM 10   → DCA 비중 변경
    /holding dca bear NOW 23 ...          → 하락장 DCA 비중 변경
    /holding refresh                      → 전 종목 현재가 갱신
    /holding dividend [금액 TICKER [메모]] → QQQI 배당 조회/기록
    /holding apply                        → 파싱된 스냅샷 반영
    """
    if not args:
        send_fn(chat_id, list_holdings())
        return

    fn = _HOLDING_HANDLERS.get(args[0].lower())
    if fn is None:
        send_fn(chat_id, list_holdings())
        return
    fn(chat_id, args, send_fn)


def cmd_apply_snapshot(chat_id: str, send_fn):
    """pending_snapshot.json 을 portfolio_snapshot.json 에 반영."""
    pending = load_pending_snapshot()
    if not pending:
        send_fn(chat_id,
                "❌ 적용할 스냅샷 없음\n\n"
                "계좌현황 스크린샷이나 PDF를 전송하면\n"
                "파싱 후 대기 파일이 생성됩니다.")
        return

    holdings = pending.get("holdings", [])
    if not holdings:
        send_fn(chat_id, "❌ 파싱된 보유 종목이 없습니다.")
        return

    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        send_fn(chat_id, f"❌ portfolio_snapshot.json 로드 실패: {e}")
        return

    # 백업
    backup_path = PORTFOLIO_PATH + ".bak"
    shutil.copy2(PORTFOLIO_PATH, backup_path)

    existing = {h["ticker"]: h for h in snap.get("overseas_general", {}).get("holdings_usd", [])}
    changes: list[str] = []

    for h in holdings:
        ticker = h["ticker"]
        cost_usd = round(h["shares"] * h["avg_price_usd"], 4)
        value_usd = round(h.get("value_usd", h["shares"] * h["current_price_usd"]), 4)
        pnl_usd = round(value_usd - cost_usd, 4)
        return_pct = round((pnl_usd / cost_usd) * 100, 2) if cost_usd else 0.0

        if ticker in existing:
            old_sh = existing[ticker].get("shares", 0)
            old_avg = existing[ticker].get("avg_price_usd", 0)
            existing[ticker]["name"] = h["name"]
            existing[ticker]["shares"] = h["shares"]
            existing[ticker]["avg_price_usd"] = h["avg_price_usd"]
            existing[ticker]["current_price_usd"] = h["current_price_usd"]
            existing[ticker]["cost_usd"] = cost_usd
            existing[ticker]["value_usd"] = value_usd
            existing[ticker]["pnl_usd"] = pnl_usd
            existing[ticker]["return_pct"] = return_pct
            changes.append(
                f"  {ticker} ({h['name']})\n"
                f"    수량: {old_sh} → {h['shares']}주\n"
                f"    평단: ${old_avg:.2f} → ${h['avg_price_usd']:.2f}"
            )
        else:
            new_entry = {
                "ticker": ticker,
                "name": h["name"],
                "shares": h["shares"],
                "avg_price_usd": h["avg_price_usd"],
                "current_price_usd": h["current_price_usd"],
                "cost_usd": cost_usd,
                "value_usd": value_usd,
                "pnl_usd": pnl_usd,
                "return_pct": return_pct,
            }
            snap.setdefault("overseas_general", {}).setdefault("holdings_usd", []).append(new_entry)
            existing[ticker] = new_entry
            changes.append(f"  {ticker} ({h['name']}) — 신규 추가  {h['shares']}주 @${h['avg_price_usd']:.2f}")

    snap["overseas_general"]["holdings_usd"] = list(existing.values())
    snap["snapshot_date"] = datetime.now().strftime("%Y-%m-%d")

    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)

    clear_pending_snapshot()

    lines = [
        "✅ 포트폴리오 스냅샷 반영 완료",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ] + changes + [
        "",
        "백업: portfolio_snapshot.json.bak",
        "/portfolio 로 확인",
    ]
    send_fn(chat_id, "\n".join(lines))