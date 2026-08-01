"""Shared daily momentum overlay for KR/US mock trading.

The helper stays pure: callers provide daily close history, benchmark history,
and a regime label. The scorer then returns bounded overlay metadata that can
tilt ranking and target sizing without replacing the base policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


def _clamp01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _norm_signed(value: float, span: float) -> float:
    if span <= 0:
        return 0.5
    return _clamp01(0.5 + (float(value) / float(span)))


def _norm_ratio(value: float, span: float) -> float:
    if span <= 0:
        return 0.5
    return _clamp01(0.5 + (float(value) / float(span)))


def _clean_series(series) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    s = pd.Series(series).copy()
    if not isinstance(s.index, pd.DatetimeIndex):
        try:
            s.index = pd.to_datetime(s.index)
        except Exception:
            pass
    s = pd.to_numeric(s, errors="coerce").dropna()
    if isinstance(s.index, pd.DatetimeIndex):
        s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def _align(candidate, benchmark, volume=None) -> pd.DataFrame:
    c = _clean_series(candidate).rename("close")
    b = _clean_series(benchmark).rename("benchmark")
    if c.empty or b.empty:
        return pd.DataFrame(columns=["close", "benchmark"])
    df = pd.concat([c, b], axis=1, join="inner").dropna()
    if volume is not None:
        v = _clean_series(volume).rename("volume")
        if not v.empty:
            df = pd.concat([df, v], axis=1, join="inner").dropna()
    return df


def _asof_value(df: pd.DataFrame, asof=None) -> str | None:
    if df.empty:
        return None
    if asof is not None:
        try:
            return pd.Timestamp(asof).isoformat()
        except Exception:
            pass
    idx = df.index[-1]
    try:
        return pd.Timestamp(idx).isoformat()
    except Exception:
        return str(idx)


def build_momentum_features(close, benchmark_close, volume=None, *, asof=None) -> dict[str, Any]:
    """Build a compact, explainable feature set for daily momentum overlay.

    Features are normalized to roughly [0, 1], with 0.5 meaning neutral. Missing
    history is reflected via `history_ok=False`; the scorer will fail closed.
    """
    df = _align(close, benchmark_close, volume)
    out: dict[str, Any] = {
        "asof": _asof_value(df, asof=asof),
        "bars": int(len(_clean_series(close))),
        "benchmark_bars": int(len(_clean_series(benchmark_close))),
        "history_ok": False,
        "benchmark_ok": False,
        "volume_confirmation": None,
        "mom12": None,
        "mom63": None,
        "close_vs_sma50": None,
        "close_vs_sma200": None,
        "relative_strength_60d": None,
        "accel20": None,
    }
    if df.empty:
        return out

    close_s = df["close"].astype(float)
    bench_s = df["benchmark"].astype(float)
    out["benchmark_ok"] = bool(len(bench_s) >= 2)
    out["history_ok"] = bool(len(close_s) >= 253 and len(bench_s) >= 253)

    last = float(close_s.iloc[-1])

    if len(close_s) >= 253 and float(close_s.iloc[-253]) > 0:
        mom12 = float(close_s.iloc[-22] / close_s.iloc[-253] - 1.0)
        out["mom12"] = round(_norm_signed(mom12, 0.60), 4)

    if len(close_s) >= 64 and float(close_s.iloc[-64]) > 0:
        mom63 = float(close_s.iloc[-1] / close_s.iloc[-64] - 1.0)
        out["mom63"] = round(_norm_signed(mom63, 0.35), 4)

    if len(close_s) >= 50:
        sma50 = float(close_s.rolling(50, min_periods=50).mean().iloc[-1])
        if sma50 > 0:
            out["close_vs_sma50"] = round(_norm_ratio(last / sma50 - 1.0, 0.18), 4)

    if len(close_s) >= 200:
        sma200 = float(close_s.rolling(200, min_periods=200).mean().iloc[-1])
        if sma200 > 0:
            out["close_vs_sma200"] = round(_norm_ratio(last / sma200 - 1.0, 0.30), 4)

    if len(close_s) >= 61 and len(bench_s) >= 61:
        cand_60 = float(close_s.iloc[-1] / close_s.iloc[-61] - 1.0)
        bench_60 = float(bench_s.iloc[-1] / bench_s.iloc[-61] - 1.0)
        out["relative_strength_60d"] = round(_norm_signed(cand_60 - bench_60, 0.30), 4)

    if len(close_s) >= 41:
        recent_20 = float(close_s.iloc[-1] / close_s.iloc[-21] - 1.0)
        prior_20 = float(close_s.iloc[-21] / close_s.iloc[-41] - 1.0)
        out["accel20"] = round(_norm_signed(recent_20 - prior_20, 0.20), 4)

    if "volume" in df.columns:
        vol = df["volume"].astype(float).dropna()
        if len(vol) >= 20:
            recent = float(vol.tail(5).mean())
            base = float(vol.tail(20).mean())
            if base > 0:
                out["volume_confirmation"] = round(_clamp01(0.5 + ((recent / base) - 1.0) / 0.4), 4)

    return out


_MARKET_CFG = {
    "kr": {
        "strong_threshold": 0.68,
        "weak_threshold": 0.48,
        "broken_threshold": 0.42,
        "overlay_weight": {"risk_on": 0.45, "neutral": 0.25},
        "mult_cap": 1.20,
    },
    "us": {
        "strong_threshold": 0.66,
        "weak_threshold": 0.46,
        "broken_threshold": 0.40,
        "overlay_weight": {"risk_on": 0.50, "neutral": 0.28},
        "mult_cap": 1.25,
    },
}


def _normalize_market(market: str | None) -> str:
    m = str(market or "us").strip().lower()
    return "kr" if m.startswith("kr") else "us"


def _normalize_regime(regime: str | None) -> str | None:
    r = str(regime or "").strip().lower()
    if r in {"risk_on", "risk-on", "bull", "trend", "uptrend"}:
        return "risk_on"
    if r in {"neutral", "mixed", "observe"}:
        return "neutral"
    if r in {"risk_off", "risk-off", "bear", "defensive"}:
        return "risk_off"
    return None


def _feature_score(features: dict[str, Any], keys: tuple[str, ...]) -> tuple[float | None, list[float]]:
    vals = [float(features[k]) for k in keys if features.get(k) is not None]
    if not vals:
        return None, []
    return float(sum(vals) / len(vals)), vals


def _reason_codes_for_features(features: dict[str, Any], state: str, regime: str, active: bool) -> list[str]:
    codes: list[str] = []
    if not active:
        return codes
    codes.append(f"regime:{regime}")
    if features.get("close_vs_sma50") is not None:
        codes.append("trend:sma50_up" if float(features["close_vs_sma50"]) >= 0.5 else "trend:sma50_down")
    if features.get("close_vs_sma200") is not None:
        codes.append("trend:sma200_up" if float(features["close_vs_sma200"]) >= 0.5 else "trend:sma200_down")
    if features.get("mom12") is not None:
        codes.append("momentum:mom12_up" if float(features["mom12"]) >= 0.5 else "momentum:mom12_down")
    if features.get("mom63") is not None:
        codes.append("momentum:mom63_up" if float(features["mom63"]) >= 0.5 else "momentum:mom63_down")
    if features.get("relative_strength_60d") is not None:
        codes.append("relative_strength:lead" if float(features["relative_strength_60d"]) >= 0.5 else "relative_strength:lag")
    if features.get("accel20") is not None:
        codes.append("accel:up" if float(features["accel20"]) >= 0.5 else "accel:down")
    if features.get("volume_confirmation") is None:
        codes.append("volume:missing")
    else:
        codes.append("volume:confirm" if float(features["volume_confirmation"]) >= 0.5 else "volume:weak")
    codes.append(f"state:{state}")
    return codes


def score_momentum_overlay(base_score: float, features: dict[str, Any], *, market: str,
                           regime: str | None, freshness_ok: bool) -> dict[str, Any]:
    """Blend base score with a bounded momentum overlay.

    The overlay is active only when the daily data is fresh, enough history is
    available, and the market regime is supportive. Otherwise the function
    falls back to the base score and marks the overlay inactive.
    """
    cfg = _MARKET_CFG[_normalize_market(market)]
    base = _clamp01(float(base_score or 0.0))
    regime_norm = _normalize_regime(regime)
    history_ok = bool(features.get("history_ok"))
    benchmark_ok = bool(features.get("benchmark_ok"))
    gate_codes: list[str] = []

    if not freshness_ok:
        gate_codes.append("gate:freshness")
    if not history_ok:
        gate_codes.append("gate:history")
    if not benchmark_ok:
        gate_codes.append("gate:benchmark")
    if regime_norm not in {"risk_on", "neutral"}:
        gate_codes.append(f"gate:regime:{regime_norm or 'missing'}")

    if gate_codes:
        return {
            "base_score": base,
            "momentum_score": 0.5,
            "selection_score": base,
            "momentum_tilt": 0.0,
            "momentum_multiplier": 1.0,
            "overlay_active": False,
            "momentum_state": "inactive",
            "reason_codes": gate_codes,
            "momentum_reason_codes": gate_codes,
            "market": _normalize_market(market),
            "regime": regime_norm,
            "overlay_weight": 0.0,
        }

    score_keys = (
        "mom12", "mom63", "close_vs_sma50", "close_vs_sma200",
        "relative_strength_60d", "accel20", "volume_confirmation",
    )
    momentum_score, used_vals = _feature_score(features, score_keys)
    if momentum_score is None:
        gate_codes.append("gate:features")
        return {
            "base_score": base,
            "momentum_score": 0.5,
            "selection_score": base,
            "momentum_tilt": 0.0,
            "momentum_multiplier": 1.0,
            "overlay_active": False,
            "momentum_state": "inactive",
            "reason_codes": gate_codes,
            "momentum_reason_codes": gate_codes,
            "market": _normalize_market(market),
            "regime": regime_norm,
            "overlay_weight": 0.0,
        }

    strong_th = float(cfg["strong_threshold"])
    weak_th = float(cfg["weak_threshold"])
    broken_th = float(cfg["broken_threshold"])
    above_sma200 = float(features.get("close_vs_sma200") or 0.5)
    relative_strength = float(features.get("relative_strength_60d") or 0.5)

    if momentum_score <= broken_th or above_sma200 < 0.35 or relative_strength < 0.35:
        state = "broken"
    elif momentum_score >= strong_th and above_sma200 >= 0.50 and relative_strength >= 0.50:
        state = "strong"
    else:
        state = "weak"

    regime_weight = float(cfg["overlay_weight"].get(regime_norm, 0.0))
    if state == "weak":
        regime_weight *= 0.70
    elif state == "broken":
        regime_weight *= 1.00

    if state == "strong":
        momentum_multiplier = 1.0 + (float(cfg["mult_cap"]) - 1.0) * max(0.0, (momentum_score - strong_th) / max(1e-9, 1.0 - strong_th))
    elif state == "weak":
        if momentum_score <= broken_th:
            momentum_multiplier = 0.25
        else:
            momentum_multiplier = 0.45 + 0.50 * max(0.0, (momentum_score - weak_th) / max(1e-9, strong_th - weak_th))
    else:  # broken
        momentum_multiplier = max(0.0, 0.25 * momentum_score)

    momentum_multiplier = float(min(float(cfg["mult_cap"]), max(0.0, momentum_multiplier)))
    selection_score = _clamp01(base * (1.0 - regime_weight) + momentum_score * regime_weight)
    momentum_tilt = round(selection_score - base, 6)
    reasons = _reason_codes_for_features(features, state, regime_norm, True)
    if state == "broken":
        reasons.append("trend:broken")
    elif state == "weak":
        reasons.append("trend:soft")
    else:
        reasons.append("trend:strong")

    return {
        "base_score": base,
        "momentum_score": round(momentum_score, 6),
        "selection_score": round(selection_score, 6),
        "momentum_tilt": momentum_tilt,
        "momentum_multiplier": round(momentum_multiplier, 6),
        "overlay_active": True,
        "momentum_state": state,
        "reason_codes": reasons,
        "momentum_reason_codes": reasons,
        "market": _normalize_market(market),
        "regime": regime_norm,
        "overlay_weight": round(regime_weight, 6),
    }


def inactive_momentum_overlay(base_score: float, *, market: str, regime: str | None = None,
                              reason_codes: list[str] | None = None) -> dict[str, Any]:
    """Canonical inactive overlay payload.

    Used by the cron paths when the feature flag is off or when the overlay is
    intentionally suppressed before any expensive feature fetches.
    """
    base = _clamp01(float(base_score or 0.0))
    codes = list(reason_codes or [])
    return {
        "base_score": base,
        "momentum_score": 0.5,
        "selection_score": base,
        "momentum_tilt": 0.0,
        "momentum_multiplier": 1.0,
        "overlay_active": False,
        "momentum_state": "inactive",
        "reason_codes": codes,
        "momentum_reason_codes": codes,
        "market": _normalize_market(market),
        "regime": _normalize_regime(regime),
        "overlay_weight": 0.0,
    }
