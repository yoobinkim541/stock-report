"""tests/test_institutional_flow.py — 13F 교차검증 시간 예산 (감사 #30)."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from reports.institutional_flow import rank_accumulation


def _accumulation_frame(seed: int, n: int = 80) -> pd.DataFrame:
    """매집 패턴 합성 OHLCV — accum_score 가 높게 나오는 상승추세+상승일 거래량 多."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-01-01", periods=n)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.004, 0.012, n)), index=idx)
    chg = close.diff().fillna(0.0)
    vol = pd.Series(np.where(chg.values >= 0, rng.uniform(8e6, 12e6, n), rng.uniform(1e6, 3e6, n)), index=idx)
    span = close * 0.015
    return pd.DataFrame({
        "Open": close, "High": close + span * 0.2, "Low": close - span * 0.8,
        "Close": close, "Volume": vol,
    })


def test_enrichment_respects_time_budget_and_skips_remaining_tickers(monkeypatch):
    """감사 #30 — fetch_13f(yfinance) 는 건별 타임아웃이 없어, 한 종목이 느려지면
    (hang 포함) enrich_top 개 전부에 대해 무한정 대기할 수 있었음.
    전체 시간 예산을 넘기면 나머지 종목은 13F 없이 스킵해야 한다."""
    tickers = ["T1", "T2", "T3", "T4", "T5"]
    data = {t: _accumulation_frame(seed=i) for i, t in enumerate(tickers)}

    def _fetcher(ts, days=160):
        return {t: data[t] for t in ts if t in data}

    calls = []

    def _slow_fetch_13f(ticker):
        calls.append(ticker)
        time.sleep(0.05)
        return {"held_pct": 0.5, "net_change": 0.02, "buyers": 3, "sellers": 1,
                "top_buyer": "X", "as_of": "2026Q1"}

    import reports.institutional_flow as inst_flow
    monkeypatch.setattr(inst_flow, "fetch_13f", _slow_fetch_13f)

    start = time.monotonic()
    result = rank_accumulation(
        tickers, price_fetcher=_fetcher, enrich=True, enrich_top=5, min_score=0, limit=10,
        enrich_budget_s=0.12,   # 5종목×0.05초=0.25초 예산 초과 유도
    )
    elapsed = time.monotonic() - start

    assert len(calls) < 5, "시간 예산을 넘겼는데도 나머지 종목까지 전부 13F 조회함"
    assert elapsed < 0.3, f"예산(0.12s)을 크게 초과해 실행됨: {elapsed:.2f}s"
    assert any(r.get("institutional") is None for r in result), "예산 초과로 스킵된 종목이 있어야 함"


def test_enrichment_runs_normally_within_budget(monkeypatch):
    tickers = ["T1", "T2"]
    data = {t: _accumulation_frame(seed=i) for i, t in enumerate(tickers)}

    def _fetcher(ts, days=160):
        return {t: data[t] for t in ts if t in data}

    def _fast_fetch_13f(ticker):
        return {"held_pct": 0.5, "net_change": 0.02, "buyers": 3, "sellers": 1,
                "top_buyer": "X", "as_of": "2026Q1"}

    import reports.institutional_flow as inst_flow
    monkeypatch.setattr(inst_flow, "fetch_13f", _fast_fetch_13f)

    result = rank_accumulation(
        tickers, price_fetcher=_fetcher, enrich=True, enrich_top=5, min_score=0, limit=10,
        enrich_budget_s=20.0,
    )

    assert all(r.get("institutional") is not None for r in result)
