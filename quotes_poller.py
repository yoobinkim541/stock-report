#!/usr/bin/env python3
"""quotes_poller.py — REST 시세 폴러 상시 프로세스 (KIS WS 캡 밖 롱테일 실시간가).

KIS WebSocket 은 세션당 41심볼 하드캡(KR 10·US 10 워치리스트)이라 포트폴리오+모의
유니버스+단기 스캐너 전체를 못 덮는다. 이 폴러가 그 롱테일을 REST 배치로 채운다:

  1차: 토스증권 GET /api/v1/prices — **한 호출에 200심볼**(KR 6자리+US 티커 혼합) ⭐
  2차: 키움 REST ka10095(지정종목정보요청·KR 복수코드) — 토스 실패/키 없음 시 KR 만

쓰기: ~/.cache/rest_quotes.json (safe_io atomic·이 프로세스가 유일 writer —
kis_realtime_quotes.json 은 kis_stream 단일 writer 그대로, 파일 분리로 충돌 0).
소비: providers/realtime_quotes 가 WS 신선 > REST 신선 > None(yfinance) 로 병합 —
소비자 코드 변경 없이 같은 seam 으로 커버리지만 확대된다.

안전: **시세 read-only** — 주문 TR/URL 없음(tests grep 강제). 장 마감 시장 심볼은
호출 자체를 생략(비용·레이트리밋 방어). QUOTES_POLL_ENABLED=true 일 때만 동작(opt-in).
크론 워치독: scripts/quotes_poller_watchdog.sh (매 1분·죽으면 재기동).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE_PATH = os.path.expanduser("~/.cache/rest_quotes.json")
PID_FILE = os.path.expanduser("~/.local/state/stock-report/quotes_poller.pid")

ENABLED = os.getenv("QUOTES_POLL_ENABLED", "false").lower() == "true"
POLL_SECS = max(5, int(os.getenv("QUOTES_POLL_SECS", "10")))
POLL_MAX = min(200, int(os.getenv("QUOTES_POLL_MAX", "200")))     # 토스 배치 상한 200
KIWOOM_FALLBACK_MAX = int(os.getenv("QUOTES_POLL_KIWOOM_MAX", "30"))

_KIWOOM_QUOTE_URL = "https://api.kiwoom.com/api/dostk/stkinfo"    # 시세 조회 전용 (read-only)


# ── 시장 개장 창 (관대한 경계 — 개장 직전/직후 포함해도 무해: 값만 수집) ────────

def kr_market_open(now: datetime | None = None) -> bool:
    """KRX 08:50~15:40 KST (동시호가 포함·KST 기준 요일 판정)."""
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    kst = now.astimezone(timezone(timedelta(hours=9)))
    if kst.weekday() >= 5:
        return False
    m = kst.hour * 60 + kst.minute
    return 8 * 60 + 50 <= m <= 15 * 60 + 40


def us_market_open(now: datetime | None = None) -> bool:
    """미 정규장 포괄 13:00~21:10 UTC (서머 13:30~20:00·겨울 14:30~21:00)."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return 13 * 60 <= m <= 21 * 60 + 10


def _base(sym: str) -> str:
    return str(sym or "").strip().upper().split(".")[0]


def is_kr_symbol(sym: str) -> bool:
    return _base(sym).isdigit() and len(_base(sym)) == 6


# ── 유니버스 (포트폴리오 + 모의 + 단기 스캐너 — 우선순위 순·중복 제거·캡) ──────

def build_universe(cap: int = POLL_MAX) -> list[str]:
    syms: list[str] = []

    def _add(items):
        for s in items or []:
            b = _base(s)
            if b and b not in syms and not b.startswith("^"):
                syms.append(b)

    try:
        from portfolio_universe import load_portfolio_tickers
        _add(load_portfolio_tickers())                      # 1) 보유 (최우선)
    except Exception:
        pass
    _add(["QQQ", "SPY"])                                    # 2) 벤치마크
    for mkt in ("kr", "us"):                                # 3) 단기 동적 유니버스
        try:
            from providers import intraday_universe as iu
            _add(iu.current_universe(mkt))
        except Exception:
            pass
    try:                                                    # 4) US 모의 유니버스
        from crons.us_mock_track import _universe as us_uni
        _add(us_uni())
    except Exception:
        pass
    try:                                                    # 5) KR 모의 스캔 대상 (KOSPI 상위)
        from reports.investment_report import KOSPI_TOP30
        _add(KOSPI_TOP30)
    except Exception:
        pass
    _add([s.strip() for s in os.getenv("QUOTES_POLL_EXTRA", "").split(",") if s.strip()])
    return syms[:cap]


# ── 소스별 조회 (read-only) ──────────────────────────────────────────────────

def fetch_toss(symbols: list[str]) -> dict[str, float]:
    """토스 배치 현재가 — {base_symbol: price}. 실패/키없음 → {}."""
    try:
        from providers import toss_api
        return {_base(k): v for k, v in toss_api.prices(symbols).items() if v}
    except Exception as e:
        logger.info("토스 시세 실패: %s", e)
        return {}


