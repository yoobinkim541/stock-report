"""Deterministic chart analysis helpers for the workspace rail."""
from __future__ import annotations

from typing import Callable

import pandas as pd

from ohlc_utils import normalize_ohlc_frame


def _close(hist) -> pd.Series | None:
    df = normalize_ohlc_frame(hist)
    if df is None or getattr(df, "empty", True) or "Close" not in getattr(df, "columns", []):
        return None
    s = df["Close"].astype(float).dropna()
    return s if len(s) else None


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def seasonality_summary(hist) -> dict:
    close = _close(hist)
    if close is None or len(close) < 60:
        return {"ok": False, "reason": "not_enough_history", "months": []}
    monthly = close.resample("ME").last().pct_change().dropna()
    rows = []
    for month in range(1, 13):
        vals = monthly[monthly.index.month == month]
        if len(vals):
            rows.append(
                {
                    "month": month,
                    "avg_return": round(float(vals.mean()), 6),
                    "win_rate": round(float((vals > 0).mean()), 6),
                    "sample": int(len(vals)),
                }
            )
        else:
            rows.append({"month": month, "avg_return": None, "win_rate": None, "sample": 0})
    return {"ok": True, "months": rows}


def relative_strength_summary(hist, benchmark) -> dict:
    close = _close(hist)
    bench = _close(benchmark)
    if close is None or bench is None:
        return {"ok": False, "reason": "missing_close"}
    df = pd.concat([close.rename("asset"), bench.rename("benchmark")], axis=1).dropna()
    if len(df) < 80:
        return {"ok": False, "reason": "not_enough_history"}
    rel = (df["asset"] / df["asset"].iloc[0]) / (df["benchmark"] / df["benchmark"].iloc[0])
    rs_60 = float(rel.pct_change(60).iloc[-1]) if len(rel) > 60 else 0.0
    mom_20 = float(rel.pct_change(20).iloc[-1] - rel.pct_change(60).iloc[-1]) if len(rel) > 60 else 0.0
    if rs_60 >= 0 and mom_20 >= 0:
        quadrant = "leading"
    elif rs_60 >= 0 and mom_20 < 0:
        quadrant = "weakening"
    elif rs_60 < 0 and mom_20 < 0:
        quadrant = "lagging"
    else:
        quadrant = "improving"
    return {
        "ok": True,
        "relative_strength_60d": round(rs_60, 6),
        "relative_momentum_20d": round(mom_20, 6),
        "quadrant": quadrant,
    }


def pattern_candidates(hist) -> list[dict]:
    df = normalize_ohlc_frame(hist)
    if df is None or getattr(df, "empty", True) or not {"High", "Low", "Close"} <= set(df.columns):
        return []
    if len(df) < 60:
        return []
    close = df["Close"].astype(float).dropna()
    if len(close) < 60:
        return []
    out: list[dict] = []
    recent = df.tail(60)
    last = float(close.iloc[-1])
    previous = recent.iloc[:-1]
    hi20 = float(previous["High"].tail(20).max())
    lo20 = float(previous["Low"].tail(20).min())
    hi60 = float(previous["High"].max())
    lo60 = float(previous["Low"].min())

    if last > hi20 and hi20 >= hi60 * 0.98:
        out.append(
            {
                "kind": "channel_breakout",
                "direction": "up",
                "confidence": 0.62,
                "window": 60,
                "invalidation_price": round(hi20, 4),
                "evidence": ["close_above_recent_range", "near_60bar_high"],
            }
        )
    if last < lo20 and lo20 <= lo60 * 1.02:
        out.append(
            {
                "kind": "channel_breakout",
                "direction": "down",
                "confidence": 0.62,
                "window": 60,
                "invalidation_price": round(lo20, 4),
                "evidence": ["close_below_recent_range", "near_60bar_low"],
            }
        )

    ma20 = close.rolling(20).mean()
    sd20 = close.rolling(20).std()
    bandwidth = ((ma20 + 2 * sd20) - (ma20 - 2 * sd20)) / ma20.replace(0, float("nan"))
    if len(bandwidth.dropna()) >= 40:
        now_bw = float(bandwidth.iloc[-1])
        prev_q = float(bandwidth.dropna().tail(60).quantile(0.25))
        if now_bw > prev_q * 1.35 and float(bandwidth.iloc[-5:].min()) <= prev_q:
            out.append(
                {
                    "kind": "bollinger_squeeze_expansion",
                    "direction": "up" if close.iloc[-1] >= close.iloc[-5] else "down",
                    "confidence": 0.58,
                    "window": 60,
                    "invalidation_price": round(float(close.iloc[-5]), 4),
                    "evidence": ["bandwidth_reexpansion_after_compression"],
                }
            )
    return out[:5]


def multi_timeframe_summary(load_ohlc: Callable[[str, str], object], ticker: str) -> dict:
    rows = []
    for tf in ("5m", "1h", "1d", "1wk"):
        try:
            hist = load_ohlc(ticker, tf)
        except Exception as exc:
            rows.append({"timeframe": tf, "ok": False, "reason": str(exc)})
            continue
        close = _close(hist)
        if close is None or len(close) < 30:
            rows.append({"timeframe": tf, "ok": False, "reason": "not_enough_history"})
            continue
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean() if len(close) >= 60 else ma20
        last = float(close.iloc[-1])
        trend = "up" if last >= float(ma20.iloc[-1]) >= float(ma60.iloc[-1]) else "down" if last < float(ma20.iloc[-1]) else "mixed"
        rsi = _rsi(close).dropna()
        rsi_zone = "neutral"
        if len(rsi):
            rv = float(rsi.iloc[-1])
            rsi_zone = "overbought" if rv >= 70 else "oversold" if rv <= 30 else "neutral"
        rows.append({"timeframe": tf, "ok": True, "trend": trend, "rsi_zone": rsi_zone, "last": round(last, 4)})
    return {"ok": any(row.get("ok") for row in rows), "ticker": ticker, "rows": rows}
