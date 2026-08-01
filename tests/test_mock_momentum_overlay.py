from __future__ import annotations

import numpy as np
import pandas as pd

from ml.mock_momentum_overlay import build_momentum_features, score_momentum_overlay


def _trend_series(start: float, stop: float, n: int = 260) -> pd.Series:
    idx = pd.bdate_range("2025-01-02", periods=n)
    return pd.Series(np.linspace(start, stop, n), index=idx)


def _chop_series(n: int = 260) -> pd.Series:
    idx = pd.bdate_range("2025-01-02", periods=n)
    x = np.arange(n, dtype=float)
    return pd.Series(100.0 + np.sin(x / 2.5) * 2.0 + np.where(x % 2 == 0, 0.8, -0.8), index=idx)


def test_build_momentum_features_extracts_compact_trend_signals():
    close = _trend_series(100, 170)
    bench = _trend_series(100, 100)
    vol = pd.Series(np.linspace(1_000_000, 1_500_000, len(close)), index=close.index)

    feats = build_momentum_features(close, bench, vol)

    assert {"mom12", "mom63", "close_vs_sma50", "close_vs_sma200",
            "relative_strength_60d", "accel20", "volume_confirmation"} <= set(feats)
    assert 0.0 <= feats["mom12"] <= 1.0
    assert 0.0 <= feats["close_vs_sma200"] <= 1.0
    assert 0.0 <= feats["relative_strength_60d"] <= 1.0
    assert feats["volume_confirmation"] is not None


def test_score_momentum_overlay_rises_in_risk_on_trend():
    close = _trend_series(100, 170)
    bench = _trend_series(100, 100)
    vol = pd.Series(np.linspace(1_000_000, 1_500_000, len(close)), index=close.index)
    feats = build_momentum_features(close, bench, vol)

    out = score_momentum_overlay(0.58, feats, market="kr", regime="risk_on", freshness_ok=True)

    assert out["overlay_active"] is True
    assert out["momentum_state"] == "strong"
    assert out["selection_score"] > 0.58
    assert out["momentum_multiplier"] > 1.0
    assert 0.0 <= out["momentum_score"] <= 1.0
    assert 0.0 <= out["selection_score"] <= 1.0
    assert 0.0 <= out["momentum_multiplier"] <= 1.25
    assert "regime:risk_on" in out["reason_codes"]


def test_score_momentum_overlay_falls_back_when_stale_or_unavailable():
    close = _chop_series()
    bench = _trend_series(100, 100)
    feats = build_momentum_features(close, bench)

    out = score_momentum_overlay(0.62, feats, market="us", regime="risk_off", freshness_ok=False)

    assert out["overlay_active"] is False
    assert out["momentum_state"] == "inactive"
    assert out["selection_score"] == 0.62
    assert out["momentum_multiplier"] == 1.0
    assert any(code.startswith("gate:") or code.startswith("regime:") for code in out["reason_codes"])


def test_score_momentum_overlay_can_trim_broken_trend():
    close = _trend_series(180, 100)
    bench = _trend_series(100, 100)
    vol = pd.Series(np.linspace(1_500_000, 900_000, len(close)), index=close.index)
    feats = build_momentum_features(close, bench, vol)

    out = score_momentum_overlay(0.79, feats, market="us", regime="risk_on", freshness_ok=True)

    assert out["overlay_active"] is True
    assert out["momentum_state"] == "broken"
    assert out["selection_score"] < 0.79
    assert 0.0 <= out["momentum_multiplier"] <= 0.25
    assert "trend:broken" in out["reason_codes"]
