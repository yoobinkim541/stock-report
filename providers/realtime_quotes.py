#!/usr/bin/env python3
"""realtime_quotes.py — 실시간 시세 캐시의 **읽기전용 클라이언트** (폴백의 단일 seam).

소스 2계층 (WS 신선 > REST 신선 > None→yfinance), 각 계층은 Redis 신선 > 파일 신선:
  1차: kis_stream.py → Redis quotes:ws + ~/.cache/kis_realtime_quotes.json (KIS WS 틱·호가 — 세션 41심볼 캡)
  2차: quotes_poller.py → Redis quotes:rest + ~/.cache/rest_quotes.json (토스 배치 200 + 키움 KR 폴백 —
       WS 캡 밖 롱테일 현재가. 가격만 — 호가/체결강도는 WS 전용)

핵심 계약: **절대 예외를 던지지 않고**, 비활성/없음/stale 이면 None 을 반환해
호출부가 기존 yfinance 경로로 우아하게 폴백하게 한다. 소비자는 이 seam 만 안다.

신선도 2단 (계층별 독립):
  1) heartbeat — writer 프로세스가 살아있고 최근 갱신했는가. Redis가 stale이면 파일 fallback, 둘 다 죽었으면 캐시 전체 불신.
  2) 심볼별 ts — 해당 종목 값이 max_age_s 이내인가.
"""
from __future__ import annotations

import json
import os
import time
from math import isfinite

CACHE_PATH = os.path.expanduser("~/.cache/kis_realtime_quotes.json")
REST_CACHE_PATH = os.path.expanduser("~/.cache/rest_quotes.json")
HEARTBEAT_KEY = "__heartbeat__"
WS_REDIS_KEY = "quotes:ws"
REST_REDIS_KEY = "quotes:rest"

_REDIS_CLIENT = None
_REDIS_CLIENT_URL = None


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


def _redis_url() -> str:
    return os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_URL") or ""


def _redis_client():
    global _REDIS_CLIENT, _REDIS_CLIENT_URL

    url = _redis_url()
    if _REDIS_CLIENT is not None and _REDIS_CLIENT_URL == url:
        return _REDIS_CLIENT
    import redis

    _REDIS_CLIENT = redis.Redis.from_url(url, decode_responses=True, socket_timeout=1.0, socket_connect_timeout=1.0)
    _REDIS_CLIENT_URL = url
    return _REDIS_CLIENT


