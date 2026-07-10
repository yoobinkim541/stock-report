#!/usr/bin/env python3
"""intraday_universe.py — 단기 트레이딩 동적 유니버스 스캐너 ("stocks in play").

오늘 거래대금·등락폭이 큰 **유동성 있는** 종목을 선발해 단기 모의 엔진·실시간 스트림
워치리스트에 공급한다. 선발은 '어디를 볼지'만 — 진입 타이밍은 축(ml/intraday_axes)이 결정.

소스:
  KR — KIS 거래대금 순위 API(kis_quote.volume_rank_kr, 실전 read-only) → 필터(가격·거래대금·
       보통주만·ETF/스팩 제외) → |등락률| 상위 top-K
  US — 기존 S&P500 히트맵 스냅샷(~/reports/ml-cache/sp500_heatmap.json, 20분 크론) 재사용
       → 시총 하한 → |등락률| 상위 top-K (+ QQQ 코어)

상태: ~/.cache/intraday_universe.json (atomic write)
  {"KR": {"symbols": [...], "updated": epoch, "src": "scan|env"}, "US": {...}}

규율:
  - **히스테리시스**: keep(보유·당일 기편입) 심볼은 스캔에서 밀려도 유지(포지션·프로파일 연속성).
  - 스캔 실패/스냅샷 stale → 기존 상태 유지, 그것도 없으면 정적 env(INTRADAY_UNIVERSE_*) 폴백.
  - 표기는 전부 base 심볼(intraday_bars.base_symbol) — "005930"·"NVDA".
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from providers.intraday_bars import base_symbol

logger = logging.getLogger(__name__)

STATE_PATH = Path(os.path.expanduser("~/.cache/intraday_universe.json"))
_HEATMAP_PATH = Path(os.path.expanduser("~/reports/ml-cache/sp500_heatmap.json"))
_HEATMAP_MAX_AGE_S = 90 * 60          # views.sp500_heatmap 와 동일 신선도 기준

# 정적 폴백 기본값 — 스캐너 불능 시에도 KR 유동성 최상위로 동작
_DEFAULT_KR = "005930,000660,373220,005380,035420"
_DEFAULT_US = "QQQ"
_DEFAULT_LEVERAGE_MAP = (
    "QQQ:TQQQ,NVDA:NVDL,TSLA:TSLL,AAPL:AAPU,AMZN:AMZU,GOOGL:GGLL,"
    "MSFT:MSFU,META:METU,COIN:CONL,PLTR:PLTU,MSTR:MSTU"
)

# KR 비보통주·상품 제외 (이름 기반 — 완전하지 않아도 graceful, 보통주 코드 끝자리 0 필터가 1차)
_KR_NAME_EXCLUDE = ("스팩", "ETN", "레버리지", "인버스", "선물", "채권")
_KR_ETF_PREFIX = ("KODEX", "TIGER", "KBSTAR", "RISE", "ACE", "SOL", "PLUS",
                  "ARIRANG", "HANARO", "KOSEF", "KIWOOM", "WON", "TIMEFOLIO", "UNICORN")


def scan_enabled() -> bool:
    return os.getenv("INTRADAY_SCAN_ENABLED", "true").lower() == "true"


def _feature_on() -> bool:
    """단기 서브시스템 활성 여부 — bar 수집 또는 모의 엔진 중 하나라도 켜져 있으면 참."""
    return (os.getenv("INTRADAY_BARS_ENABLED", "false").lower() == "true"
            or os.getenv("INTRADAY_MOCK_ENABLED", "false").lower() == "true")


def _env_list(name: str, default: str) -> list[str]:
    return [base_symbol(t) for t in (os.getenv(name, default) or "").split(",") if t.strip()]


def leverage_enabled() -> bool:
    return os.getenv("INTRADAY_LEVERAGE_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def leverage_map() -> dict[str, str]:
    """기초자산 단기 신호 → 체결할 레버리지 ETF. env 로 완전 교체 가능."""
    raw = os.getenv("INTRADAY_LEVERAGE_MAP", _DEFAULT_LEVERAGE_MAP)
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        if ":" not in part:
            continue
        src, dst = [base_symbol(x) for x in part.split(":", 1)]
        if src and dst and src != dst:
            out[src] = dst
    return out


def expand_with_leverage(symbols: list[str]) -> list[str]:
    """스트림/분봉 수집용 확장 — 기초자산과 체결 ETF를 함께 유지."""
    out = [base_symbol(s) for s in symbols if s]
    if leverage_enabled():
        mp = leverage_map()
        out.extend(mp[s] for s in list(out) if s in mp)
    return list(dict.fromkeys(out))


def static_universe(market: str) -> list[str]:
    if market.upper() == "KR":
        return _env_list("INTRADAY_UNIVERSE_KR", _DEFAULT_KR)
    return _env_list("INTRADAY_UNIVERSE_US", _DEFAULT_US)


def _top_k(market: str) -> int:
    env = "INTRADAY_SCAN_TOP_KR" if market.upper() == "KR" else "INTRADAY_SCAN_TOP_US"
    try:
        return max(1, int(os.getenv(env, "5" if market.upper() == "KR" else "4")))
    except ValueError:
        return 5


# ── 필터 (순수 — 단위 테스트 대상) ────────────────────────────────────────────

def filter_kr_candidates(rows: list[dict], *, min_price: float = 1000.0,
                         min_turnover: float = 30e9) -> list[dict]:
    """KIS 순위 행 → 단타 가능 보통주만. rows: [{code,name,price,chg_pct,turnover},...]"""
    out = []
    for r in rows or []:
        code = str(r.get("code") or "").strip()
        name = str(r.get("name") or "").strip()
        if len(code) != 6 or not code.isdigit() or not code.endswith("0"):
            continue                      # 보통주 코드 끝자리 0 (우선주 5/7/9/K 제외)
        if (r.get("price") or 0) < min_price:
            continue
        if (r.get("turnover") or 0) < min_turnover:
            continue                      # 유동성 하한 — 스프레드·모의체결 신뢰
        up = name.upper()
        if any(x in name for x in _KR_NAME_EXCLUDE) or up.startswith(_KR_ETF_PREFIX):
            continue
        if name.endswith("우") or name[-2:] in ("우B", "우C"):
            continue
        out.append(r)
    return out


def filter_us_candidates(rows: list[dict], *, min_mcap: float = 10e9) -> list[dict]:
    """히트맵 행 → 시총 하한 통과분. rows: [{ticker, market_cap, pct},...]"""
    return [r for r in rows or []
            if (r.get("market_cap") or 0) >= min_mcap and r.get("pct") is not None
            and r.get("ticker")]


def rank_by_move(rows: list[dict], key: str, k: int) -> list[str]:
    """|등락률| 상위 k 심볼 (동률 시 원 순서 유지)."""
    scored = sorted(rows, key=lambda r: abs(float(r.get(key) or 0.0)), reverse=True)
    out: list[str] = []
    id_key = "code" if rows and "code" in rows[0] else "ticker"
    for r in scored:
        s = base_symbol(str(r.get(id_key) or ""))
        if s and s not in out:
            out.append(s)
        if len(out) >= k:
            break
    return out


def merge_with_keep(keep: list[str], scanned: list[str], top_k: int) -> list[str]:
    """히스테리시스 — keep(보유·기편입)은 무조건 유지, 스캔분은 잔여 슬롯만."""
    keep = [base_symbol(s) for s in keep if s]
    out = list(dict.fromkeys(keep))
    for s in scanned:
        if len(out) >= max(top_k, len(keep)):
            break
        if s not in out:
            out.append(s)
    return out


# ── 스캔 (네트워크·파일 IO — graceful None) ───────────────────────────────────

def scan_kr(top_k: int | None = None) -> list[str] | None:
    """KIS 거래대금 순위 → 필터 → |등락률| 상위. 실패/비활성 None."""
    try:
        from providers import kis_quote
        rows = kis_quote.volume_rank_kr()
    except Exception as e:
        logger.debug("KR 순위 조회 실패: %s", e)
        return None
    if not rows:
        return None
    try:
        min_turnover = float(os.getenv("INTRADAY_MIN_TURNOVER_KRW", str(30e9)))
    except ValueError:
        min_turnover = 30e9
    cands = filter_kr_candidates(rows, min_turnover=min_turnover)
    got = rank_by_move(cands, "chg_pct", top_k or _top_k("KR"))
    return got or None


def scan_us(top_k: int | None = None) -> list[str] | None:
    """S&P500 히트맵 스냅샷 → 시총 하한 → |등락률| 상위 + QQQ 코어. stale/실패 None."""
    try:
        if not _HEATMAP_PATH.exists():
            return None
        if time.time() - _HEATMAP_PATH.stat().st_mtime > _HEATMAP_MAX_AGE_S:
            return None                    # 20분 크론 스냅샷이 90분 이상 stale → 신뢰 불가
        rows = json.loads(_HEATMAP_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("US 히트맵 로드 실패: %s", e)
        return None
    movers = rank_by_move(filter_us_candidates(rows), "pct", top_k or _top_k("US"))
    if not movers:
        return None
    return list(dict.fromkeys(["QQQ"] + movers))   # QQQ 코어(기 스트리밍) + 무버


# ── 상태 파일 (reader/writer) ─────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        import safe_io
        safe_io.atomic_write_json(str(STATE_PATH), state)
    except Exception:
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            tmp.rename(STATE_PATH)
        except OSError as e:
            logger.warning("유니버스 상태 저장 실패: %s", e)


def refresh(market: str, keep: list[str] | None = None, *,
            max_age_s: int = 20 * 60) -> list[str]:
    """유니버스 갱신 (엔진이 개장 시+주기 호출). 신선하면 기존 유지, 스캔 실패 시 폴백 체인.

    반환: 현재 유효 유니버스 (항상 non-empty — 최후 폴백은 정적 env).
    """
    mk = market.upper()
    state = _load_state()
    ent = state.get(mk) or {}
    now = time.time()
    if ent.get("symbols") and now - float(ent.get("updated") or 0) < max_age_s:
        if not keep or all(base_symbol(s) in ent["symbols"] for s in keep):
            return list(ent["symbols"])
    scanned = None
    if scan_enabled():
        scanned = scan_kr() if mk == "KR" else scan_us()
    if scanned:
        syms = merge_with_keep(list(keep or []), scanned, _top_k(mk))
        src = "scan"
    elif ent.get("symbols"):
        syms = merge_with_keep(list(keep or []), list(ent["symbols"]), _top_k(mk))
        src = ent.get("src", "scan")       # 스캔 실패 — 직전 결과 유지
    else:
        syms = merge_with_keep(list(keep or []), static_universe(mk), _top_k(mk))
        src = "env"
    state[mk] = {"symbols": syms, "updated": now, "src": src}
    _save_state(state)
    return syms


def current_universe(market: str) -> list[str]:
    """현재 유니버스 (read-only — kis_stream·대시보드). 상태 없으면 정적 env."""
    ent = _load_state().get(market.upper()) or {}
    syms = ent.get("symbols") or []
    return list(syms) if syms else static_universe(market)


def watchlist_symbols() -> list[str]:
    """kis_stream 워치리스트 편입용 KR+US 합본 — 단기 서브시스템 비활성 시 빈 리스트."""
    if not _feature_on():
        return []
    return current_universe("KR") + expand_with_leverage(current_universe("US"))
