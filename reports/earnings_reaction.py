#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reports/earnings_reaction.py — 과거 실적발표 후 주가반응(PEAD) 분석 (Phase 1 / §G2).

각 과거 실적일에 대해: 실적후 1일 반응(발표 다음 거래일 변동) + 5/20일 드리프트(PEAD) +
(가능 시) 실제변동/옵션 IV 기대변동 비율. 종목별로 집계 → 서프라이즈→반응 관계·드리프트 지속성.

전부 **결정적 분석**(예측 아님). 가격은 yfinance(또는 주입), 서프라이즈는 earnings_data.
관례: 실적은 대개 장마감 후 발표 → 발표일 직후 거래일을 '반응일'로 본다(after-close PEAD 표준).
"""
from __future__ import annotations

import logging
import statistics

logger = logging.getLogger(__name__)


def _closes(ticker: str, *, days: int = 800):
    """일별 종가 Series — lib.price_utils.fetch_closes 위임(행위 동일; tz 정규화는 post_earnings_reactions 가 멱등 처리)."""
    from lib.price_utils import fetch_closes
    return fetch_closes(ticker, period=f"{min(days, 1500)}d")


def post_earnings_reactions(ticker: str, *, prices=None, hist=None) -> list[dict]:
    """과거 실적일별 [{date, surprise_pct, reaction_1d, drift_5d, drift_20d}] (오래된→최신).

    prices(종가 Series)·hist(earnings_data.earnings_history 결과) 주입 시 무네트워크(테스트).
    """
    try:
        import pandas as pd
        from providers import earnings_data as ed
        closes = prices if prices is not None else _closes(ticker)
        if closes is None or len(closes) < 25:
            return []
        if getattr(closes.index, "tz", None) is not None:
            closes = closes.copy()
            closes.index = closes.index.tz_localize(None)
        h = hist if hist is not None else ed.earnings_history(ticker, limit=12)
        idx = closes.index
        out = []
        for e in h:
            try:
                d = pd.Timestamp(e["date"])
            except Exception:
                continue
            # day0 = 발표일 이하 마지막 거래일, 반응일 = day0+1
            pos = idx.searchsorted(d, side="right") - 1
            if pos < 0 or pos + 1 >= len(idx):
                continue
            c0 = float(closes.iloc[pos])
            c1 = float(closes.iloc[pos + 1])
            if c0 <= 0:
                continue
            rec = {"date": e["date"], "surprise_pct": e.get("surprise_pct"),
                   "reaction_1d": round(c1 / c0 - 1.0, 4), "drift_5d": None, "drift_20d": None}
            if pos + 1 + 5 < len(idx):
                rec["drift_5d"] = round(float(closes.iloc[pos + 1 + 5]) / c1 - 1.0, 4)
            if pos + 1 + 20 < len(idx):
                rec["drift_20d"] = round(float(closes.iloc[pos + 1 + 20]) / c1 - 1.0, 4)
            out.append(rec)
        out.sort(key=lambda r: r["date"])
        return out
    except Exception as e:
        logger.debug("반응 분석 실패 %s: %s", ticker, e)
        return []


def reaction_summary(reactions: list[dict]) -> dict:
    """반응 리스트 집계 — 평균/중앙 절대변동·beat→상승 적중률·드리프트 지속성."""
    out = {"n": 0, "avg_abs_move_1d": None, "median_abs_move_1d": None,
           "beat_up_rate": None, "miss_down_rate": None,
           "avg_drift_5d_on_beat": None, "drift_persistence": None}
    rs = [r for r in reactions if r.get("reaction_1d") is not None]
    if not rs:
        return out
    out["n"] = len(rs)
    moves = [abs(r["reaction_1d"]) for r in rs]
    out["avg_abs_move_1d"] = round(sum(moves) / len(moves), 4)
    out["median_abs_move_1d"] = round(statistics.median(moves), 4)

    surp = [r for r in rs if r.get("surprise_pct") is not None]
    beats = [r for r in surp if r["surprise_pct"] > 0]
    misses = [r for r in surp if r["surprise_pct"] < 0]
    if beats:
        out["beat_up_rate"] = round(sum(1 for r in beats if r["reaction_1d"] > 0) / len(beats), 3)
        d5 = [r["drift_5d"] for r in beats if r.get("drift_5d") is not None]
        if d5:
            out["avg_drift_5d_on_beat"] = round(sum(d5) / len(d5), 4)
    if misses:
        out["miss_down_rate"] = round(sum(1 for r in misses if r["reaction_1d"] < 0) / len(misses), 3)
    # 드리프트 지속성 = sign(surprise)와 sign(drift_5d) 일치 비율 (PEAD)
    pairs = [(r["surprise_pct"], r["drift_5d"]) for r in surp if r.get("drift_5d") is not None
             and r["surprise_pct"] != 0]
    if pairs:
        same = sum(1 for s, d in pairs if (s > 0) == (d > 0))
        out["drift_persistence"] = round(same / len(pairs), 3)
    return out


def analyze(ticker: str, *, prices=None, hist=None) -> dict:
    """종목 PEAD 분석 = 반응 리스트 + 집계 요약."""
    reactions = post_earnings_reactions(ticker, prices=prices, hist=hist)
    return {"ticker": ticker, "reactions": reactions, "summary": reaction_summary(reactions)}
