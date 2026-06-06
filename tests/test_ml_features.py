import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.features import add_macro_features, add_news_features, compute_features, ichimoku, momentum, rsi


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


def test_macro_and_news_merge_hooks():
    base = pd.DataFrame({"sma_5": [1.0, 2.0]}, index=pd.date_range("2024-01-01", periods=2))
    macro = pd.DataFrame({"yield_10y": [4.0, 4.1]}, index=base.index)
    news = pd.DataFrame({"news_sentiment": [0.1, -0.2]}, index=base.index)
    merged = add_news_features(add_macro_features(base, macro), news)
    assert list(merged.columns) == ["sma_5", "yield_10y", "news_sentiment"]
