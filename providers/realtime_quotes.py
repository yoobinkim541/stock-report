#!/usr/bin/env python3
"""realtime_quotes.py — 실시간 시세 캐시의 **읽기전용 클라이언트** (폴백의 단일 seam).

소스 2계층 (WS 신선 > REST 신선 > None→yfinance):
  1차: kis_stream.py → ~/.cache/kis_realtime_quotes.json (KIS WS 틱·호가 — 세션 41심볼 캡)
  2차: quotes_poller.py → ~/.cache/rest_quotes.json (토스 배치 200 + 키움 KR 폴백 —
       WS 캡 밖 롱테일 현재가. 가격만 — 호가/체결강도는 WS 전용)

핵심 계약: **절대 예외를 던지지 않고**, 비활성/없음/stale 이면 None 을 반환해
호출부가 기존 yfinance 경로로 우아하게 폴백하게 한다. 소비자는 이 seam 만 안다.

신선도 2단 (계층별 독립):
  1) heartbeat — writer 프로세스가 살아있고 최근 갱신했는가. 죽었으면 그 캐시 전체 불신.
  2) 심볼별 ts — 해당 종목 값이 max_age_s 이내인가.
"""
from __future__ import annotations

import json
import os
import time

CACHE_PATH = os.path.expanduser("~/.cache/kis_realtime_quotes.json")
REST_CACHE_PATH = os.path.expanduser("~/.cache/rest_quotes.json")
HEARTBEAT_KEY = "__heartbeat__"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


DEFAULT_STALE_S = _int_env("REALTIME_STALE_S", 60)
HEARTBEAT_STALE_S = _int_env("REALTIME_HEARTBEAT_STALE_S", 120)
REST_HEARTBEAT_STALE_S = _int_env("QUOTES_POLL_HEARTBEAT_STALE_S", 90)   # 폴 주기(10s)×여유


def ws_enabled() -> bool:
    return os.getenv("REALTIME_ENABLED", "false").lower() == "true"


def poll_enabled() -> bool:
    return os.getenv("QUOTES_POLL_ENABLED", "false").lower() == "true"


def enabled() -> bool:
    """실시간 seam 활성 — WS 또는 REST 폴러 중 하나라도 켜져 있으면 참."""
    return ws_enabled() or poll_enabled()


# ── 순수 신선도 (폐형해 테스트 대상) ─────────────────────────────────────────

def _is_fresh(ts, now: float, max_age_s: float) -> bool:
    """ts 가 now 기준 max_age_s 이내인가. ts None/형식오류/미래과다 → False."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return False
    age = now - ts
    return -1.0 <= age <= max_age_s     # 약간의 시계 skew(미래 1s) 허용, 그 외 미래값 거부


# ── 캐시 읽기 (예외 무발) ─────────────────────────────────────────────────────

def _read_cache(path: str | None = None) -> dict:
    try:
        with open(CACHE_PATH if path is None else path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def heartbeat_age(cache: dict | None = None) -> float | None:
    """스트림 마지막 갱신 후 경과초. 없으면 None."""
    cache = _read_cache() if cache is None else cache
    hb = cache.get(HEARTBEAT_KEY) or {}
    try:
        return time.time() - float(hb.get("ts"))
    except (TypeError, ValueError):
        return None


def _live_cache() -> dict | None:
    """WS 캐시: 활성+heartbeat 신선이면 dict, 아니면 None(전체 폴백)."""
    if not ws_enabled():
        return None
    cache = _read_cache()
    if not cache:
        return None
    age = heartbeat_age(cache)
    if age is None or age > HEARTBEAT_STALE_S:
        return None       # 스트림 죽음/정지 → 캐시 전체 불신
    return cache


def _rest_cache() -> dict | None:
    """REST 폴 캐시(quotes_poller): 활성+heartbeat 신선이면 dict, 아니면 None."""
    if not poll_enabled():
        return None
    cache = _read_cache(REST_CACHE_PATH)
    if not cache:
        return None
    age = heartbeat_age(cache)
    if age is None or age > REST_HEARTBEAT_STALE_S:
        return None       # 폴러 죽음/장 마감 대기 → 캐시 전체 불신
    return cache


def _pick(cache: dict | None, symbol: str, max_age_s: int) -> dict | None:
    if not cache:
        return None
    e = cache.get(symbol) or cache.get(symbol.upper())
    if not isinstance(e, dict):
        return None
    return e if _is_fresh(e.get("ts"), time.time(), max_age_s) else None


def _entry(symbol: str, max_age_s: int, cache: dict | None = None) -> dict | None:
    """WS 신선 우선 → REST 폴 신선 → None. cache 인자는 테스트 주입용(WS 계층)."""
    e = _pick(_live_cache() if cache is None else cache, symbol, max_age_s)
    if e is not None:
        return e
    if cache is None:                       # 명시 주입 시엔 그 계층만 (테스트 결정성)
        return _pick(_rest_cache(), symbol, max_age_s)
    return None


# ── 공개 reader ───────────────────────────────────────────────────────────────

def get_price(symbol: str, *, max_age_s: int = DEFAULT_STALE_S) -> float | None:
    try:
        e = _entry(symbol, max_age_s)
        p = e.get("price") if e else None
        return float(p) if p else None
    except Exception:
        return None


def get_orderbook(symbol: str, *, max_age_s: int = DEFAULT_STALE_S) -> dict | None:
    try:
        e = _entry(symbol, max_age_s)
        if not e or (e.get("best_bid") is None and e.get("best_ask") is None):
            return None
        return {"bids": e.get("bids"), "asks": e.get("asks"),
                "best_bid": e.get("best_bid"), "best_ask": e.get("best_ask"), "ts": e.get("ts")}
    except Exception:
        return None


def best(symbol: str, side: str, *, max_age_s: int = DEFAULT_STALE_S) -> float | None:
    """체결 우호가: 매수(buy)=최우선 매도호가(ask)·매도(sell)=최우선 매수호가(bid)."""
    ob = get_orderbook(symbol, max_age_s=max_age_s)
    if not ob:
        return None
    px = ob.get("best_ask") if side == "buy" else ob.get("best_bid")
    try:
        return float(px) if px else None
    except (TypeError, ValueError):
        return None


def get_volume(symbol: str, *, max_age_s: int = DEFAULT_STALE_S) -> float | None:
    try:
        e = _entry(symbol, max_age_s)
        v = e.get("volume") if e else None
        return float(v) if v is not None else None
    except Exception:
        return None


def is_fresh(symbol: str, *, max_age_s: int = DEFAULT_STALE_S) -> bool:
    return _entry(symbol, max_age_s) is not None
