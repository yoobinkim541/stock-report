import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.features import (add_macro_features, add_news_features, compute_features, find_pivots,
                         ichimoku, momentum, rsi, rsi_divergence, rsi_divergence_events)


def _ohlcv(n=90):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = pd.Series(np.linspace(100, 130, n), index=idx)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.arange(n) + 1000,
        },
        index=idx,
    )


def test_compute_features_has_expected_columns():
    features = compute_features(_ohlcv())
    for col in ["sma_5", "ema_12", "rsi_14", "macd", "bb_mid_20", "mom_21d", "vol_21d", "atr_14"]:
        assert col in features.columns


def test_momentum_uses_past_values_only():
    close = pd.Series([100.0, 110.0, 121.0], index=pd.date_range("2024-01-01", periods=3))
    out = momentum(close, 1)
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == pytest.approx(0.10)
    assert out.iloc[2] == pytest.approx(0.10)


def test_rsi_rising_series_reaches_high_value():
    close = pd.Series(np.arange(1, 40, dtype=float), index=pd.date_range("2024-01-01", periods=39))
    out = rsi(close, 14)
    assert out.dropna().iloc[-1] > 90


def test_ichimoku_omits_lookahead_chikou_column():
    df = _ohlcv(90)
    out = ichimoku(df["close"], df)
    assert "ichi_chikou" not in out.columns
    assert {"ichi_tenkan", "ichi_kijun", "ichi_senkou_a", "ichi_senkou_b"}.issubset(out.columns)


def test_compute_features_includes_rsi_divergence_column():
    features = compute_features(_ohlcv())
    assert "rsi_divergence" in features.columns


def _flat_series(n=41, base=50.0):
    return pd.Series(base, index=pd.date_range("2024-01-01", periods=n))


def test_rsi_divergence_events_detects_bearish():
    """가격은 더 높은 고점을 찍는데 RSI 는 더 낮은 고점 — 약세 다이버전스."""
    close = _flat_series()
    close.iloc[10] = 110.0                 # 1차 고점
    close.iloc[30] = 120.0                 # 2차(더 높은) 고점
    rsi_s = _flat_series(base=50.0)
    rsi_s.iloc[10] = 70.0
    rsi_s.iloc[30] = 60.0                  # 가격은 올랐는데 RSI 는 하락 → 약세

    events = rsi_divergence_events(close, rsi_s, pivot_window=5)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "bearish"
    assert ev["date"] == close.index[30]
    assert ev["price"] == 120.0


def test_rsi_divergence_events_detects_bullish():
    """가격은 더 낮은 저점을 찍는데 RSI 는 더 높은 저점 — 강세 다이버전스."""
    close = _flat_series()
    close.iloc[10] = -10.0                 # 1차 저점
    close.iloc[30] = -20.0                 # 2차(더 낮은) 저점
    rsi_s = _flat_series(base=50.0)
    rsi_s.iloc[10] = 30.0
    rsi_s.iloc[30] = 40.0                  # 가격은 더 빠졌는데 RSI 는 상승 → 강세

    events = rsi_divergence_events(close, rsi_s, pivot_window=5)
    assert len(events) == 1
    assert events[0]["type"] == "bullish"
    assert events[0]["date"] == close.index[30]


def test_rsi_divergence_events_ignores_confirming_moves():
    """가격·RSI 가 같은 방향(다이버전스 아님)이면 이벤트 없음."""
    close = _flat_series()
    close.iloc[10] = 110.0
    close.iloc[30] = 120.0
    rsi_s = _flat_series(base=50.0)
    rsi_s.iloc[10] = 60.0
    rsi_s.iloc[30] = 70.0                  # 가격·RSI 둘 다 상승 — 다이버전스 아님

    assert rsi_divergence_events(close, rsi_s, pivot_window=5) == []


def test_rsi_divergence_no_lookahead_shift():
    """다이버전스 피처는 피봇 확정 시점(pivot_window 만큼 지연) 이전엔 0 이어야 한다."""
    close = _flat_series()
    close.iloc[10] = 110.0
    close.iloc[30] = 120.0
    rsi_s = _flat_series(base=50.0)
    rsi_s.iloc[10] = 70.0
    rsi_s.iloc[30] = 60.0

    out = rsi_divergence(close, rsi_s, pivot_window=5, persist_bars=5)
    confirm_pos = 30 + 5                    # 피봇(30) + pivot_window
    assert (out.iloc[:confirm_pos] == 0.0).all()          # 확정 전엔 전부 0
    assert (out.iloc[confirm_pos:confirm_pos + 5] == -1.0).all()  # 확정 후 persist_bars 유지
    assert out.iloc[confirm_pos + 5] == 0.0                # persist 이후 소멸


def test_find_pivots_flags_local_extremes():
    close = _flat_series()
    close.iloc[10] = 110.0
    close.iloc[20] = -10.0
    is_high, is_low = find_pivots(close, window=5)
    assert bool(is_high.iloc[10]) is True
    assert bool(is_low.iloc[20]) is True
    assert bool(is_high.iloc[20]) is False


def test_macro_and_news_merge_hooks():
    base = pd.DataFrame({"sma_5": [1.0, 2.0]}, index=pd.date_range("2024-01-01", periods=2))
    macro = pd.DataFrame({"yield_10y": [4.0, 4.1]}, index=base.index)
    news = pd.DataFrame({"news_sentiment": [0.1, -0.2]}, index=base.index)
    merged = add_news_features(add_macro_features(base, macro), news)
    assert list(merged.columns) == ["sma_5", "yield_10y", "news_sentiment"]
