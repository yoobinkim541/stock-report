#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_bot.py — Intelligence Barbell 양방향 텔레그램 봇

Commands:
  /help      — 명령어 목록
  /status    — Phase + 핵심 수치 (빠른 조회, 캐시 5분)
  /phase     — Phase 미터 + 행동 지침
  /portfolio — 포트폴리오 실시간 현황
  /dca       — 오늘 DCA 배분
  /sgov      — SGOV 실탄 상태
  /report    — 전체 바벨 리포트 (항상 실시간)
  /alert     — 가격 알림 관리 (add / list / remove)

보안: ALLOWED_CHAT_ID 만 응답
      5분마다 가격 알림 자동 체크
"""

import os
import sys
import time
import json
import logging
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from barbell_strategy import (
    fetch_qqq_data, fetch_rsi, fetch_vix, fetch_ma200,
    fetch_exchange_rate, fetch_portfolio_value,
    estimate_qqqi_monthly_dividend,
    classify_market, calculate_dca, calculate_sgov_target,
    calculate_rebalancing, build_smart_report,
    build_report, build_simulation_report, load_leverage_state, load_phase_state,
    _phase_meter, _bar, _sgov_compare, _dca_rows,
    BULL_PHASES, BEAR_PHASES,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
)
from price_alerts import load_alerts, add_alert, remove_alert, check_alerts
from portfolio_tracker import (
    record_daily, load_history, calc_performance,
    build_performance_report, build_benchmark_report, build_dividend_calendar,
    record_dividend, get_dividend_summary,
)
from order_generator import generate as generate_order
from holding_manager import (
    list_holdings, buy_holding, sell_holding,
    show_dca_weights, set_dca_weights,
    refresh_portfolio_prices, set_target_weight, show_target_weights,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

ALLOWED_CHAT_ID    = TELEGRAM_CHAT_ID
POLL_TIMEOUT       = 20    # long-polling 대기(초)
RETRY_DELAY        = 10    # 오류 후 재시도 대기(초)
CACHE_TTL          = 300   # 시장 데이터 캐시 유지(초, 5분)
ALERT_CHECK_SECS   = 300   # 가격 알림 체크 주기(초)
PID_FILE           = "/tmp/barbell_bot.pid"

# ══════════════════════════════════════════════════════════════════════
#  시장 데이터 캐시
# ══════════════════════════════════════════════════════════════════════

_cache: dict = {}


def fetch_market(force: bool = False) -> dict:
    """모든 시장 데이터 일괄 조회. CACHE_TTL 동안 재사용."""
    now = time.time()
    if not force and "data" in _cache and now - _cache.get("ts", 0) < CACHE_TTL:
        return _cache["data"]

    qqq  = fetch_qqq_data()
    rsi  = fetch_rsi("QQQ")
    vix  = fetch_vix()
    ma   = fetch_ma200("QQQ")
    fx   = fetch_exchange_rate()
    port = fetch_portfolio_value()
    div  = estimate_qqqi_monthly_dividend(port["qqqi_shares"], port["qqqi_usd"])
    market_type, phase_key = classify_market(qqq, rsi, vix)

    data = {
        "qqq": qqq, "rsi": rsi, "vix": vix, "ma": ma,
        "exchange_rate": fx, "portfolio": port, "qqqi_div": div,
        "market_type": market_type, "phase_key": phase_key,
        "fetched_at": datetime.now().strftime("%m/%d %H:%M"),
    }
    _cache["data"] = data
    _cache["ts"]   = now
    return data


# ══════════════════════════════════════════════════════════════════════
#  Telegram API
# ══════════════════════════════════════════════════════════════════════

def _api(method: str, **kwargs) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    try:
        r = requests.post(url, json=kwargs, timeout=15)
        return r.json() if r.ok else {}
    except Exception as e:
        logger.error(f"API {method}: {e}")
        return {}


def send(chat_id: str, text: str):
    """4000자 초과 시 자동 분할 전송."""
    for i in range(0, len(text), 4000):
        _api("sendMessage", chat_id=chat_id, text=text[i:i + 4000])


def typing(chat_id: str):
    _api("sendChatAction", chat_id=chat_id, action="typing")


def get_updates(offset: int | None = None) -> list:
    params: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    return _api("getUpdates", **params).get("result", [])


# ══════════════════════════════════════════════════════════════════════
#  명령어 핸들러
# ══════════════════════════════════════════════════════════════════════

def cmd_help() -> str:
    return (
        "🏋️ Intelligence Barbell Bot\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "/status      빠른 현황 (Phase + 핵심 수치)\n"
        "/phase       Phase 미터 + 행동 지침\n"
        "/portfolio   포트폴리오 실시간 현황\n"
        "/dca         오늘 DCA 배분\n"
        "/order       소수점 매수 주문서 (키움 즉시 입력)\n"
        "/tax         양도소득세 추산 (전량 매도 시 22%)\n"
        "/sgov        SGOV 실탄 상태\n"
        "/history     성과 히스토리 (1d/7d/30d/90d)\n"
        "/rebalance   리밸런싱 계산기\n"
        "/sim         시뮬레이션 리포트\n"
        "/dividend    QQQI 배당 기록\n"
        "/holding     보유 종목 조회/매수·매도/목표비중/DCA 비중\n"
        "/report      전체 바벨 리포트 (실시간)\n"
        "/alert       가격 알림 관리\n"
        "/help        이 메시지\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"캐시: {CACHE_TTL // 60}분  ·  /report·/order는 항상 실시간"
    )


def cmd_status(d: dict) -> str:
    qqq  = d["qqq"]
    mt   = d["market_type"]
    pk   = d["phase_key"]
    info = BULL_PHASES[pk] if mt == "bull" else BEAR_PHASES[pk]
    port = d["portfolio"]
    rsi  = d["rsi"]
    vix  = d["vix"]
    dd   = qqq.get("drawdown_pct", 0)
    total_krw = int(port["total_usd"] * d["exchange_rate"])

    rsi_s = ("🔥과매도" if rsi < 30 else "⚠️약세"     if rsi < 40
             else "🫧극과매수" if rsi > 75 else "🌡과매수" if rsi > 70
             else "✅중립")
    vix_s = ("💥극공포" if vix > 40 else "🚨공포"   if vix > 30
             else "😴과낙관" if vix < 15 else "✅정상")

    return (
        f"📊 현재 상태  ({d['fetched_at']})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{info['emoji']} {info['label']}\n"
        f"\n"
        f"QQQ   ${qqq.get('current', 0):>8,.2f}\n"
        f"낙폭  {dd:>+8.2f}%\n"
        f"RSI   {rsi:>8.1f}   {rsi_s}\n"
        f"VIX   {vix:>8.1f}   {vix_s}\n"
        f"\n"
        f"총액  ${port['total_usd']:>8,.0f}  (₩{total_krw:,})\n"
        f"SGOV  ${port['sgov_usd']:>8,.0f}  실탄 대기중"
    )


def cmd_phase(d: dict) -> str:
    mt   = d["market_type"]
    pk   = d["phase_key"]
    info = BULL_PHASES[pk] if mt == "bull" else BEAR_PHASES[pk]
    dd   = d["qqq"].get("drawdown_pct", 0)

    lines = [
        f"📍 {info['emoji']} {info['label']}",
        _phase_meter(mt, pk),
        f"  QQQ {dd:+.2f}%  ·  {info['description']}",
        "",
        "📋 행동 지침",
    ]
    for i, act in enumerate(info["action_items"], 1):
        lines.append(f"  {i}. {act}")
    return "\n".join(lines)


def cmd_portfolio(d: dict) -> str:
    port = d["portfolio"]
    fx   = d["exchange_rate"]
    total_krw = int(port["total_usd"] * fx)
    sgov_r = port["sgov_usd"] / port["total_usd"] if port["total_usd"] > 0 else 0
    qqqi_r = port["qqqi_usd"] / port["total_usd"] if port["total_usd"] > 0 else 0

    lines = [
        f"💼 포트폴리오  ({d['fetched_at']})",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  총액  ${port['total_usd']:>8,.2f}  (₩{total_krw:,})",
        f"  환율  {fx:,.1f}원/USD",
        f"  SGOV  ${port['sgov_usd']:>7,.2f}  {_bar(sgov_r, 10)}  {sgov_r*100:.1f}%  실탄",
        f"  QQQI  ${port['qqqi_usd']:>7,.2f}  {_bar(min(qqqi_r/0.35, 1), 10)}  {qqqi_r*100:.1f}%  배당엔진",
    ]

    leverage   = load_leverage_state()
    lev_prices = port.get("prices", {})
    has_lev    = False
    for ticker, pos in leverage.items():
        sh = pos.get("shares", 0)
        if sh > 0:
            has_lev = True
            avg   = pos.get("avg_price_usd", 0)
            price = lev_prices.get(ticker, avg)
            val   = sh * price
            pnl   = (price - avg) / avg * 100 if avg > 0 else 0
            sign  = "+" if pnl >= 0 else ""
            lines.append(f"  {ticker}    ${val:>7,.0f}  {sh}주 @${avg:.2f}  {sign}{pnl:.1f}%")
    if not has_lev:
        lines.append("  레버리지  미보유")

    div = d["qqqi_div"]
    lines += [
        "",
        f"  QQQI 월 배당  ${div['monthly_usd']:.2f}  (연 {div['annual_yield_pct']:.1f}%)",
    ]
    return "\n".join(lines)


def cmd_dca(d: dict) -> str:
    dca = calculate_dca(d["market_type"], d["phase_key"], d["exchange_rate"])
    lines = [
        f"💸 오늘 DCA  {dca['total_krw']:,}원  (${dca['total_usd']:.2f})  [{dca['multiplier']}x]",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ] + _dca_rows(dca["by_ticker"], dca["total_krw"], d["exchange_rate"]) + [
        "",
        "📋 키움 소수점 매수 주문서: /order",
    ]
    return "\n".join(lines)


def cmd_order(chat_id: str):
    """소수점 매수 주문서 — 키움증권 해외주식 > 소수점 매수 화면에서 즉시 입력."""
    send(chat_id, "⏳ 주문서 생성 중...")
    typing(chat_id)
    try:
        report = generate_order()
        send(chat_id, report)
    except Exception as e:
        send(chat_id, f"❌ 주문서 생성 오류: {e}")
        logger.exception("cmd_order")


def cmd_sgov(d: dict) -> str:
    port = d["portfolio"]
    sgov = calculate_sgov_target(
        d["market_type"], d["phase_key"], port["total_usd"], port["sgov_usd"]
    )
    lines = [
        f"🛡 SGOV 실탄  ({d['fetched_at']})",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ] + _sgov_compare(sgov["current_usd"], sgov["target_usd"]) + [
        f"  목표 {sgov['target_pct']}%  ·  차이 ${sgov['diff_usd']:+,.0f}",
        f"  → {sgov['action']}",
    ]
    return "\n".join(lines)


def cmd_history(d: dict) -> str:
    """포트폴리오 성과 히스토리 — portfolio_tracker 데이터 기반."""
    records = load_history()
    if not records:
        return (
            "⚠️ 히스토리 없음\n"
            "크론에 등록되면 매일 자동 기록됩니다.\n"
            "지금 당장 기록하려면:\n"
            "  python3 portfolio_tracker.py"
        )
    latest = records[-1]
    perf   = calc_performance(records)
    return build_performance_report(perf, latest)


def cmd_rebalance(d: dict) -> str:
    """스마트 리밸런싱 — 안전마진 + 종목 비중 + DCA 조정."""
    return build_smart_report(
        d["portfolio"], d["market_type"], d["phase_key"], d["exchange_rate"]
    )


def cmd_sim(chat_id: str, args: list):
    mode = args[0] if args else "bull2"
    send(chat_id, build_simulation_report(mode))


def cmd_dividend(chat_id: str, args: list):
    """QQQI 배당 기록 및 누적 통계."""
    if not args:
        # 통계 조회
        summary = get_dividend_summary()
        if summary["count"] == 0:
            send(chat_id,
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

        send(chat_id, "\n".join(lines))
        return

    # 기록 모드: /dividend 22.15 ORCL 메모
    if len(args) < 2:
        send(chat_id, "사용법: /dividend <금액> <재투자종목> [메모]\n예: /dividend 22.15 ORCL 5월배당")
        return
    try:
        amount = float(args[0])
    except ValueError:
        send(chat_id, f"❌ 금액 오류: {args[0]}")
        return
    ticker = args[1].upper()
    note   = " ".join(args[2:]) if len(args) > 2 else ""
    entry  = record_dividend(amount, ticker, note)
    send(chat_id,
         f"✅ 배당 기록 완료\n"
         f"━━━━━━━━━━━━━━━━━━━━━━━\n"
         f"  날짜    {entry['date']}\n"
         f"  금액    ${entry['amount_usd']:.2f}\n"
         f"  재투자  {entry['reinvested_in']}\n"
         f"  메모    {entry.get('note','─')}")


def cmd_holding(chat_id: str, args: list):
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
    """
    if not args:
        send(chat_id, list_holdings())
        return

    sub = args[0].lower()

    # ── /holding buy ────────────────────────────────────────────────
    if sub == "buy":
        # /holding buy ORCL 2 200.50 [frac]
        if len(args) < 4:
            send(chat_id,
                 "사용법:\n"
                 "/holding buy TICKER 주수 평단가\n"
                 "/holding buy TICKER 주수 평단가 frac  ← 소수점 계좌\n\n"
                 "예시:\n"
                 "/holding buy ORCL 2 200.50\n"
                 "/holding buy NOW 0.5 120.30 frac")
            return
        try:
            ticker  = args[1].upper()
            shares  = float(args[2])
            price   = float(args[3])
            frac    = len(args) > 4 and args[4].lower() == "frac"
        except (ValueError, IndexError):
            send(chat_id, "❌ 형식 오류: /holding buy TICKER 주수 평단가")
            return
        result = buy_holding(ticker, shares, price, fractional=frac)
        send(chat_id, result)

    # ── /holding target ──────────────────────────────────────────────
    elif sub == "target":
        remaining = args[1:]
        if not remaining:
            # 목표 비중 현황 — 현재 포트폴리오 기반 자동 추론 포함
            try:
                port = fetch_market()["portfolio"]
            except Exception:
                port = None
            send(chat_id, show_target_weights(port))
            return

        # /holding target TICKER 비중% TICKER 비중% ...
        if len(remaining) % 2 != 0:
            send(chat_id,
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
            send(chat_id, "❌ 형식 오류: TICKER와 비중%를 번갈아 입력")
            return

        result = set_target_weight(updates)
        send(chat_id, result)

    # ── /holding refresh ─────────────────────────────────────────────
    elif sub == "refresh":
        send(chat_id, "⏳ 전 종목 현재가 갱신 중...")
        result = refresh_portfolio_prices()
        send(chat_id, result)

    # ── /holding sell ────────────────────────────────────────────────
    elif sub == "sell":
        # /holding sell CPNG [주수]
        if len(args) < 2:
            send(chat_id, "사용법: /holding sell TICKER [주수]\n전량 청산 시 주수 생략")
            return
        ticker = args[1].upper()
        shares = float(args[2]) if len(args) > 2 else None
        result = sell_holding(ticker, shares)
        send(chat_id, result)

    # ── /holding dca ────────────────────────────────────────────────
    elif sub == "dca":
        remaining = args[1:]

        # 값 없으면 현황 표시
        if not remaining:
            send(chat_id, show_dca_weights())
            return

        # 모드 추출 (bear 키워드)
        mode = "normal"
        if remaining and remaining[0].lower() == "bear":
            mode = "bear"
            remaining = remaining[1:]

        # 짝수 아니면 오류
        if len(remaining) % 2 != 0:
            send(chat_id,
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
            send(chat_id, "❌ 형식 오류: TICKER와 비중(%)을 번갈아 입력")
            return

        result = set_dca_weights(updates, mode=mode)
        send(chat_id, result)

    else:
        # args[0]이 sub-command 없이 바로 list
        send(chat_id, list_holdings())


def cmd_tax(chat_id: str):
    """해외주식 양도소득세 추산 — 매도 시 예상 세금 계산."""
    send(chat_id, "⏳ 세금 추산 중 (실시간 가격 조회)...")
    typing(chat_id)
    try:
        import yfinance as yf
        snap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_snapshot.json")
        with open(snap_path, encoding="utf-8") as f:
            snap = json.load(f)

        fx = fetch_exchange_rate()

        # 전체 보유 종목 수집 (해외만 — 국내는 다른 세율 적용)
        holdings: list[dict] = []
        for h in snap.get("overseas_general", {}).get("holdings_usd", []):
            shares = h.get("shares", 0)
            if shares <= 0:
                continue
            # avg 우선순위: avg_price_usd > cost_total_usd/shares > cost_usd/shares
            avg = h.get("avg_price_usd")
            if not avg:
                cost_total = h.get("cost_total_usd")
                if cost_total:
                    avg = cost_total / shares
            if not avg:
                cost = h.get("cost_usd", 0)
                if cost:
                    avg = cost / shares
            if avg and avg > 0:
                holdings.append({"ticker": h["ticker"], "avg": avg, "shares": shares, "name": h.get("name", h["ticker"])})
        for h in snap.get("overseas_fractional", {}).get("holdings", []):
            cost = h.get("cost_usd", 0)
            shares = h.get("shares", 0)
            if shares > 0 and cost > 0:
                avg = cost / shares
                holdings.append({"ticker": h["ticker"], "avg": avg, "shares": shares, "name": h.get("name", h["ticker"])})

        if not holdings:
            send(chat_id, "⚠️ 보유 종목 데이터 없음 (portfolio_snapshot.json 확인)")
            return

        # 현재 가격 일괄 조회
        tickers_set = list({h["ticker"] for h in holdings})
        try:
            raw = yf.download(tickers_set, period="2d", progress=False, auto_adjust=True)
            if len(tickers_set) == 1:
                prices = {tickers_set[0]: float(raw["Close"].iloc[-1])}
            else:
                prices = {t: float(raw["Close"][t].dropna().iloc[-1]) for t in tickers_set if t in raw["Close"]}
        except Exception:
            prices = {}

        EXEMPTION_KRW = 2_500_000  # 연간 기본공제 250만원
        TAX_RATE = 0.22            # 22% (소득세 20% + 지방세 2.2%)

        rows = []
        for h in holdings:
            t = h["ticker"]
            cur = prices.get(t)
            avg = h["avg"]
            shares = h["shares"]
            if cur is None:
                rows.append((t, h["name"], shares, avg, None, None, None))
                continue
            gain_usd = (cur - avg) * shares
            gain_krw = gain_usd * fx
            rows.append((t, h["name"], shares, avg, cur, gain_usd, gain_krw))

        # 손익 합산
        valid_rows = [(t, n, s, a, c, gu, gk) for t, n, s, a, c, gu, gk in rows if gu is not None]
        total_gain_krw = sum(r[6] for r in valid_rows)
        taxable_krw = max(0.0, total_gain_krw - EXEMPTION_KRW)
        tax_krw = taxable_krw * TAX_RATE
        tax_usd = tax_krw / fx if fx > 0 else 0

        SEP = "─" * 52
        lines = [
            "💸 해외주식 양도소득세 추산",
            f"📅 {datetime.now().strftime('%Y-%m-%d')}  (@{fx:,.0f}원)",
            SEP,
            f"{'종목':<18} {'수량':>7} {'평단':>8} {'현재':>8} {'손익(USD)':>10}",
            SEP,
        ]

        gain_rows = sorted(valid_rows, key=lambda r: r[5], reverse=True)
        for t, n, s, avg, cur, gu, gk in gain_rows:
            sign = "▲" if gu > 0 else "▼"
            short_name = n[:10] if n else t
            lines.append(
                f"{t:<6} {short_name:<12} {s:>6.4f}주  @${avg:>6.2f}  @${cur:>6.2f}  {sign}${abs(gu):>7.2f}"
            )
        for t, n, s, avg, cur, gu, gk in rows:
            if gu is None:
                lines.append(f"{t:<6} {'(가격 조회 실패)':<20}")

        lines += [
            SEP,
            f"미실현 순손익     : {'+' if total_gain_krw >= 0 else ''}{total_gain_krw:,.0f}원  (${total_gain_krw/fx:,.2f})",
            f"기본공제 (250만원): -{min(EXEMPTION_KRW, max(0,total_gain_krw)):,.0f}원",
            f"과세표준          : {taxable_krw:,.0f}원",
            f"예상 세금 (22%)   : {tax_krw:,.0f}원  (${tax_usd:,.2f})",
            "",
            "※ 전량 매도 시 추산 — 실현 손익 기준 신고",
            "※ 손실 통산 가능: 손실 종목이 수익을 상쇄",
            "※ 국내 양도세 신고: 5월 (전년도 실현손익)",
        ]
        send(chat_id, "\n".join(lines))
    except Exception as e:
        send(chat_id, f"❌ 세금 추산 오류: {e}")
        logger.exception("cmd_tax")


def cmd_report(chat_id: str):
    """항상 실시간 데이터로 전체 바벨 리포트."""
    send(chat_id, "⏳ 실시간 데이터 수집 중...")
    typing(chat_id)
    try:
        d   = fetch_market(force=True)
        old = load_phase_state()
        report = build_report(
            d["qqq"], d["rsi"], d["vix"], d["ma"],
            d["portfolio"], d["exchange_rate"], d["qqqi_div"], old,
        )
        send(chat_id, report)
    except Exception as e:
        send(chat_id, f"❌ 리포트 생성 오류: {e}")
        logger.exception("cmd_report")


def cmd_alert(chat_id: str, args: list):
    """가격 알림 add / list / remove."""
    if not args:
        send(chat_id,
             "🔔 가격 알림 사용법\n"
             "━━━━━━━━━━━━━━━━━━━━━━━\n"
             "/alert add TICKER 가격 buy|sell [메모]\n"
             "  buy  → 현재가 ≤ 가격 시 발동 (손절·매수기회)\n"
             "  sell → 현재가 ≥ 가격 시 발동 (익절 목표)\n"
             "\n"
             "/alert list          활성 알림 목록\n"
             "/alert remove ID     알림 삭제\n"
             "\n"
             "예시:\n"
             "/alert add CPNG 14.00 sell  ← 손절\n"
             "/alert add ORCL 260.00 sell ← 익절\n"
             "/alert add QQQ  430.00 buy  ← 매수 기회")
        return

    sub = args[0].lower()

    if sub == "list":
        alerts = load_alerts()
        active  = [a for a in alerts if not a.get("triggered")]
        done    = [a for a in alerts if a.get("triggered")]

        if not alerts:
            send(chat_id, "등록된 알림이 없습니다.\n/alert add 로 추가하세요.")
            return

        lines = ["🔔 가격 알림 목록", "━━━━━━━━━━━━━━━━━━━━━━━"]
        if active:
            lines.append("⏳ 활성")
            for a in active:
                t     = "↓매수" if a["type"] == "buy" else "↑매도"
                note  = f"  [{a['note']}]" if a.get("note") else ""
                lines.append(f"  {a['id']}  {a['ticker']}  {t}${a['price']:.2f}{note}")
        if done:
            lines.append("\n✅ 완료")
            for a in done[-5:]:
                t  = "↓매수" if a["type"] == "buy" else "↑매도"
                tp = a.get("triggered_price", "?")
                lines.append(f"  {a['id']}  {a['ticker']}  {t}${a['price']:.2f}  → ${tp}  ({a.get('triggered_at','?')[:10]})")
        send(chat_id, "\n".join(lines))

    elif sub == "add":
        if len(args) < 4:
            send(chat_id, "사용법: /alert add TICKER 가격 buy|sell [메모]\n예: /alert add CPNG 14.00 sell 손절")
            return
        ticker = args[1].upper()
        try:
            price = float(args[2])
        except ValueError:
            send(chat_id, f"❌ 가격 오류: {args[2]}")
            return
        atype = args[3].lower()
        if atype not in ("buy", "sell"):
            send(chat_id, "❌ buy 또는 sell 만 입력 가능합니다.")
            return
        note = " ".join(args[4:]) if len(args) > 4 else ""

        # 현재가 조회
        current = None
        try:
            import yfinance as yf
            h = yf.Ticker(ticker).history(period="2d")
            if not h.empty:
                current = float(h["Close"].iloc[-1])
        except Exception:
            pass

        aid = add_alert(ticker, price, atype, note)
        type_str = "현재가 ≤ 목표가 시 발동 (매수/손절)" if atype == "buy" else "현재가 ≥ 목표가 시 발동 (익절)"
        msg = (
            f"✅ 알림 등록 완료\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ID      {aid}\n"
            f"종목    {ticker}\n"
            f"목표가  ${price:.2f}  ({type_str})\n"
        )
        if note:
            msg += f"메모    {note}\n"
        if current:
            diff = (price - current) / current * 100
            msg += f"현재가  ${current:.2f}  (목표까지 {diff:+.1f}%)"
        send(chat_id, msg)

    elif sub == "remove":
        if len(args) < 2:
            send(chat_id, "사용법: /alert remove ID")
            return
        if remove_alert(args[1]):
            send(chat_id, f"✅ 알림 {args[1]} 삭제 완료")
        else:
            send(chat_id, f"❌ ID {args[1]} 를 찾을 수 없습니다.")

    else:
        send(chat_id, f"❌ 알 수 없는 하위 명령: {sub}\n/alert 로 사용법 확인")


# ══════════════════════════════════════════════════════════════════════
#  가격 알림 자동 체크
# ══════════════════════════════════════════════════════════════════════

def notify_triggered_alerts():
    try:
        fired = check_alerts()
    except Exception as e:
        logger.error(f"알림 체크 오류: {e}")
        return
    for a in fired:
        t   = "매수 조건 달성" if a["type"] == "buy" else "익절 목표 달성"
        tp  = a.get("triggered_price", a["price"])
        msg = (
            f"🔔 가격 알림 발동!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"종목    {a['ticker']}\n"
            f"조건    {t}  @ ${a['price']:.2f}\n"
            f"현재가  ${tp:.2f}\n"
            f"ID      {a['id']}\n"
        )
        if a.get("note"):
            msg += f"메모    {a['note']}"
        send(ALLOWED_CHAT_ID, msg)
        logger.info(f"알림 발동: {a['ticker']} {a['type']} @ ${tp}")


# ══════════════════════════════════════════════════════════════════════
#  명령어 라우터
# ══════════════════════════════════════════════════════════════════════

_SIMPLE_CMDS = {
    "/help":      lambda d, _: cmd_help(),
    "/status":    lambda d, _: cmd_status(d),
    "/phase":     lambda d, _: cmd_phase(d),
    "/portfolio": lambda d, _: cmd_portfolio(d),
    "/dca":       lambda d, _: cmd_dca(d),
    "/sgov":      lambda d, _: cmd_sgov(d),
    "/history":   lambda d, _: cmd_history(d),
    "/rebalance": lambda d, _: cmd_rebalance(d),
}


def dispatch(text: str, chat_id: str):
    parts = text.strip().split()
    cmd   = parts[0].lower().split("@")[0]
    args  = parts[1:]

    # /report : 별도 처리 (내부에서 send 직접 호출)
    if cmd == "/report":
        cmd_report(chat_id)
        return

    # /alert, /dividend, /sim : 인자 필요
    if cmd == "/alert":
        typing(chat_id)
        cmd_alert(chat_id, args)
        return

    if cmd == "/dividend":
        typing(chat_id)
        cmd_dividend(chat_id, args)
        return

    if cmd == "/sim":
        typing(chat_id)
        cmd_sim(chat_id, args)
        return

    if cmd == "/holding":
        typing(chat_id)
        cmd_holding(chat_id, args)
        return

    if cmd == "/order":
        cmd_order(chat_id)
        return

    if cmd == "/tax":
        cmd_tax(chat_id)
        return

    fn = _SIMPLE_CMDS.get(cmd)
    if fn is None:
        send(chat_id, f"❓ 모르는 명령어: {cmd}\n/help 로 목록 확인")
        return

    typing(chat_id)
    try:
        d = fetch_market()
        send(chat_id, fn(d, chat_id))
    except Exception as e:
        send(chat_id, f"❌ 오류: {e}")
        logger.exception(f"dispatch {cmd}")


# ══════════════════════════════════════════════════════════════════════
#  메인 폴링 루프
# ══════════════════════════════════════════════════════════════════════

def run():
    if not TELEGRAM_TOKEN:
        logger.error("STOCK_BOT_TOKEN 없음 — .env 파일 확인")
        sys.exit(1)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info(f"🤖 Barbell Bot 시작 (PID {os.getpid()})")
    send(ALLOWED_CHAT_ID, "🤖 Barbell Bot 온라인 ✅\n/help 로 명령어 확인")

    offset: int | None  = None
    last_alert_check    = 0.0

    while True:
        try:
            updates = get_updates(offset)
            for upd in updates:
                offset  = upd["update_id"] + 1
                msg     = upd.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "")

                if not text.startswith("/"):
                    continue
                if chat_id != ALLOWED_CHAT_ID:
                    logger.warning(f"차단: chat_id {chat_id}")
                    _api("sendMessage", chat_id=chat_id, text="🔒 권한 없음")
                    continue

                logger.info(f"수신: {text!r}")
                dispatch(text, chat_id)

            # 가격 알림 주기 체크
            now = time.time()
            if now - last_alert_check > ALERT_CHECK_SECS:
                notify_triggered_alerts()
                last_alert_check = now

        except KeyboardInterrupt:
            logger.info("Bot 종료")
            send(ALLOWED_CHAT_ID, "🤖 Bot 오프라인")
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
            break
        except Exception as e:
            logger.error(f"루프 오류: {e} — {RETRY_DELAY}초 후 재시도")
            time.sleep(RETRY_DELAY)


# ══════════════════════════════════════════════════════════════════════
#  로컬 테스트 (Telegram 미전송)
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Barbell Telegram Bot")
    parser.add_argument("--test", action="store_true",
                        help="명령어 응답 로컬 출력 (전송 없음)")
    args = parser.parse_args()

    if args.test:
        print("=== 로컬 테스트 모드 ===\n")
        d = fetch_market()
        for name, fn in _SIMPLE_CMDS.items():
            print(f"\n{'─'*40}")
            print(f"[{name}]")
            print(fn(d, ALLOWED_CHAT_ID))
    else:
        run()
