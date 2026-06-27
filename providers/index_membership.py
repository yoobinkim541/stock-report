#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/index_membership.py — 교차시장 지수 멤버십 (Phase B / §A) — point-in-time.

목적: 생존편향 제거 — 시점 t 에 실제 지수에 있던 종목(이후 편출/상폐분 포함)을 반환.

데이터(2026.06 서버 실측):
  ✅ US S&P500: fja05680/sp500 `...Components & Changes (Updated).csv` (raw GitHub) — 1996-01-02~,
     date→tickers 2712 스냅샷. 생존편향 0. (NASDAQ100 시점별은 무료 부재 → 현재만 위키, 한계 명시.)
  ✅ KR: providers.kr_market_data (marcap 시총 상위 N) 위임 — 1995~.

공통 인터페이스로 KR/US 어댑터. change_events = 인접 스냅샷 diff(편입/퇴출). 네트워크는 캐시+graceful.
"""
from __future__ import annotations

import io
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache"))
_SP500_FN = "S&P 500 Historical Components & Changes (Updated).csv"
_SP500_URL = "https://raw.githubusercontent.com/fja05680/sp500/master/" + urllib.parse.quote(_SP500_FN)
_SP500_CACHE = _CACHE_DIR / "sp500_history.csv"
_SP500_TTL_H = 24 * 7


def _fetch_sp500_history():
    """fja05680 S&P500 시점별 구성 DataFrame[date, tickers]. 캐시(주간). 실패 시 None."""
    try:
        import pandas as pd
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fresh = _SP500_CACHE.exists() and (time.time() - _SP500_CACHE.stat().st_mtime) < _SP500_TTL_H * 3600
        if not fresh:
            req = urllib.request.Request(_SP500_URL, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=30).read()
            tmp = _SP500_CACHE.with_suffix(".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, _SP500_CACHE)
        return pd.read_csv(_SP500_CACHE)
    except Exception as e:
        logger.warning("sp500 history 로드 실패: %s", e)
        try:
            import pandas as pd
            if _SP500_CACHE.exists():
                return pd.read_csv(_SP500_CACHE)   # 만료 캐시라도 폴백
        except Exception:
            pass
        return None


def _sp500_snapshots():
    """[(date_str, frozenset(tickers))] 시간순. 실패 시 []."""
    df = _fetch_sp500_history()
    if df is None or len(df) == 0:
        return []
    dcol, tcol = df.columns[0], df.columns[1]
    out = []
    for _, r in df.iterrows():
        toks = [t.strip().upper() for t in str(r[tcol]).split(",") if t.strip()]
        out.append((str(r[dcol])[:10], frozenset(toks)))
    out.sort(key=lambda x: x[0])
    return out


def members_asof(market: str, date: str, *, n: int = 200) -> list[str]:
    """시점 t 멤버십(생존편향 제거). market: 'sp500' / 'kr'. 실패 시 []."""
    market = (market or "").lower()
    if market in ("sp500", "us", "spx"):
        snaps = _sp500_snapshots()
        prior = [s for d, s in snaps if d <= str(date)[:10]]
        return sorted(prior[-1]) if prior else []
    if market in ("kr", "kospi", "krx"):
        from providers import kr_market_data as km
        return km.top_n_by_marcap(date, n=n)
    return []


def change_events(market: str) -> list[dict]:
    """편입/퇴출 이벤트 [{date, ticker, action}] — 인접 스냅샷 diff. market='sp500'."""
    market = (market or "").lower()
    if market not in ("sp500", "us", "spx"):
        return []   # KR 은 marcap top-N diff(별도) / NASDAQ100 무료 부재
    snaps = _sp500_snapshots()
    events = []
    for i in range(1, len(snaps)):
        d, cur = snaps[i]
        prev = snaps[i - 1][1]
        for t in cur - prev:
            events.append({"date": d, "ticker": t, "action": "add"})
        for t in prev - cur:
            events.append({"date": d, "ticker": t, "action": "remove"})
    return events


def removals(market: str = "sp500") -> dict:
    """{ticker: 첫 퇴출일} — 지수 편출 라벨(생존편향 제거 학습용). distress/M&A 구분은 별도 필요."""
    out = {}
    for e in change_events(market):
        if e["action"] == "remove" and e["ticker"] not in out:
            out[e["ticker"]] = e["date"]
    return out
