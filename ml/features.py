"""p5 — Feature dataset builder.

Computes technical features over OHLCV / close data without lookahead leakage:
  all features at row t use only data up to and including row t.

Public API
----------
compute_features(df)         — full feature frame from OHLCV DataFrame
add_macro_features(df, ...)  — merge prebuilt macro/news frames into feature frame
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_close(df: pd.DataFrame) -> pd.Series:
    if "close" not in df.columns:
        raise ValueError("DataFrame must have a 'close' column.")
    return df["close"].astype(float)


def _ewm_std(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).std()


# ---------------------------------------------------------------------------
# Individual feature functions (all accept a Series or DataFrame, return Series)
# ---------------------------------------------------------------------------

def sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window, min_periods=window).mean().rename(f"sma_{window}")


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean().rename(f"ema_{span}")


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI — no lookahead; uses only past closes."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    out = out.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return out.rename(f"rsi_{period}")


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram."""
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": hist,
    })


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: mid, upper, lower, %B, bandwidth."""
    mid = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    return pd.DataFrame({
        f"bb_mid_{window}": mid,
        f"bb_upper_{window}": upper,
        f"bb_lower_{window}": lower,
        f"bb_pct_b_{window}": pct_b,
        f"bb_bw_{window}": bandwidth,
    })


def momentum(close: pd.Series, period: int) -> pd.Series:
    """Percentage return over *period* trading days (no lookahead)."""
    return close.pct_change(period).rename(f"mom_{period}d")


def volatility(close: pd.Series, window: int = 21) -> pd.Series:
    """Annualised realised volatility of log returns."""
    log_ret = np.log(close / close.shift(1))
    return (
        log_ret.rolling(window, min_periods=window).std() * np.sqrt(252)
    ).rename(f"vol_{window}d")


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (requires high/low/close columns)."""
    required = {"high", "low", "close"}
    if not required.issubset(df.columns):
        return pd.Series(dtype=float, name=f"atr_{period}")
    hi = df["high"].astype(float)
    lo = df["low"].astype(float)
    cl = df["close"].astype(float)
    prev_cl = cl.shift(1)
    tr = pd.concat([hi - lo, (hi - prev_cl).abs(), (lo - prev_cl).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().rename(f"atr_{period}")


def ichimoku(close: pd.Series, df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Ichimoku Kinkō Hyō — deterministic, no lookahead.

    Tenkan/Kijun use only past highs/lows. Senkou spans are shifted forward by
    convention, so the value visible at row t was computed from data at or
    before t-26. Chikou is intentionally omitted because the chart convention
    requires close.shift(-26), which would place future close values on row t.
    """
    if df is not None and {"high", "low"}.issubset(df.columns):
        hi = df["high"].astype(float)
        lo = df["low"].astype(float)
    else:
        hi = close
        lo = close

    tenkan = (hi.rolling(9).max() + lo.rolling(9).min()) / 2
    kijun = (hi.rolling(26).max() + lo.rolling(26).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((hi.rolling(52).max() + lo.rolling(52).min()) / 2).shift(26)

    return pd.DataFrame({
        "ichi_tenkan": tenkan,
        "ichi_kijun": kijun,
        "ichi_senkou_a": senkou_a,
        "ichi_senkou_b": senkou_b,
    })


# ---------------------------------------------------------------------------
# Main feature builder
# ---------------------------------------------------------------------------

def compute_features(
    df: pd.DataFrame,
    *,
    include_ichimoku: bool = True,
    include_atr: bool = True,
) -> pd.DataFrame:
    """Compute the full technical feature set from an OHLCV DataFrame.

    Args:
        df: DataFrame with at least a 'close' column. 'open', 'high', 'low',
            'volume' are used when available.
        include_ichimoku: Compute Ichimoku features (adds 5 columns).
        include_atr: Compute ATR (requires high/low/close).

    Returns:
        Wide DataFrame of features aligned to df's index.
        Features at row t use only data up to t — no lookahead.
    """
    close = _require_close(df)
    parts: list[pd.DataFrame | pd.Series] = []

    # Moving averages
    for w in (5, 10, 20, 50, 200):
        parts.append(sma(close, w))
    for s in (12, 26, 50):
        parts.append(ema(close, s))

    # RSI
    parts.append(rsi(close, 14))

    # MACD
    parts.append(macd(close))

    # Bollinger Bands
    parts.append(bollinger(close, 20))

    # Momentum
    for p in (1, 5, 10, 21, 63, 126):
        parts.append(momentum(close, p))

    # Volatility
    for w in (10, 21, 63):
        parts.append(volatility(close, w))

    # ATR (needs high/low)
    if include_atr:
        parts.append(atr(df, 14))

    # Ichimoku
    if include_ichimoku:
        parts.append(ichimoku(close, df))

    # Volume features (if available)
    if "volume" in df.columns:
        vol = df["volume"].astype(float)
        parts.append(vol.rolling(20).mean().rename("vol_sma_20"))
        parts.append((vol / vol.rolling(20).mean().replace(0, np.nan)).rename("vol_ratio_20"))

    # Close normalised vs 52-week high/low
    parts.append((close / close.rolling(252).max().replace(0, np.nan)).rename("close_vs_52w_high"))
    parts.append((close / close.rolling(252).min().replace(0, np.nan)).rename("close_vs_52w_low"))

    out = pd.concat(parts, axis=1)
    out.index = df.index
    return out


# ---------------------------------------------------------------------------
# Macro / news feature merge hooks
# ---------------------------------------------------------------------------

def add_macro_features(
    feature_df: pd.DataFrame,
    macro_df: pd.DataFrame,
    how: str = "left",
) -> pd.DataFrame:
    """Merge a prebuilt macro feature frame into the feature DataFrame.

    Args:
        feature_df: Output of compute_features() (date-indexed).
        macro_df: Date-indexed DataFrame with macro columns (e.g. yield curve, VIX).
        how: Join type ('left', 'inner', 'outer').

    Returns:
        Merged DataFrame. NaN values are left for the caller to fill/drop.
    """
    if macro_df.empty:
        return feature_df
    return feature_df.join(macro_df, how=how)


def add_news_features(
    feature_df: pd.DataFrame,
    news_df: pd.DataFrame,
    how: str = "left",
) -> pd.DataFrame:
    """Merge prebuilt news/sentiment feature frame into the feature DataFrame.

    Args:
        feature_df: Output of compute_features() (date-indexed).
        news_df: Date-indexed DataFrame with news feature columns
                 (e.g. count, sentiment, theme_tech, event_earnings).
        how: Join type ('left', 'inner', 'outer').

    Returns:
        Merged DataFrame. NaN values are left for the caller to fill/drop.
    """
    if news_df.empty:
        return feature_df
    return feature_df.join(news_df, how=how)
