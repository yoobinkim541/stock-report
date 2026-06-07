#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_bot.py — Intelligence Barbell 양방향 텔레그램 봇

Commands:
  /help      — 명령어 목록
  /ask       — 포트폴리오 상담
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
import fcntl
import logging
import threading
from pathlib import Path
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from barbell_strategy import (
    fetch_qqq_data, fetch_rsi, fetch_vix, fetch_ma200, fetch_fear_greed,
    fetch_exchange_rate, fetch_portfolio_value,
    estimate_qqqi_monthly_dividend,
    classify_market, calculate_dca, calculate_sgov_target,
    build_smart_report,
    build_report, build_simulation_report, load_leverage_state, load_phase_state, save_phase_state,
    has_phase_changed, send_phase5_emergency,
    _holding_details_from_snapshot,
    _phase_meter, _bar, _sgov_compare, _dca_rows,
    BULL_PHASES, BEAR_PHASES,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
)
from bot.attachment_parser import (
    extract_text_from_pdf, extract_text_from_image,
    parse_portfolio_from_text, parse_sells_from_text,
    detect_content_type,
    save_pending_snapshot,
    save_pending_sells,
    build_pending_snapshot_summary, build_pending_sells_summary,
    ATTACH_DIR, _ensure_dir as _ensure_attach_dir,
)
from bot.price_alerts import load_alerts, add_alert, remove_alert, check_alerts
from portfolio_tracker import (
    load_history, calc_performance,
    build_performance_report,
)
from bot.order_generator import generate as generate_order
from holding_manager import refresh_portfolio_prices
from bot.stock_advisor import ask_portfolio_advisor
from bot.tax_commands import cmd_tax
from bot.holding_commands import cmd_holding, cmd_dividend, cmd_apply_snapshot
from bot.entry_commands import cmd_entry
try:
    from reports.source_collector import build_digest as build_source_digest, load_recent_events as load_source_events
except Exception:
    build_source_digest = None
    load_source_events = None

try:
    from ml.reporting import build_sample_ml_strategy_report, chunk_text as _ml_chunk_text
    _ML_REPORTING_AVAILABLE = True
except Exception:
    _ML_REPORTING_AVAILABLE = False

logger = logging.getLogger(__name__)

_LOCK_FD = None  # kept open for process lifetime to hold fcntl lock


class _Telegram409(Exception):
    """Raised when Telegram returns HTTP 409 Conflict on getUpdates."""


def setup_logging():
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
PHASE_CHECK_SECS   = 300   # Phase 변화 체크 주기(초, 5분)
ENTRY_CHECK_SECS   = 1800  # 진입 타점 알림 체크 주기(초, 30분)


def _pid_file_path() -> str:
    path = Path.home() / ".local" / "state" / "stock-report" / "barbell_bot.pid"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


PID_FILE           = _pid_file_path()
_PHASE_LOCK_FILE   = os.path.expanduser("~/.cache/barbell_state.lock")


def _lock_file_path() -> str:
    return str(Path(PID_FILE).with_suffix(".lock"))