def _read_redis_cache(key: str) -> dict:
    if not _redis_url():
        return {}
    try:
        raw = _redis_client().get(key)
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_redis_cache(key: str, payload: dict, ttl_s: int | None = None) -> bool:
    if not _redis_url() or not isinstance(payload, dict):
        return False
    try:
        ttl = int(ttl_s or os.getenv("REALTIME_REDIS_TTL_S", "180"))
        _redis_client().setex(key, ttl, json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return True
    except Exception:
        return False


def write_ws_cache(payload: dict) -> bool:
    return _write_redis_cache(os.getenv("REALTIME_WS_REDIS_KEY", WS_REDIS_KEY), payload)


def write_rest_cache(payload: dict) -> bool:
    return _write_redis_cache(os.getenv("REALTIME_REST_REDIS_KEY", REST_REDIS_KEY), payload)


# ── 순수 신선도 (폐형해 테스트 대상) ─────────────────────────────────────────

def _is_fresh(ts, now: float, max_age_s: float) -> bool:
    """ts 가 now 기준 max_age_s 이내인가. ts None/형식오류/미래과다 → False."""
    try:
        ts = float(ts)
        now = float(now)
        max_age_s = float(max_age_s)
    except (TypeError, ValueError, OverflowError):
        return False
    if not isfinite(ts) or not isfinite(now) or not isfinite(max_age_s) or max_age_s < 0.0:
        return False
    age = now - ts
    return isfinite(age) and -1.0 <= age <= max_age_s  # 약간의 시계 skew(미래 1s) 허용


# ── 캐시 읽기 (예외 무발) ─────────────────────────────────────────────────────

def _read_cache(path: str | None = None) -> dict:
    target = CACHE_PATH if path is None else path
    if target == CACHE_PATH:
        redis_cache = _read_redis_cache(os.getenv("REALTIME_WS_REDIS_KEY", WS_REDIS_KEY))
        redis_age = heartbeat_age(redis_cache) if redis_cache else None
        if redis_cache and redis_age is not None and redis_age <= HEARTBEAT_STALE_S:
            return redis_cache
    if target == REST_CACHE_PATH:
        redis_cache = _read_redis_cache(os.getenv("REALTIME_REST_REDIS_KEY", REST_REDIS_KEY))
        redis_age = heartbeat_age(redis_cache) if redis_cache else None
        if redis_cache and redis_age is not None and redis_age <= REST_HEARTBEAT_STALE_S:
            return redis_cache
    try:
        with open(target, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def heartbeat_age(cache: dict | None = None) -> float | None:
    """스트림 마지막 갱신 후 경과초. 없으면 None."""
    cache = _read_cache() if cache is None else cache
    hb = cache.get(HEARTBEAT_KEY) or {}
    try:
        age = time.time() - float(hb.get("ts"))
    except (TypeError, ValueError):
        return None
    return age if isfinite(age) else None


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


def _pick(cache: dict | None, symbol: str, max_age_s: int, *, now: float | None = None) -> dict | None:
    if not cache:
        return None
    e = cache.get(symbol) or cache.get(symbol.upper())
    if not isinstance(e, dict):
        return None
    return e if _is_fresh(e.get("ts"), time.time() if now is None else now, max_age_s) else None


def read_realtime_snapshot() -> dict[str, dict]:
    """Read each live quote layer once for a request.

    Consumers should pass this snapshot through their symbol loop instead of
    calling the scalar readers repeatedly. Empty dictionaries are intentional:
    they preserve the existing graceful fallback contract.
    """

    return {
        "ws": _live_cache() or {},
        "rest": _rest_cache() or {},
    }


def entry_from_snapshot(
    symbol: str,
    *,
    max_age_s: int = DEFAULT_STALE_S,
    snapshot: dict | None = None,
    now: float | None = None,
) -> dict | None:
    """Return the best fresh entry from a previously read quote snapshot."""

    layers = snapshot if isinstance(snapshot, dict) else read_realtime_snapshot()
    entry = _pick(layers.get("ws"), symbol, max_age_s, now=now)
    if entry is not None:
        return entry
    return _pick(layers.get("rest"), symbol, max_age_s, now=now)


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


def quote_health(
    symbol: str,
    *,
    now: float | None = None,
    max_age_s: int = DEFAULT_STALE_S,
) -> dict[str, object]:
    """Return replayable health for one quote without changing quote reads."""

    try:
        current = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        return {"status": "pause", "reason": "invalid_quote_now", "age_seconds": None}
    if not isfinite(current):
        return {"status": "pause", "reason": "invalid_quote_now", "age_seconds": None}
    try:
        max_age = float(max_age_s)
    except (TypeError, ValueError):
        return {"status": "pause", "reason": "invalid_quote_age", "age_seconds": None}
    if not isfinite(max_age) or max_age < 0.0:
        return {"status": "pause", "reason": "invalid_quote_age", "age_seconds": None}
    name = str(symbol or "").strip().upper()
    if not name:
        return {"status": "pause", "reason": "missing_quote_symbol", "age_seconds": None}
    if not enabled():
        return {"status": "pause", "reason": "quote_source_disabled", "age_seconds": None, "source": None}

    source_specs = (
        ("kis_ws", CACHE_PATH, ws_enabled()),
        ("rest", REST_CACHE_PATH, poll_enabled()),
    )
    health = source_health(now=current)
    candidates: list[tuple[str, dict]] = []
    invalid_entries: list[tuple[str, str]] = []
    missing_entries: list[str] = []
    for source, path, active in source_specs:
        if not active or health.get(source, {}).get("status") != "fresh":
            continue
        cache = _read_cache(path)
        entry = cache.get(name) or cache.get(symbol) if isinstance(cache, dict) else None
        if not isinstance(entry, dict):
            missing_entries.append(source)
            continue
        try:
            age = current - float(entry.get("ts"))
        except (TypeError, ValueError, OverflowError):
            invalid_entries.append((source, "invalid_quote_timestamp"))
            continue
        if not isfinite(age):
            invalid_entries.append((source, "invalid_quote_timestamp"))
            continue
        if age < -1.0:
            invalid_entries.append((source, "future_quote_timestamp"))
            continue
        age = max(0.0, age)
        if age > max_age:
            invalid_entries.append((source, "stale_quote"))
            continue
        candidates.append((source, {**entry, "age_seconds": age}))

    if candidates:
        source, entry = max(candidates, key=lambda item: _entry_timestamp(item[1]))
        return {"status": "fresh", "reason": "fresh", "age_seconds": entry["age_seconds"], "source": source}
    if invalid_entries:
        source, reason = invalid_entries[0]
        age = None
        if reason == "stale_quote":
            cache = _read_cache(CACHE_PATH if source == "kis_ws" else REST_CACHE_PATH)
            try:
                age = max(0.0, current - float((cache.get(name) or cache.get(symbol) or {}).get("ts")))
            except (AttributeError, TypeError, ValueError):
                pass
        return {"status": "pause", "reason": reason, "age_seconds": age, "source": source}
    if missing_entries:
        return {"status": "pause", "reason": "missing_quote", "age_seconds": None, "source": missing_entries[0]}

    active_sources = {source for source, _path, active in source_specs if active}
    active_health = [
        (source, record) for source, record in health.items()
        if source in active_sources and record.get("status") != "disabled"
    ]
    source, record = active_health[0] if active_health else (None, None)
    return {
        "status": "pause",
        "reason": str((record or {}).get("reason") or "missing_quote"),
        "age_seconds": (record or {}).get("age_seconds"),
        "source": source,
    }


def source_health(*, now: float | None = None) -> dict[str, dict[str, object]]:
    """Summarize heartbeat health for the existing WS and REST cache layers."""

    try:
        current = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        current = None
    if current is not None and not isfinite(current):
        current = None
    result: dict[str, dict[str, object]] = {}
    for name, path, enabled_flag, max_age in (
        ("kis_ws", CACHE_PATH, ws_enabled(), HEARTBEAT_STALE_S),
        ("rest", REST_CACHE_PATH, poll_enabled(), REST_HEARTBEAT_STALE_S),
    ):
        cache = _read_cache(path)
        if not enabled_flag:
            result[name] = {"status": "disabled", "reason": "source_disabled", "age_seconds": None}
            continue
        if current is None:
            result[name] = {"status": "pause", "reason": "invalid_quote_now", "age_seconds": None}
            continue
        heartbeat = (cache.get(HEARTBEAT_KEY) or {}).get("ts") if isinstance(cache, dict) else None
        try:
            age = current - float(heartbeat)
        except (TypeError, ValueError):
            result[name] = {"status": "pause", "reason": "missing_heartbeat", "age_seconds": None}
            continue
        if not isfinite(age):
            result[name] = {"status": "pause", "reason": "invalid_heartbeat", "age_seconds": None}
        elif age < -1.0:
            result[name] = {"status": "pause", "reason": "future_heartbeat", "age_seconds": 0.0}
        elif age > max_age:
            result[name] = {"status": "pause", "reason": "stale_heartbeat", "age_seconds": age}
        else:
            result[name] = {"status": "fresh", "reason": "fresh", "age_seconds": max(0.0, age)}
    return result


def _entry_timestamp(entry: dict) -> float:
    try:
        return float(entry.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0
