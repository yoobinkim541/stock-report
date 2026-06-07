"""ml/leverage_backtester.py — 레버리지 ETF 손익비 분석

목적: 하락장 진입 타점별 레버리지 ETF(QLD/TQQQ/SOXL/UPRO) vs SGOV
     조건부 수익 분포 계산 → ML 입력 피처 및 기대수익 라벨 생성

공개 API:
    build_leverage_dataset(days)       → 학습 데이터셋
    compute_entry_stats(entries)       → 진입 통계 요약
    get_current_entry_context()        → 현재 시황 기반 분석 딕셔너리
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 레버리지 ETF 정의 ─────────────────────────────────────────────────────────

INSTRUMENTS = {
    "SGOV":  {"ticker": "SGOV",  "leverage": 1.0, "expense": 0.0009, "underlying": "SGOV"},
    "QLD":   {"ticker": "QLD",   "leverage": 2.0, "expense": 0.0095, "underlying": "QQQ"},
    "TQQQ":  {"ticker": "TQQQ",  "leverage": 3.0, "expense": 0.0099, "underlying": "QQQ"},
    "SOXL":  {"ticker": "SOXL",  "leverage": 3.0, "expense": 0.0173, "underlying": "SMH"},
    "UPRO":  {"ticker": "UPRO",  "leverage": 3.0, "expense": 0.0093, "underlying": "SPY"},
}

HORIZONS = [21, 42, 63, 126]   # 1M / 2M / 3M / 6M (영업일)
DRAWDOWN_BUCKETS = [
    (-0.05, 0.0),    # 0~5%  낙폭 (정상)
    (-0.10, -0.05),  # 5~10%
    (-0.15, -0.10),  # 10~15%
    (-0.20, -0.15),  # 15~20%
    (-0.30, -0.20),  # 20~30%
    (-0.50, -0.30),  # 30%+
]


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def _fetch(tickers: list[str], days: int = 2520) -> dict[str, pd.Series]:
    """yfinance로 종가 시리즈 다운로드 (캐시 적용)."""
    from ml.data_pipeline import fetch_prices
    prices = fetch_prices(tickers, days=days)
    return {t: df["Close"] for t, df in prices.items() if "Close" in df.columns}


# ── 드로다운 계산 ─────────────────────────────────────────────────────────────

def rolling_drawdown(close: pd.Series) -> pd.Series:
    """현재까지 ATH 대비 낙폭 (0 이하)."""
    peak = close.cummax()
    return (close / peak - 1).rename("drawdown")


# ── 진입 이벤트 탐지 ──────────────────────────────────────────────────────────

@dataclass
class EntryEvent:
    date: pd.Timestamp
    drawdown: float          # QQQ 낙폭 at entry
    vix: float
    rsi: float
    fg_proxy: float
    ma200_gap: float         # (price/MA200 - 1)
    forward_returns: dict    # {instrument: {horizon: return}}
    features: dict           # 기타 ML 피처


def find_entry_events(
    close_map: dict[str, pd.Series],
    vix:        pd.Series,
    rsi:        pd.Series,
    fg:         pd.Series,
    min_drawdown: float = -0.05,  # 최소 낙폭 진입 조건
) -> list[EntryEvent]:
    """QQQ 낙폭 기준 진입 이벤트 탐지 (비겹침 30일 쿨다운)."""
    qqq = close_map.get("QQQ")
    if qqq is None:
        return []

    dd      = rolling_drawdown(qqq)
    ma200   = qqq.rolling(200, min_periods=60).mean()
    ma200_g = (qqq / ma200 - 1).fillna(0)

    common = qqq.index
    for s in [vix, rsi, fg]:
        if s is not None:
            common = common.intersection(s.reindex(common).dropna().index)

    events: list[EntryEvent] = []
    last_entry = pd.Timestamp("1900-01-01")
    cooldown   = pd.Timedelta(days=30)

    for date in common:
        if date - last_entry < cooldown:
            continue
        d = float(dd.get(date, 0))
        if d > min_drawdown:
            continue   # 낙폭 미달

        # 최대 잔여 기간 확인
        max_h = max(HORIZONS)
        future = qqq.loc[date:]
        if len(future) < max_h:
            continue

        # 미래 수익률 계산
        fwd: dict[str, dict] = {}
        for name, info in INSTRUMENTS.items():
            s = close_map.get(info["ticker"])
            if s is None:
                continue
            fwd[name] = {}
            for h in HORIZONS:
                try:
                    future_s = s.loc[date:]
                    if len(future_s) > h:
                        fwd[name][h] = float(future_s.iloc[h] / future_s.iloc[0] - 1)
                except Exception:
                    pass

        events.append(EntryEvent(
            date        = date,
            drawdown    = d,
            vix         = float(vix.get(date, np.nan)),
            rsi         = float(rsi.get(date, np.nan)),
            fg_proxy    = float(fg.get(date, np.nan)),
            ma200_gap   = float(ma200_g.get(date, 0)),
            forward_returns = fwd,
            features    = {
                "drawdown":    d,
                "vix":         float(vix.get(date, np.nan)),
                "rsi":         float(rsi.get(date, np.nan)),
                "fg_proxy":    float(fg.get(date, np.nan)),
                "ma200_gap":   float(ma200_g.get(date, 0)),
                "mom_20d":     float((qqq.get(date, np.nan) / qqq.shift(20).get(date, np.nan) - 1)
                                     if date in qqq.index else np.nan),
            },
        ))
        last_entry = date

    logger.info("진입 이벤트: %d건 (min_dd=%.0f%%)", len(events), min_drawdown * 100)
    return events


# ── 진입 통계 집계 ────────────────────────────────────────────────────────────

@dataclass
class InstrumentStats:
    name:          str
    n_entries:     int
    median_ret:    dict   # {horizon: median_return}
    p25_ret:       dict   # 25th percentile
    p75_ret:       dict   # 75th percentile
    hit_rate:      dict   # P(positive return)
    max_drawdown:  float  # worst case realized loss
    calmar:        dict   # median_ret / |max_drawdown|


def compute_entry_stats(
    events: list[EntryEvent],
    drawdown_range: tuple[float, float] = (-1.0, 0.0),
) -> dict[str, InstrumentStats]:
    """낙폭 구간별 진입 통계."""
    lo, hi = drawdown_range
    subset = [e for e in events if lo <= e.drawdown < hi]
    if not subset:
        return {}

    stats: dict[str, InstrumentStats] = {}
    for name in INSTRUMENTS:
        rets_by_h: dict[int, list[float]] = {h: [] for h in HORIZONS}
        for ev in subset:
            for h in HORIZONS:
                r = ev.forward_returns.get(name, {}).get(h)
                if r is not None and np.isfinite(r):
                    rets_by_h[h].append(r)

        if not any(rets_by_h.values()):
            continue

        mdd = min((min(v) for v in rets_by_h.values() if v), default=0.0)
        stats[name] = InstrumentStats(
            name        = name,
            n_entries   = len(subset),
            median_ret  = {h: float(np.median(v)) if v else np.nan for h, v in rets_by_h.items()},
            p25_ret     = {h: float(np.percentile(v, 25)) if v else np.nan for h, v in rets_by_h.items()},
            p75_ret     = {h: float(np.percentile(v, 75)) if v else np.nan for h, v in rets_by_h.items()},
            hit_rate    = {h: float(np.mean([r > 0 for r in v])) if v else np.nan for h, v in rets_by_h.items()},
            max_drawdown = mdd,
            calmar      = {h: (float(np.median(v)) / abs(mdd) if mdd < 0 and v else np.nan)
                           for h, v in rets_by_h.items()},
        )

    return stats


# ── ML 학습 데이터셋 ──────────────────────────────────────────────────────────

def build_leverage_dataset(days: int = 2520) -> dict:
    """레버리지 ETF ML 모델용 데이터셋 빌드.

    Returns:
        events   — list[EntryEvent]
        features — pd.DataFrame (피처 행렬)
        targets  — dict[instrument][horizon] → pd.Series (forward return)
        stats_by_bucket — 낙폭 구간별 InstrumentStats
    """
    logger.info("레버리지 데이터셋 빌드 시작...")

    tickers = list({info["ticker"] for info in INSTRUMENTS.values()} |
                   {"QQQ", "SPY", "SMH", "^VIX", "HYG", "IEF"})
    close_map = _fetch(tickers, days=days)

    # VIX
    vix = close_map.pop("^VIX", None)
    if vix is None:
        vix = pd.Series(dtype=float)

    # RSI(14) from QQQ
    qqq = close_map.get("QQQ", pd.Series(dtype=float))
    delta = qqq.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # Fear/Greed proxy (기존 파이프라인)
    try:
        from ml.data_pipeline import build_fear_greed_proxy
        fg = build_fear_greed_proxy(days=days)
    except Exception:
        fg = pd.Series(dtype=float)

    events = find_entry_events(close_map, vix=vix, rsi=rsi, fg=fg, min_drawdown=-0.03)

    # 낙폭 구간별 통계
    stats_by_bucket = {}
    for (lo, hi) in DRAWDOWN_BUCKETS:
        label = f"{int(lo*100)}%~{int(hi*100)}%"
        stats_by_bucket[label] = compute_entry_stats(events, drawdown_range=(lo, hi))

    # 피처 행렬
    feat_rows = [e.features for e in events]
    features  = pd.DataFrame(feat_rows, index=[e.date for e in events]).dropna()

    # 타겟: 각 종목의 horizon별 forward return
    targets: dict[str, dict[int, pd.Series]] = {}
    for name in INSTRUMENTS:
        targets[name] = {}
        for h in HORIZONS:
            series = pd.Series(
                {e.date: e.forward_returns.get(name, {}).get(h) for e in events},
                dtype=float,
            ).dropna()
            targets[name][h] = series

    logger.info(
        "데이터셋 완성: 이벤트 %d건 | 피처 %d개",
        len(events), features.shape[1],
    )

    return {
        "events":           events,
        "features":         features,
        "targets":          targets,
        "stats_by_bucket":  stats_by_bucket,
        "instruments":      INSTRUMENTS,
        "horizons":         HORIZONS,
    }


# ── 현재 시황 기반 분석 ───────────────────────────────────────────────────────

def get_current_entry_context(days: int = 2520) -> dict:
    """현재 시황과 과거 유사 조건 분포를 비교해 손익비 반환.

    Returns:
        drawdown      — 현재 QQQ 낙폭
        current_feats — 현재 피처 딕셔너리
        stats         — 현재 낙폭 구간의 InstrumentStats
        similar_events — 과거 유사 조건 이벤트 수
        dataset        — 전체 데이터셋 (ML 모델 학습용)
    """
    ds = build_leverage_dataset(days=days)
    events = ds["events"]

    # 현재 QQQ 낙폭
    tickers   = list({info["ticker"] for info in INSTRUMENTS.values()} |
                     {"QQQ", "^VIX", "HYG", "IEF"})
    close_map = _fetch(tickers, days=120)

    qqq   = close_map.get("QQQ")
    if qqq is None or qqq.empty:
        return ds

    cur_dd    = float(rolling_drawdown(qqq).iloc[-1])
    ma200     = qqq.rolling(200, min_periods=60).mean()
    ma200_gap = float((qqq / ma200 - 1).iloc[-1]) if len(ma200.dropna()) else 0.0

    vix_s = close_map.get("^VIX")
    vix_v = float(vix_s.iloc[-1]) if vix_s is not None and not vix_s.empty else np.nan

    try:
        from barbell_strategy import fetch_rsi
        rsi_v = fetch_rsi("QQQ")
    except Exception:
        rsi_v = np.nan

    try:
        from ml.data_pipeline import get_fg_proxy_score
        fg_v = get_fg_proxy_score()
    except Exception:
        fg_v = 50.0

    current_feats = {
        "drawdown":  cur_dd,
        "vix":       vix_v,
        "rsi":       rsi_v,
        "fg_proxy":  fg_v,
        "ma200_gap": ma200_gap,
        "mom_20d":   float(qqq.pct_change(20).iloc[-1]) if len(qqq) > 20 else 0.0,
    }

    # 현재 낙폭 구간 통계
    bucket = next(
        ((lo, hi) for lo, hi in DRAWDOWN_BUCKETS if lo <= cur_dd < hi),
        (-0.05, 0.0),
    )
    stats = compute_entry_stats(events, drawdown_range=bucket)

    # 유사 조건 이벤트 수 (낙폭 ±5%, VIX ±5 범위)
    similar = [
        e for e in events
        if abs(e.drawdown - cur_dd) < 0.05
        and (np.isnan(vix_v) or abs(e.vix - vix_v) < 5)
    ]

    ds.update({
        "current_drawdown":  cur_dd,
        "current_feats":     current_feats,
        "current_stats":     stats,
        "current_bucket":    bucket,
        "similar_events":    similar,
        "n_similar":         len(similar),
    })
    return ds