def fetch_kiwoom_kr(codes: list[str]) -> dict[str, float]:
    """키움 ka10095(지정종목정보요청) — KR 복수코드('|' 연결) 현재가. 실패 → {}."""
    codes = [c for c in codes if is_kr_symbol(c)][:KIWOOM_FALLBACK_MAX]
    if not codes:
        return {}
    try:
        import requests
        from kiwoom_rest_api.auth.token import TokenManager
        if not os.getenv("KIWOOM_API_KEY") or not os.getenv("KIWOOM_API_SECRET"):
            return {}
        tok = TokenManager().access_token
        if not tok:
            return {}
        resp = requests.post(
            _KIWOOM_QUOTE_URL,
            headers={"content-type": "application/json;charset=UTF-8",
                     "Authorization": f"Bearer {tok}", "api-id": "ka10095"},
            json={"stk_cd": "|".join(codes)},
            timeout=10)
        resp.raise_for_status()
        return parse_kiwoom_quotes(resp.json())
    except Exception as e:
        logger.info("키움 시세 폴백 실패: %s", e)
        return {}


def parse_kiwoom_quotes(result: dict) -> dict[str, float]:
    """ka10095 응답 → {code: price} (순수). 부호 접두(+/-)·쉼표 제거·리스트 키 관대 탐색."""
    out: dict[str, float] = {}
    if not isinstance(result, dict):
        return out
    rows = None
    for k, v in result.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            rows = v
            break
    for item in rows or []:
        code = _base(item.get("stk_cd", ""))
        raw = item.get("cur_prc") or item.get("stk_prpr") or ""
        try:
            price = abs(float(str(raw).replace(",", "").replace("+", "").strip()))
        except (TypeError, ValueError):
            continue
        if code and price > 0:
            out[code] = price
    return out


# ── 폴링 사이클 ───────────────────────────────────────────────────────────────

def poll_once(now: datetime | None = None, *, universe: list[str] | None = None,
              toss_fn=fetch_toss, kiwoom_fn=fetch_kiwoom_kr,
              cache_path: str = CACHE_PATH) -> int:
    """1회 폴링 — 열린 시장 심볼만 조회해 캐시 쓰기. 반환 = 갱신 심볼 수."""
    now = now or datetime.now(timezone.utc)
    kr_open, us_open = kr_market_open(now), us_market_open(now)
    if not (kr_open or us_open):
        return 0
    symbols = [s for s in (universe if universe is not None else build_universe())
               if (is_kr_symbol(s) and kr_open) or (not is_kr_symbol(s) and us_open)]
    if not symbols:
        return 0

    prices: dict[str, tuple[float, str]] = {}
    for sym, px in (toss_fn(symbols) or {}).items():
        prices[sym] = (px, "toss")
    missing_kr = [s for s in symbols if is_kr_symbol(s) and s not in prices]
    if missing_kr:
        for sym, px in (kiwoom_fn(missing_kr) or {}).items():
            prices.setdefault(sym, (px, "kiwoom"))
    if not prices:
        return 0

    ts = time.time()
    cache = {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            old = json.load(f)
        if isinstance(old, dict):
            cache = old
    except Exception:
        pass
    for sym, (px, src) in prices.items():
        cache[sym] = {"price": px, "ts": ts, "src": src}
    cache["__heartbeat__"] = {"ts": ts, "n": len(prices)}
    try:
        import safe_io
        safe_io.atomic_write_json(cache_path, cache)
    except Exception:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, cache_path)
    return len(prices)


def _acquire_lock():
    Path(PID_FILE).parent.mkdir(parents=True, exist_ok=True)
    fh = open(PID_FILE, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("이미 실행 중 — 종료")
        sys.exit(0)
    fh.seek(0); fh.truncate(); fh.write(str(os.getpid())); fh.flush()
    return fh


def main() -> int:
    if not ENABLED:
        logger.info("QUOTES_POLL_ENABLED=false — 폴러 비활성 (opt-in)")
        return 0
    _lock = _acquire_lock()   # noqa: F841 — 프로세스 수명 동안 잠금 유지
    logger.info("REST 시세 폴러 시작 — 주기 %ds·캡 %d", POLL_SECS, POLL_MAX)
    universe_at = 0.0
    universe: list[str] = []
    while True:
        try:
            if time.time() - universe_at > 300:            # 유니버스 5분마다 재구성
                universe = build_universe()
                universe_at = time.time()
                logger.info("유니버스 %d심볼 (KR %d·US %d)", len(universe),
                            sum(is_kr_symbol(s) for s in universe),
                            sum(not is_kr_symbol(s) for s in universe))
            n = poll_once(universe=universe)
            if n == 0 and not (kr_market_open() or us_market_open()):
                time.sleep(60)                             # 전 시장 마감 — 저전력 대기
                continue
        except Exception as e:
            logger.warning("폴링 사이클 오류(계속): %s", e)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    sys.exit(main())
