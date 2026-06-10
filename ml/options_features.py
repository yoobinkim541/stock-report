"""ml/options_features.py — 옵션 체인 파생 지표 (yfinance)

종목별 옵션 체인에서 시장이 직접 가격에 반영한 기대를 추출한다:
  atm_iv            — 30일 내외 만기 ATM 내재변동성 (콜·풋 평균)
  pcr_volume/oi     — 풋/콜 거래량·미결제약정 비율 (>1 = 하방 베팅 우세)
  iv_skew           — OTM 풋 IV − OTM 콜 IV (양수 = 하방 헤지 수요)
  expected_move_pct — ATM IV 기반 30일 기대변동폭 (±%)

1시간 파일 캐시 (~/.cache/options_metrics.json) — 옵션 체인 조회는 느림.
한국 주식(.KS/.KQ)은 옵션 데이터 없음 → None.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

CACHE_PATH    = Path.home() / ".cache" / "options_metrics.json"
CACHE_TTL_SEC = 3600
TARGET_DTE    = 30   # 목표 만기 (일)


def _load_cache() -> dict:
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache))
    except Exception:
        pass


def fetch_option_metrics(ticker: str, force: bool = False) -> dict | None:
    """단일 종목 옵션 지표. 옵션 미상장·조회 실패 시 None."""
    if ticker.endswith((".KS", ".KQ")) or ticker.startswith("^"):
        return None

    cache = _load_cache()
    hit = cache.get(ticker)
    if not force and hit and time.time() - hit.get("ts", 0) < CACHE_TTL_SEC:
        return hit.get("metrics")

    metrics = _compute_metrics(ticker)
    cache[ticker] = {"ts": time.time(), "metrics": metrics}
    _save_cache(cache)
    return metrics


def _compute_metrics(ticker: str) -> dict | None:
    import yfinance as yf
    try:
        tk = yf.Ticker(ticker)
        expiries = tk.options
        if not expiries:
            return None

        hist = tk.history(period="1d")
        if hist.empty:
            return None
        spot = float(hist["Close"].iloc[-1])

        # yfinance impliedVolatility 필드는 신뢰 불가(플레이스홀더 빈발) →
        # 체결가(lastPrice) 기반 ATM 스트래들로 IV·기대변동폭을 직접 역산
        def _near_price(df, target, n=2) -> float:
            d = df[(df["lastPrice"] > 0)]
            if d.empty:
                return np.nan
            d = d.assign(dist=(d["strike"] - target).abs()).nsmallest(n, "dist")
            return float(d["lastPrice"].iloc[0])

        now = datetime.now(timezone.utc)
        def _dte_of(e: str) -> int:
            return (datetime.strptime(e, "%Y-%m-%d").replace(tzinfo=timezone.utc) - now).days
        candidates = sorted((e for e in expiries if _dte_of(e) > 0),
                            key=lambda e: abs(_dte_of(e) - TARGET_DTE))[:4]

        for exp in candidates:
            chain = tk.option_chain(exp)
            calls, puts = chain.calls, chain.puts
            if calls.empty or puts.empty:
                continue
            dte = _dte_of(exp)
            atm_call = _near_price(calls, spot)
            atm_put  = _near_price(puts, spot)
            if not (np.isfinite(atm_call) and np.isfinite(atm_put)):
                continue

            # 스트래들 근사: straddle ≈ 0.8 × S × σ × √T
            straddle_pct = (atm_call + atm_put) / spot
            t_frac  = dte / 365
            atm_iv  = straddle_pct / (0.8 * math.sqrt(t_frac))
            if not (0.05 < atm_iv < 3.0):
                continue

            # 풋/콜 비율 (거래량·미결제약정)
            cv, pv   = float(calls["volume"].fillna(0).sum()), float(puts["volume"].fillna(0).sum())
            coi, poi = float(calls["openInterest"].fillna(0).sum()), float(puts["openInterest"].fillna(0).sum())
            pcr_volume = pv / cv if cv > 0 else None
            pcr_oi     = poi / coi if coi > 0 else None

            # 스큐 프록시: 등거리(5%) OTM 풋가격 / OTM 콜가격 (>1 = 하방 헤지 수요 우세)
            otm_put, otm_call = _near_price(puts, spot * 0.95), _near_price(calls, spot * 1.05)
            skew_ratio = (otm_put / otm_call
                          if np.isfinite(otm_put) and np.isfinite(otm_call) and otm_call > 0
                          else None)

            return {
                "expiry":            exp,
                "dte":               dte,
                "spot":              round(spot, 2),
                "atm_iv":            round(float(atm_iv), 4),
                "pcr_volume":        round(pcr_volume, 3) if pcr_volume is not None else None,
                "pcr_oi":            round(pcr_oi, 3) if pcr_oi is not None else None,
                "skew_ratio":        round(skew_ratio, 3) if skew_ratio is not None else None,
                # 30일 환산 기대변동폭
                "expected_move_pct": round(float(atm_iv) * math.sqrt(30 / 365), 4),
            }
        return None
    except Exception as e:
        logger.debug("옵션 지표 조회 실패 (%s): %s", ticker, e)
        return None