def _acquire_instance_lock() -> bool:
    """Non-blocking exclusive flock. Returns False if another instance holds it."""
    global _LOCK_FD
    try:
        fd = open(_lock_file_path(), "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FD = fd  # keep fd open to hold lock
        return True
    except (IOError, OSError):
        return False


def _cleanup_pid_file():
    try:
        with open(PID_FILE) as f:
            stored = int(f.read().strip())
        if stored == os.getpid():
            os.remove(PID_FILE)
    except Exception:
        pass


BOT_COMMANDS = [
    {"command": "help",           "description": "명령어 목록"},
    {"command": "status",         "description": "Phase + 핵심 수치 (5분 캐시)"},
    {"command": "summary",        "description": "한 줄 빠른 현황 — Phase·QQQ·총액·F&G"},
    {"command": "phase",          "description": "Phase 미터 + 행동 지침"},
    {"command": "report",         "description": "전체 바벨 리포트 (실시간)"},
    {"command": "sim",            "description": "시장 상태 시뮬레이션"},
    {"command": "portfolio",      "description": "포트폴리오 실시간 현황"},
    {"command": "rebalance",      "description": "리밸런싱 계산기"},
    {"command": "history",        "description": "성과 히스토리 (1d/7d/30d/90d)"},
    {"command": "sgov",           "description": "SGOV 실탄 상태"},
    {"command": "dca",            "description": "오늘 DCA 배분"},
    {"command": "order",          "description": "소수점 매수 주문서"},
    {"command": "holding",        "description": "보유 종목 조회/매수·매도/목표비중/DCA/배당"},
    {"command": "tax",            "description": "실현손익 & 양도세 (sim/sell/history/delete/import)"},
    {"command": "ask",            "description": "AI 포트폴리오 상담"},
    {"command": "alert",          "description": "가격 알림 관리 (add/list/remove)"},
    {"command": "mlreport",       "description": "ML 전략 성과 리포트 (샘플)"},
    {"command": "ranking",        "description": "NASDAQ100 종목 랭킹 (LightGBM)"},
    {"command": "leverage",       "description": "레버리지 ETF 진입 분석 (QLD/TQQQ/SOXL/UPRO 손익비·타점)"},
    {"command": "meta",           "description": "ML 통합 포트폴리오 배분 (MetaAllocator)"},
    {"command": "entry",          "description": "진입 타점 분석 — 포트/us50/kr/watch/단일종목 (예: /entry NVDA, /entry kr)"},
]

BOT_COMMAND_ALIASES = {
    "/portpolio": "/portfolio",
    "/protfolio": "/portfolio",
    "/porfolio": "/portfolio",
}


INTERNAL_TEXT_ROUTES = [
    ("/portfolio", ("포트폴리오", "portfolio", "보유현황", "보유 현황")),
    ("/status", ("상태", "현황", "status")),
    ("/phase", ("phase", "페이즈", "단계")),
    ("/dca", ("dca", "적립", "배분")),
    ("/sgov", ("sgov", "실탄")),
    ("/history", ("history", "히스토리", "성과")),
    ("/rebalance", ("rebalance", "리밸런싱")),
    ("/order", ("주문서", "매수 주문")),
    ("/help", ("명령어", "도움말", "help")),
]


# ══════════════════════════════════════════════════════════════════════
#  시장 데이터 캐시
# ══════════════════════════════════════════════════════════════════════

_cache: dict = {}
_cache_lock = threading.Lock()


def fetch_benchmark_returns(tickers=("QQQ", "SPY"), yf_module=None) -> dict:
    """Fetch current/YTD benchmark returns for advisor grounding."""
    if yf_module is None:
        import yfinance as yf_module

    returns = {}
    for ticker in tickers:
        try:
            h = yf_module.Ticker(ticker).history(period="ytd", auto_adjust=True)
            if h.empty:
                continue
            close = h["Close"].dropna()
            if len(close) < 2:
                continue
            first = float(close.iloc[0])
            current = float(close.iloc[-1])
            returns[ticker] = {
                "current": round(current, 2),
                "ytd_pct": round((current - first) / first * 100, 2) if first > 0 else None,
            }
        except Exception:
            continue
    return returns


def load_advisor_source_digest(hours: int = 24) -> str:
    """Load compact trusted-source cache for /ask grounding."""
    if not build_source_digest or not load_source_events:
        return ""
    try:
        events = load_source_events(hours=hours)
    except Exception:
        return ""
    if not events:
        return ""
    return build_source_digest(events, limit=10)


def fetch_market(force: bool = False) -> dict:
    """모든 시장 데이터 일괄 조회. CACHE_TTL 동안 재사용."""
    now = time.time()
    with _cache_lock:
        if not force and "data" in _cache and now - _cache.get("ts", 0) < CACHE_TTL:
            return _cache["data"]

    qqq  = fetch_qqq_data()
    rsi  = fetch_rsi("QQQ")
    vix  = fetch_vix()
    ma   = fetch_ma200("QQQ")
    fg   = fetch_fear_greed()
    fx   = fetch_exchange_rate()
    port = fetch_portfolio_value()
    bench = fetch_benchmark_returns()
    source_digest = load_advisor_source_digest()
    div  = estimate_qqqi_monthly_dividend(port["qqqi_shares"], port["qqqi_usd"])
    market_type, phase_key = classify_market(qqq, rsi, vix)

    data = {
        "qqq": qqq, "rsi": rsi, "vix": vix, "ma": ma,
        "fear_greed": fg,
        "benchmarks": bench,
        "source_digest": source_digest,
        "exchange_rate": fx, "portfolio": port, "qqqi_div": div,
        "market_type": market_type, "phase_key": phase_key,
        "fetched_at": datetime.now().strftime("%m/%d %H:%M"),
    }
    with _cache_lock:
        _cache["data"] = data
        _cache["ts"]   = now
    return data


# ══════════════════════════════════════════════════════════════════════
#  Telegram API
# ══════════════════════════════════════════════════════════════════════

_SENTINEL_409: dict = {"__conflict_409__": True}


def _api(method: str, **kwargs) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    request_timeout = kwargs.pop("_request_timeout", 15)
    attempts = kwargs.pop("_attempts", 3)
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(url, json=kwargs, timeout=request_timeout)
            if r.ok:
                return r.json()
            if r.status_code == 409:
                logger.warning("API %s HTTP 409 Conflict", method)
                return _SENTINEL_409
            logger.warning("API %s HTTP %s: %s", method, r.status_code, r.text[:200])
            if r.status_code < 500 and r.status_code != 429:
                return {}
        except Exception as e:
            logger.error("API %s attempt %d/%d: %s", method, attempt, attempts, e)
        if attempt < attempts:
            time.sleep(min(2 ** (attempt - 1), 4))
    return {}


def configure_bot_commands():
    """Telegram 메뉴에 BOT_COMMANDS 등록 (setMyCommands, 전 scope)."""
    scopes = [
        None,  # default scope
        {"type": "all_private_chats"},
        {"type": "all_chat_administrators"},
    ]
    success = 0
    for scope in scopes:
        params = {"commands": BOT_COMMANDS}
        if scope is not None:
            params["scope"] = scope
        result = _api("setMyCommands", **params)
        if result.get("result") is True:
            success += 1
    logger.info("setMyCommands 완료 (%d개 scope, %d개 명령어)", success, len(BOT_COMMANDS))


def send(chat_id: str, text: str, max_len: int = 4000):
    """4000자 초과 시 줄바꿈 기준으로 분할 전송 (이모지·단어 깨짐 방지)."""
    if len(text) <= max_len:
        _api("sendMessage", chat_id=chat_id, text=text)
        return
    chunks, current, current_len = [], [], 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if current and current_len + line_len > max_len:
            chunks.append("\n".join(current))
            current, current_len = [line], line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    for chunk in chunks:
        _api("sendMessage", chat_id=chat_id, text=chunk)


def send_photo(chat_id: str, path: str, caption: str = "") -> bool:
    """로컬 PNG/JPG 파일을 텔레그램으로 전송."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(path, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={"photo": f},
                timeout=30,
            )
        return r.ok
    except Exception as e:
        logger.warning("send_photo 실패 (%s): %s", path, e)
        return False


def typing(chat_id: str):
    _api("sendChatAction", chat_id=chat_id, action="typing")


def keep_typing(chat_id: str):
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            typing(chat_id)
            stop.wait(4)

    threading.Thread(target=loop, daemon=True).start()
    return stop.set


def get_updates(offset: int | None = None) -> list | None:
    params: dict = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    resp = _api("getUpdates", _request_timeout=POLL_TIMEOUT + 10, **params)
    if resp.get("__conflict_409__"):
        raise _Telegram409("Telegram 409 Conflict — 다른 getUpdates 인스턴스 충돌")
    if not resp.get("ok"):
        return None
    return resp.get("result", [])


# ══════════════════════════════════════════════════════════════════════
#  명령어 핸들러
# ══════════════════════════════════════════════════════════════════════

def cmd_help() -> str:
    lines = ["🏋️ Intelligence Barbell Bot", "━━━━━━━━━━━━━━━━━━━━━━━"]
    for cmd in BOT_COMMANDS:
        lines.append(f"/{cmd['command']:20s} {cmd['description']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📎 이미지·PDF 전송 → 포트폴리오·매도내역 자동 파싱")
    lines.append(f"캐시: {CACHE_TTL // 60}분  ·  /report·/order는 항상 실시간")
    return "\n".join(lines)


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

    fg    = d.get("fear_greed") or {}
    fg_sc = fg.get("score", 50.0)
    fg_rt = fg.get("rating", "neutral")
    fg_lbl = ("💀극단공포" if fg_sc <= 25 else "😨공포" if fg_sc <= 45
              else "😐중립" if fg_sc <= 55 else "😄탐욕" if fg_sc <= 75
              else "🤑극단탐욕")
    fg_proxy = fg.get("proxy_score", -1.0)
    fg_cnn_ok = fg.get("cnn_ok", True)
    _fg_proxy_str = (f"  proxy {fg_proxy:.0f}" if fg_proxy >= 0 else "")

    rsi_s = ("🔥과매도" if rsi < 30 else "⚠️약세"     if rsi < 40
             else "🫧극과매수" if rsi > 75 else "🌡과매수" if rsi > 70
             else "✅중립")
    vix_s = ("💥극공포" if vix > 40 else "🚨공포"   if vix > 30
             else "😴과낙관" if vix < 15 else "✅정상")

    mom_1m = qqq.get("mom_1m_pct", 0) or 0
    mom_s  = f"{mom_1m:+.1f}%"
    ret_pct  = port.get("return_pct", 0) or 0
    ret_sign = "▲" if ret_pct > 0 else ("▼" if ret_pct < 0 else "─")

    return (
        f"📊 현재 상태  ({d['fetched_at']})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{info['emoji']} {info['label']}\n"
        f"\n"
        f"QQQ   ${qqq.get('current', 0):>8,.2f}   1M {mom_s}\n"
        f"낙폭  {dd:>+8.2f}%\n"
        f"RSI   {rsi:>8.1f}   {rsi_s}\n"
        f"VIX   {vix:>8.1f}   {vix_s}\n"
        f"F&G   {fg_sc:>8.1f}   {fg_lbl}{_fg_proxy_str}\n"
        f"\n"
        f"총액  ${port['total_usd']:>8,.0f}  (₩{total_krw:,})\n"
        f"수익  {ret_sign}{abs(ret_pct):>7.1f}%\n"
        f"SGOV  ${port['sgov_usd']:>8,.0f}  실탄 대기중"
    )


def cmd_summary(d: dict) -> str:
    """한 줄 빠른 상태 — Phase · QQQ · 총액 · F&G."""
    qqq  = d["qqq"]
    mt   = d["market_type"]
    pk   = d["phase_key"]
    info = BULL_PHASES[pk] if mt == "bull" else BEAR_PHASES[pk]
    port = d["portfolio"]
    fx   = d["exchange_rate"]
    fg   = (d.get("fear_greed") or {}).get("score", 50)
    dd   = qqq.get("drawdown_pct", 0)
    ret  = port.get("return_pct", 0) or 0
    ret_s = f"{'▲' if ret>=0 else '▼'}{abs(ret):.1f}%"
    fg_e = ("💀" if fg<=25 else "😨" if fg<=45 else "😐" if fg<=55 else "😄" if fg<=75 else "🤑")
    return (
        f"{info['emoji']} {info['label']}  |  "
        f"QQQ ${qqq.get('current',0):,.0f} ({dd:+.1f}%)  |  "
        f"₩{int(port['total_usd']*fx):,} {ret_s}  |  "
        f"F&G {fg:.0f}{fg_e}"
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
    pnl_usd = port.get("pnl_usd", 0.0)
    return_pct = port.get("return_pct", 0.0)
    pnl_sign = "+" if pnl_usd >= 0 else "-"
    domestic_cost = port.get("domestic_cost_krw", 0)
    domestic_value = port.get("domestic_value_krw", 0)
    domestic_pnl = port.get("domestic_pnl_krw", 0)
    overall_cost = port.get("cost_usd", 0) * fx + domestic_cost
    overall_value = port["total_usd"] * fx + domestic_value
    overall_pnl = pnl_usd * fx + domestic_pnl
    overall_return = overall_pnl / overall_cost * 100 if overall_cost > 0 else 0.0
    overall_sign = "+" if overall_pnl >= 0 else "-"

    lines = [
        f"💼 포트폴리오  ({d['fetched_at']})",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  총액  ${port['total_usd']:,.2f}  {pnl_sign}${abs(pnl_usd):,.2f} ({pnl_sign}{abs(return_pct):.1f}%)",
        f"  원화  ₩{total_krw:,}",
        f"  전체  ₩{int(overall_value):,}  {overall_sign}₩{abs(int(overall_pnl)):,} ({overall_sign}{abs(overall_return):.1f}%)",
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

    # ── 개별 종목 P&L ───────────────────────────────────────────────────
    details = port.get("holdings_detail", [])
    _SKIP = {"SGOV", "QQQI", "QLD", "TQQQ"}
    stock_details = [h for h in details if h.get("ticker") not in _SKIP and h.get("value_usd", 0) > 0]
    if stock_details:
        lines += ["", "━━━ 📈 개별 종목 ━━━"]
        stock_details.sort(key=lambda h: h.get("value_usd", 0), reverse=True)
        for h in stock_details:
            ret  = h.get("return_pct", 0) or 0
            val  = h.get("value_usd", 0)
            sign = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
            lines.append(
                f"  {h['ticker']:<6}  ${val:>7,.0f}  {sign}{abs(ret):5.1f}%"
            )

    div = d["qqqi_div"]
    lines += [
        "",
        f"  QQQI 월 배당  ${div['monthly_usd']:.2f}  (연 {div['annual_yield_pct']:.1f}%)",
    ]
    return "\n".join(lines)


def cmd_dca(d: dict) -> str:
    dca       = calculate_dca(d["market_type"], d["phase_key"], d["exchange_rate"])
    base_mult = dca.get("base_mult", dca["multiplier"])
    fg_proxy  = dca.get("fg_proxy", -1.0)
    fg_adj    = dca.get("fg_adj", 1.0)
    ml_mult   = dca.get("ml_mult", 1.0)
    ml_label  = dca.get("ml_label", "")
    ml_bread  = dca.get("ml_breadth", 0.0)
    ml_dir    = dca.get("ml_direction", {})

    # 배율 분해: Phase × F&G × ML
    mult_parts = [f"{base_mult}×(Phase)"]
    if fg_adj != 1.0:
        mult_parts.append(f"×{fg_adj}(F&G)")
    if ml_mult != 1.0:
        mult_parts.append(f"×{ml_mult}(ML)")
    mult_str = "  ".join(mult_parts)

    lines = [
        f"💸 오늘 DCA  {dca['total_krw']:,}원  (${dca['total_usd']:.2f})",
        f"   [{dca['multiplier']}×]  {mult_str}",
    ]
    if ml_label:
        lines.append(f"   🤖 {ml_label}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")

    # 종목별 배분 + ML 방향 표시
    for ticker, amt in dca["by_ticker"].items():
        pct  = amt / dca["total_krw"] * 100 if dca["total_krw"] > 0 else 0
        tag  = ml_dir.get(ticker, "")
        usd  = round(amt / d["exchange_rate"], 2)
        lines.append(f"  {ticker:<6}  {amt:>7,}원  ${usd:.2f}  ({pct:.0f}%)  {tag}")

    lines += [
        "",
        f"   포트폴리오 ML 강도: {ml_bread:+.2f}%",
        "📋 키움 소수점 매수 주문서: /order",
    ]
    return "\n".join(lines)


def cmd_order(chat_id: str):
    """소수점 매수 주문서 — 키움증권 해외주식 > 소수점 매수 화면에서 즉시 입력."""
    send(chat_id, "⏳ 주문서 생성 중...")
    typing(chat_id)
    try:
        import html as _html
        report = generate_order()
        escaped = _html.escape(report)
        for i in range(0, len(escaped), 4000):
            _api("sendMessage", chat_id=chat_id, text=f"<pre>{escaped[i:i + 4000]}</pre>", parse_mode="HTML")
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


def download_telegram_file(file_id: str, filename: str) -> str | None:
    """텔레그램 파일을 로컬(ATTACH_DIR)에 저장하고 경로 반환."""
    _ensure_attach_dir()
    res = _api("getFile", file_id=file_id)
    file_path = res.get("result", {}).get("file_path")
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    try:
        r = requests.get(url, timeout=60)
        if not r.ok:
            return None
        local_path = str(ATTACH_DIR / filename)
        with open(local_path, "wb") as f:
            f.write(r.content)
        return local_path
    except Exception as e:
        logger.error(f"파일 다운로드 실패: {e}")
        return None


def handle_attachment(msg: dict, chat_id: str):
    """photo 또는 document 메시지를 수신해 파싱·pending 저장·요약 전송."""
    caption = msg.get("caption", "")

    if "photo" in msg:
        photo   = msg["photo"][-1]
        file_id = photo["file_id"]
        filename = f"photo_{file_id[:12]}.jpg"
        file_type = "image"
    else:
        doc      = msg["document"]
        file_id  = doc["file_id"]
        filename = doc.get("file_name", f"doc_{file_id[:12]}.bin")
        mime     = doc.get("mime_type", "")
        file_type = "pdf" if "pdf" in mime.lower() else "image"

    send(chat_id, f"⏳ 파일 수신 중... ({file_type.upper()})")

    local_path = download_telegram_file(file_id, filename)
    if not local_path:
        send(chat_id, "❌ 파일 다운로드 실패. 다시 시도해주세요.")
        return

    # 텍스트 추출
    text: str | None = None
    if file_type == "pdf":
        text = extract_text_from_pdf(local_path)
    else:
        text = extract_text_from_image(local_path)

    if not text:
        if caption.strip():
            text = caption
        else:
            send(chat_id,
                 "⚠️ 텍스트 인식 실패\n\n"
                 "이미지에서 내용을 읽지 못했습니다.\n"
                 "아래 명령어로 직접 입력해주세요:\n\n"
                 "포트폴리오 보유 현황:\n"
                 "  /holding buy TICKER 수량 평단가\n\n"
                 "매도내역 기록:\n"
                 "  /tax sell TICKER 수량 매수단가 매도단가")
            return

    # 유형 감지
    content_type = detect_content_type(text, caption)

    if content_type == "sell":
        sells = parse_sells_from_text(text)
        if not sells:
            send(chat_id,
                 "⚠️ 매도내역 인식 실패\n\n"
                 "알려진 종목(NVDA, ORCL 등) 매도내역을 인식하지 못했습니다.\n"
                 "수동 입력:\n"
                 "/tax sell TICKER 수량 매수단가 매도단가")
            return
        save_pending_sells(sells)
        send(chat_id, build_pending_sells_summary({"parsed_at": datetime.now().isoformat(), "sells": sells}))

    elif content_type == "portfolio":
        holdings = parse_portfolio_from_text(text)
        if not holdings:
            send(chat_id,
                 "⚠️ 포트폴리오 인식 실패\n\n"
                 "알려진 종목(NVDA, ORCL 등) 보유 현황을 인식하지 못했습니다.\n"
                 "수동 입력:\n"
                 "/holding buy TICKER 수량 평단가")
            return
        if len(holdings) < 3:
            send(chat_id,
                 "⚠️ 포트폴리오 인식 불완전\n\n"
                 f"인식된 종목이 {len(holdings)}개뿐이라 스냅샷을 저장하지 않았습니다.\n"
                 "잘못 적용되면 기존 포트폴리오가 망가질 수 있습니다.\n\n"
                 "더 선명한 스크린샷/PDF를 보내거나 수동 입력해주세요:\n"
                 "/holding buy TICKER 수량 평단가")
            return
        save_pending_snapshot(holdings)
        send(chat_id, build_pending_snapshot_summary({"parsed_at": datetime.now().isoformat(), "holdings": holdings}))

    else:
        # 알 수 없음 — 양쪽 파싱 후 더 많은 쪽 사용
        holdings = parse_portfolio_from_text(text)
        sells    = parse_sells_from_text(text)
        if sells and len(sells) >= len(holdings):
            save_pending_sells(sells)
            send(chat_id,
                 "💡 매도내역으로 인식했습니다.\n\n"
                 + build_pending_sells_summary({"parsed_at": datetime.now().isoformat(), "sells": sells}))
        elif holdings:
            if len(holdings) < 3:
                send(chat_id,
                     "⚠️ 포트폴리오 인식 불완전\n\n"
                     f"인식된 종목이 {len(holdings)}개뿐이라 스냅샷을 저장하지 않았습니다.\n"
                     "캡션에 '포트폴리오'를 포함해 더 선명한 파일을 다시 보내거나 수동 입력해주세요.")
                return
            save_pending_snapshot(holdings)
            send(chat_id,
                 "💡 포트폴리오 현황으로 인식했습니다.\n\n"
                 + build_pending_snapshot_summary({"parsed_at": datetime.now().isoformat(), "holdings": holdings}))
        else:
            send(chat_id,
                 "⚠️ 내용 인식 실패\n\n"
                 "캡션에 '포트폴리오' 또는 '매도내역'을 포함해 다시 전송하거나,\n"
                 "명령어로 직접 입력해주세요:\n"
                 "/holding buy TICKER 수량 평단가\n"
                 "/tax sell TICKER 수량 매수단가 매도단가")


def handle_plain_text(text: str, chat_id: str) -> bool:
    """Plain portfolio/sell text pasted into chat -> pending save."""
    content_type = detect_content_type(text, "")

    if content_type == "sell":
        sells = parse_sells_from_text(text)
        if sells:
            save_pending_sells(sells)
            send(chat_id, build_pending_sells_summary({"parsed_at": datetime.now().isoformat(), "sells": sells}))
            return True
        return False

    if content_type == "portfolio":
        holdings = parse_portfolio_from_text(text)
        if not holdings:
            return False
        if len(holdings) < 3:
            send(chat_id,
                 "⚠️ 포트폴리오 인식 불완전\n\n"
                 f"인식된 종목이 {len(holdings)}개뿐이라 스냅샷을 저장하지 않았습니다.\n"
                 "잘못 적용되면 기존 포트폴리오가 망가질 수 있습니다.\n\n"
                 "더 많은 보유 종목을 포함해 다시 붙여넣거나 수동 입력해주세요:\n"
                 "/holding buy TICKER 수량 평단가")
            return True
        save_pending_snapshot(holdings)
        send(chat_id, build_pending_snapshot_summary({"parsed_at": datetime.now().isoformat(), "holdings": holdings}))
        return True

    return False


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
            d.get("fear_greed"),
        )
        send(chat_id, report)
    except Exception as e:
        send(chat_id, f"❌ 리포트 생성 오류: {e}")
        logger.exception("cmd_report")


def cmd_mlreport(chat_id: str, args: list = None, send_fn=None, send_photo_fn=None):
    """ML 전략 성과 리포트.

    /mlreport        — 합성 데이터 샘플 (빠름)
    /mlreport real   — 실시장 데이터 QQQ 3년 (약 30초)
    /mlreport real NVDA — 실시장 데이터 NVDA
    """
    _send       = send_fn       if send_fn       is not None else send
    _send_photo = send_photo_fn if send_photo_fn is not None else send_photo
    if not _ML_REPORTING_AVAILABLE:
        _send(chat_id, "❌ ml.reporting 모듈을 불러올 수 없습니다.")
        return

    args = args or []
    use_real = "real" in args
    ticker   = next((a for a in args if a.upper() == a and a.isalpha() and a != "REAL"), "QQQ")

    try:
        if use_real:
            _send(chat_id, f"⏳ 실데이터({ticker}) 분석 중... (약 30초)")
            from ml.reporting import build_real_ml_strategy_report
            report = build_real_ml_strategy_report(asset_ticker=ticker, days=756)
        else:
            report = build_sample_ml_strategy_report()
        for chunk in _ml_chunk_text(report):
            _send(chat_id, chunk)
    except Exception as e:
        _send(chat_id, f"❌ ML 리포트 생성 오류: {e}")
        logger.exception("cmd_mlreport")
        return

    # 이퀴티 곡선 + 시험 산점도 이미지 전송 (matplotlib 없으면 건너뜀)
    try:
        import tempfile
        from ml.sweet_spot import generate_synthetic_market_data, optimize_sweet_spot, plot_results
        data   = generate_synthetic_market_data()
        result = optimize_sweet_spot(data)
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = plot_results(result, outdir=tmpdir)
            for p in paths:
                import os
                fname = os.path.basename(p)
                caption = "📈 이퀴티 곡선" if "equity" in fname else "🔬 파라미터 탐색"
                _send_photo(chat_id, p, caption=caption)
    except Exception as e:
        logger.info("mlreport 이미지 전송 건너뜀: %s", e)


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
        except Exception as _e:
            logger.debug("가격 알림 현재가 조회 실패: %s", _e)

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
#  진입 타점 모니터링 (30분 주기 자동 알림)
# ══════════════════════════════════════════════════════════════════════

def notify_entry_signals() -> None:
    """전체 감시 대상 진입 조건 감지 → 신규 enter 신호 시 푸시 알림.

    감시 대상: 포트폴리오 + 레버리지 ETF + 미국 시총 50 + 한국 시총 10
    """
    try:
        from ml.entry_analyzer import analyze_all_entries, check_alert_signals, format_alert_message
        # watch 유니버스: 포트폴리오 + US50 + KR10 + 레버리지
        scores  = analyze_all_entries(days=756, n_similar=25, universe="watch")
        alerts  = check_alert_signals(scores)
        for s in alerts:
            msg = format_alert_message(s)
            for chunk in (msg[i:i+4000] for i in range(0, len(msg), 4000)):
                send(ALLOWED_CHAT_ID, chunk)
            logger.info("진입 알림 발송: %s (점수=%.2f, %s)", s.ticker, s.score, s.currency)
    except Exception as e:
        logger.warning("진입 타점 모니터링 오류: %s", e)


# ══════════════════════════════════════════════════════════════════════
#  Phase 변화 모니터링 (봇 자체 발동)
# ══════════════════════════════════════════════════════════════════════

def notify_phase_change():
    """Phase 변화 감지 → Phase 5 진입 시 긴급 에스컬레이션 3회 발송.
    barbell_strategy의 STATE_FILE을 공유해 크론과 중복 발송을 방지."""
    try:
        os.makedirs(os.path.dirname(_PHASE_LOCK_FILE) or ".", exist_ok=True)
        with open(_PHASE_LOCK_FILE, "w") as _lf:
            try:
                fcntl.flock(_lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                logger.debug("Phase 알림 스킵 — 다른 프로세스 실행 중")
                return
            old = load_phase_state()
            d   = fetch_market()
            mt  = d["market_type"]
            pk  = d["phase_key"]
            if not has_phase_changed(old, mt, pk):
                return
            dd = d["qqq"].get("drawdown_pct", 0)
            qqq = d.get("qqq") or {}
            if qqq.get("current", 0) <= 0 or qqq.get("high_52w", 0) <= 0 or dd <= -80:
                logger.warning("Phase 변화 알림 스킵 — QQQ 데이터 비정상: %s", qqq)
                return
            if mt == "bear" and pk == 5:
                for i in range(3):
                    send_phase5_emergency(dd, d["exchange_rate"], d["portfolio"])
                    if i < 2:
                        time.sleep(3)
                logger.warning("Phase 5 긴급 에스컬레이션 3회 발송 완료 (봇)")
            else:
                info = BULL_PHASES[pk] if mt == "bull" else BEAR_PHASES[pk]
                send(ALLOWED_CHAT_ID,
                     f"⚠️ Phase 변화 감지!\n"
                     f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                     f"{info['emoji']} {info['label']}\n"
                     f"QQQ {dd:+.2f}%\n"
                     f"/phase 로 행동 지침 확인")
                logger.info(f"Phase 변화 알림 발송: {mt}/{pk}")
            save_phase_state(mt, pk, dd)
    except Exception as e:
        logger.error(f"Phase 변화 체크 오류: {e}")


# ══════════════════════════════════════════════════════════════════════
#  명령어 라우터
# ══════════════════════════════════════════════════════════════════════
def _normalize_message_text(text: str) -> str:
    """Plain text (no leading /) from authorized chat is routed as /ask."""
    text = text.strip()
    if text and not text.startswith("/"):
        return "/ask " + text
    return text


def _infer_internal_command(text: str) -> str | None:
    """Route only explicit command-like /ask text to built-in commands."""
    lower = text.strip().lower()
    if not lower:
        return None
    if lower.startswith("/"):
        return lower.split()[0].split("@")[0]

    explicit_suffixes = ("보여줘", "보여 줘", "알려줘", "조회", "확인", "목록", "리스트")
    advisory_markers = ("해도", "돼", "될까", "어때", "추천", "매수", "팔까", "사도")
    is_explicit = any(lower.endswith(s) for s in explicit_suffixes)
    if not is_explicit or any(marker in lower for marker in advisory_markers):
        return None

    for command, keywords in INTERNAL_TEXT_ROUTES:
        if any(keyword.lower() in lower for keyword in keywords):
            return command
    return None


_MARKET_CMDS = {
    "/help":      lambda d, _: cmd_help(),
    "/status":    lambda d, _: cmd_status(d),
    "/summary":   lambda d, _: cmd_summary(d),
    "/phase":     lambda d, _: cmd_phase(d),
    "/portfolio": lambda d, _: cmd_portfolio(d),
    "/dca":       lambda d, _: cmd_dca(d),
    "/sgov":      lambda d, _: cmd_sgov(d),
    "/history":   lambda d, _: cmd_history(d),
    "/rebalance": lambda d, _: cmd_rebalance(d),
}


def _dispatch_market(cmd: str, chat_id: str):
    typing(chat_id)
    try:
        if cmd == "/portfolio":
            refresh_portfolio_prices()
            d = fetch_market(force=True)
        else:
            d = fetch_market()
        send(chat_id, _MARKET_CMDS[cmd](d, chat_id))
    except Exception as e:
        send(chat_id, f"❌ 오류: {e}")
        logger.exception(f"dispatch {cmd}")


def _dispatch_ask(chat_id: str, args: list):
    if not args:
        send(chat_id, "사용법: /ask 질문\n예: /ask 지금 추가매수해도 돼?")
        return
    stop_typing = keep_typing(chat_id)
    try:
        d = fetch_market(force=True)
        send(chat_id, ask_portfolio_advisor(" ".join(args), d))
    except Exception as e:
        send(chat_id, f"❌ 오류: {e}")
        logger.exception("dispatch /ask")
    finally:
        stop_typing()


def _dispatch_with_typing(fn, chat_id: str, args: list):
    typing(chat_id)
    fn(chat_id, args)


def _dispatch_with_send(fn, chat_id: str, args: list):
    typing(chat_id)
    fn(chat_id, args, send)


def _dispatch_apply_snapshot(chat_id: str, args: list):
    cmd_apply_snapshot(chat_id, send)


def _dispatch_order(chat_id: str, args: list):
    cmd_order(chat_id)


def _dispatch_report(chat_id: str, args: list):
    cmd_report(chat_id)


def _dispatch_tax(chat_id: str, args: list):
    cmd_tax(chat_id, args, send)


def _dispatch_mlreport(chat_id: str, args: list):
    typing(chat_id)
    cmd_mlreport(chat_id, args=args)


def cmd_ranking(chat_id: str, args: list, send_fn=None):
    """NASDAQ100 LightGBM 종목 랭킹."""
    _send = send_fn if send_fn is not None else send
    _send(chat_id, "⏳ 랭킹 생성 중... (첫 실행 시 약 15초)")
    try:
        from ml.ranker import rank_today, load_ranker, format_ranking_report
        retrain = "retrain" in (args or [])
        ranking = rank_today(mode="nasdaq100", top_n=15, retrain=retrain)
        result  = load_ranker()
        if ranking.empty or result is None:
            _send(chat_id, "❌ 랭킹 생성 실패 — 데이터 확인 필요")
            return
        report = format_ranking_report(ranking, result)
        _send(chat_id, report)
    except Exception as e:
        _send(chat_id, f"❌ 랭킹 오류: {e}")
        logger.exception("cmd_ranking")


def _dispatch_ranking(chat_id: str, args: list):
    typing(chat_id)
    cmd_ranking(chat_id, args)


def cmd_leverage(chat_id: str, args: list, send_fn=None):
    """레버리지 ETF 진입 분석 — 현재 낙폭 기준 손익비·권장 비중·타점."""
    _send = send_fn if send_fn is not None else send
    retrain = "retrain" in (args or [])
    _send(chat_id, "⏳ 레버리지 분석 중... (첫 실행 시 약 30초)")
    try:
        from ml.leverage_signal import get_entry_signal, format_leverage_report
        sig    = get_entry_signal(retrain=retrain)
        report = format_leverage_report(sig)
        for chunk in (report[i:i+4000] for i in range(0, len(report), 4000)):
            _send(chat_id, chunk)
    except Exception as e:
        _send(chat_id, f"❌ 레버리지 분석 오류: {e}")
        logger.exception("cmd_leverage")


def _dispatch_leverage(chat_id: str, args: list):
    typing(chat_id)
    cmd_leverage(chat_id, args)


def cmd_meta(chat_id: str, args: list, send_fn=None):
    """ML 신호 통합 MetaAllocator — 최종 포트폴리오 비중 추천."""
    _send = send_fn if send_fn is not None else send
    _send(chat_id, "⏳ ML 신호 통합 중... (약 20초)")
    try:
        from ml.meta_allocator import get_meta_allocation, format_meta_report
        d      = fetch_market()
        alloc  = get_meta_allocation(d["market_type"], d["phase_key"])
        report = format_meta_report(alloc)
        _send(chat_id, report)
    except Exception as e:
        _send(chat_id, f"❌ MetaAllocator 오류: {e}")
        logger.exception("cmd_meta")


def _dispatch_meta(chat_id: str, args: list):
    typing(chat_id)
    cmd_meta(chat_id, args)


def _dispatch_entry(chat_id: str, args: list):
    typing(chat_id)
    cmd_entry(chat_id, args)


_COMMAND_HANDLERS = {
    "/report": _dispatch_report,
    "/mlreport": _dispatch_mlreport,
    "/ranking":  _dispatch_ranking,
    "/leverage": _dispatch_leverage,
    "/meta":     _dispatch_meta,
    "/entry":    _dispatch_entry,
    "/alert": lambda chat_id, args: _dispatch_with_typing(cmd_alert, chat_id, args),
    "/dividend": lambda chat_id, args: _dispatch_with_send(cmd_dividend, chat_id, args),
    "/sim": lambda chat_id, args: _dispatch_with_typing(cmd_sim, chat_id, args),
    "/holding": lambda chat_id, args: _dispatch_with_send(cmd_holding, chat_id, args),
    "/order": _dispatch_order,
    "/tax": _dispatch_tax,
    "/ask": _dispatch_ask,
    "/apply_snapshot": _dispatch_apply_snapshot,
}
for _cmd in _MARKET_CMDS:
    _COMMAND_HANDLERS[_cmd] = lambda chat_id, args, cmd=_cmd: _dispatch_market(cmd, chat_id)


def dispatch(text: str, chat_id: str):
    parts = text.strip().split()
    cmd   = parts[0].lower().split("@")[0]
    args  = parts[1:]

    cmd = BOT_COMMAND_ALIASES.get(cmd, cmd)
    if cmd == "/ask":
        inferred = _infer_internal_command(" ".join(args))
        if inferred:
            cmd = inferred
            args = []

    fn = _COMMAND_HANDLERS.get(cmd)
    if fn is None:
        send(chat_id, f"❓ 모르는 명령어: {cmd}\n/help 로 목록 확인")
        return
    fn(chat_id, args)


# ══════════════════════════════════════════════════════════════════════
#  메인 폴링 루프
# ══════════════════════════════════════════════════════════════════════

def run():
    if not TELEGRAM_TOKEN:
        logger.error("STOCK_BOT_TOKEN 없음 — .env 파일 확인")
        sys.exit(1)

    if not _acquire_instance_lock():
        logger.error("다른 봇 인스턴스가 실행 중입니다 (lock 점유) — 종료")
        sys.exit(0)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info(f"🤖 Barbell Bot 시작 (PID {os.getpid()})")
    configure_bot_commands()
    send(ALLOWED_CHAT_ID, "🤖 Barbell Bot 온라인 ✅\n/help 로 명령어 확인")

    offset: int | None  = None
    last_alert_check    = 0.0
    last_phase_check    = 0.0
    last_entry_check    = 0.0
    consecutive_409     = 0

    while True:
        try:
            try:
                updates = get_updates(offset)
            except _Telegram409:
                consecutive_409 += 1
                logger.warning("Telegram 409 Conflict #%d (중복 getUpdates 충돌)", consecutive_409)
                if consecutive_409 >= 3:
                    logger.critical("409 Conflict 3회 연속 — 중복 인스턴스 감지, 종료")
                    _cleanup_pid_file()
                    sys.exit(2)
                time.sleep(RETRY_DELAY)
                continue

            if updates is None:
                logger.warning("getUpdates 실패 — %s초 후 재시도", RETRY_DELAY)
                time.sleep(RETRY_DELAY)
                continue

            consecutive_409 = 0
            for upd in updates:
                offset  = upd["update_id"] + 1
                msg     = upd.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "")

                has_attachment = "photo" in msg or "document" in msg
                if not has_attachment and not text.strip():
                    continue
                if chat_id != ALLOWED_CHAT_ID:
                    logger.warning(f"차단: chat_id {chat_id}")
                    _api("sendMessage", chat_id=chat_id, text="🔒 권한 없음")
                    continue

                if has_attachment:
                    kind = "photo" if "photo" in msg else "document"
                    logger.info(f"첨부 수신: {kind}")
                    handle_attachment(msg, chat_id)
                    continue

                if not text.startswith("/") and handle_plain_text(text, chat_id):
                    logger.info("일반 텍스트를 스냅샷/매도내역으로 처리")
                    continue

                text = _normalize_message_text(text)
                logger.info(f"수신: {text!r}")
                dispatch(text, chat_id)

            # 가격 알림 주기 체크
            now = time.time()
            if now - last_alert_check > ALERT_CHECK_SECS:
                notify_triggered_alerts()
                last_alert_check = now

            # Phase 변화 주기 체크 (Phase 5 진입 시 긴급 에스컬레이션 3회)
            if now - last_phase_check > PHASE_CHECK_SECS:
                notify_phase_change()
                last_phase_check = now

            # 진입 타점 모니터링 (30분 주기, 새로운 enter 신호 → 푸시)
            if now - last_entry_check > ENTRY_CHECK_SECS:
                notify_entry_signals()
                last_entry_check = now

        except KeyboardInterrupt:
            logger.info("Bot 종료")
            send(ALLOWED_CHAT_ID, "🤖 Bot 오프라인")
            _cleanup_pid_file()
            break
        except Exception as e:
            logger.error(f"루프 오류: {e} — {RETRY_DELAY}초 후 재시도")
            time.sleep(RETRY_DELAY)


# ══════════════════════════════════════════════════════════════════════
#  로컬 테스트 (Telegram 미전송)
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    setup_logging()

    import argparse

    parser = argparse.ArgumentParser(description="Barbell Telegram Bot")
    parser.add_argument("--test", action="store_true",
                        help="명령어 응답 로컬 출력 (전송 없음)")
    args = parser.parse_args()

    if args.test:
        print("=== 로컬 테스트 모드 ===\n")
        d = fetch_market()
        for name, fn in _MARKET_CMDS.items():
            print(f"\n{'─'*40}")
            print(f"[{name}]")
            print(fn(d, ALLOWED_CHAT_ID))
    else:
        run()
