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


def find_pivots(series: pd.Series, window: int = 5) -> tuple[pd.Series, pd.Series]:
    """좌우 window 개 봉보다 엄격히 높은/낮은 지점 = 피봇 고점/저점 불리언 마스크.

    `rolling(...).max() == series`(동률 포함) 로 정의하면 평탄 구간의 모든 점이
    서로 동률 피봇으로 잡혀 진짜 두 고점 사이에 잡음 피봇이 끼어들며 페어링이
    엉뚱한 이웃끼리 맺어진다 — 엄격 부등호(자기 자신 제외 좌/우 윈도우 각각)로
    방지. 좌우 window 개 봉을 모두 봐야 피봇이 확정되므로(중앙 정렬), 마지막
    window 개 봉은 항상 미확정 상태로 남는다 — 호출부가 필요에 따라 지연 처리.
    """
    left_max = series.shift(1).rolling(window, min_periods=window).max()
    right_max = series.shift(-window).rolling(window, min_periods=window).max()
    left_min = series.shift(1).rolling(window, min_periods=window).min()
    right_min = series.shift(-window).rolling(window, min_periods=window).min()
    is_high = series.notna() & left_max.notna() & right_max.notna() & (series > left_max) & (series > right_max)
    is_low = series.notna() & left_min.notna() & right_min.notna() & (series < left_min) & (series < right_min)
    return is_high, is_low


def rsi_divergence_events(close: pd.Series, rsi_series: pd.Series, *,
                           pivot_window: int = 5, max_gap: int = 60) -> list[dict]:
    """가격 피봇과 RSI 값을 비교해 강세/약세 다이버전스 지점을 찾는다 (표시·분석용).

    - 약세(bearish): 가격 고점 상승 + RSI 고점 하락 → 상승모멘텀 약화(고점 매도 참고)
    - 강세(bullish): 가격 저점 하락 + RSI 저점 상승 → 하락모멘텀 약화(저점 매수 참고)
    직전 피봇과 max_gap 봉 이내인 경우만 비교(너무 먼 과거 피봇은 무의미).
    반환: [{type, prior_date, date, price, rsi, prior_price, prior_rsi}, ...] date 오름차순.
    차트 표시용 — 피봇 확정에 쓰인 미래 봉을 그대로 노출하므로 ML/실시간 판단엔
    rsi_divergence() 사용.
    """
    close, rsi_series = close.align(rsi_series, join="inner")
    is_high, is_low = find_pivots(close, pivot_window)
    out: list[dict] = []
    for mask, kind, better in ((is_high, "bearish", lambda a, b: b > a),
                               (is_low, "bullish", lambda a, b: b < a)):
        idx = close.index[mask.fillna(False)]
        for a, b in zip(idx, idx[1:]):
            gap = close.index.get_loc(b) - close.index.get_loc(a)
            if gap < 1 or gap > max_gap:
                continue
            price_a, price_b = float(close[a]), float(close[b])
            rsi_a, rsi_b = rsi_series.get(a), rsi_series.get(b)
            if pd.isna(rsi_a) or pd.isna(rsi_b):
                continue
            price_diverges = better(price_a, price_b)
            rsi_confirms = (rsi_b < rsi_a) if kind == "bearish" else (rsi_b > rsi_a)
            if price_diverges and rsi_confirms:
                out.append({"type": kind, "prior_date": a, "date": b,
                           "price": price_b, "rsi": float(rsi_b),
                           "prior_price": price_a, "prior_rsi": float(rsi_a)})
    return sorted(out, key=lambda d: d["date"])


def rsi_divergence(close: pd.Series, rsi_series: pd.Series, *,
                    pivot_window: int = 5, max_gap: int = 60,
                    persist_bars: int | None = None) -> pd.Series:
    """다이버전스 방향 피처 — 약세=-1·강세=+1·해당없음=0. no-lookahead.

    피봇 고점/저점은 좌우 pivot_window 개 봉이 있어야 확정되므로, 행 t 의 값은
    t 시점에 아직 안 온 미래 봉을 쓰지 않도록 확정 시점(피봇 후 pivot_window 봉)
    으로 지연(shift) 배치한다 — compute_features 의 "행 t는 t 이전 데이터만
    사용" 불변식을 유지. 확정 시점부터 persist_bars(기본=pivot_window) 개 봉
    동안 값을 유지 — 단발 스파이크면 "현재 다이버전스 상태냐"를 마지막 봉만
    보는 소비자(단기신호·진입점수)가 확정 당일 외엔 항상 0으로 놓쳐버리기 때문
    (겹치면 최신 이벤트가 우선). 차트 등 완결된 구간 표시엔 rsi_divergence_events() 사용.
    """
    persist_bars = pivot_window if persist_bars is None else persist_bars
    out = pd.Series(0.0, index=close.index, name="rsi_divergence")
    events = rsi_divergence_events(close, rsi_series, pivot_window=pivot_window, max_gap=max_gap)
    n = len(close.index)
    for ev in events:
        try:
            confirm_pos = close.index.get_loc(ev["date"]) + pivot_window
        except KeyError:
            continue
        if confirm_pos >= n:
            continue
        end_pos = min(n, confirm_pos + max(1, persist_bars))
        out.iloc[confirm_pos:end_pos] = 1.0 if ev["type"] == "bullish" else -1.0
    return out


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


def stochastic(
    close: pd.Series,
    df: Optional[pd.DataFrame] = None,
    k_period: int = 14,
    d_period: int = 3,
) -> pd.DataFrame:
    """Stochastic Oscillator (%K, %D). 룩어헤드 없음."""
    if df is not None and {"high", "low"}.issubset(df.columns):
        hi = df["high"].astype(float)
        lo = df["low"].astype(float)
    else:
        hi = close
        lo = close
    lowest  = lo.rolling(k_period).min()
    highest = hi.rolling(k_period).max()
    stoch_k = 100 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    stoch_d = stoch_k.rolling(d_period).mean()
    return pd.DataFrame({"stoch_k": stoch_k, "stoch_d": stoch_d})


def williams_r(
    close: pd.Series,
    df: Optional[pd.DataFrame] = None,
    period: int = 14,
) -> pd.Series:
    """Williams %R — 과매수/과매도 오실레이터 (-100 ~ 0)."""
    if df is not None and {"high", "low"}.issubset(df.columns):
        hi = df["high"].astype(float)
        lo = df["low"].astype(float)
    else:
        hi = close
        lo = close
    highest = hi.rolling(period).max()
    lowest  = lo.rolling(period).min()
    return (-100 * (highest - close) / (highest - lowest).replace(0, np.nan)).rename(f"williams_r_{period}")


def cci(
    close: pd.Series,
    df: Optional[pd.DataFrame] = None,
    period: int = 20,
) -> pd.Series:
    """Commodity Channel Index. 일반적으로 ±100이 과매수/과매도 기준."""
    if df is not None and {"high", "low"}.issubset(df.columns):
        typical = (df["high"].astype(float) + df["low"].astype(float) + close) / 3
    else:
        typical = close
    ma  = typical.rolling(period).mean()
    mad = typical.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return ((typical - ma) / (0.015 * mad.replace(0, np.nan))).rename(f"cci_{period}")


def disparity(close: pd.Series, period: int) -> pd.Series:
    """이격도 — 현재가 / N일 이동평균 × 100.

    100 초과 = 이동평균 위 (과열), 100 미만 = 이동평균 아래 (침체).
    """
    ma = close.rolling(period, min_periods=period // 2).mean()
    return (close / ma.replace(0, np.nan) * 100).rename(f"disparity_{period}d")


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume — 거래량 누적 방향 지표."""
    sign  = np.sign(close.diff().fillna(0))
    _obv  = (sign * volume).cumsum()
    return _obv.rename("obv")


def cmf(close: pd.Series, df: pd.DataFrame, period: int = 21) -> pd.Series:
    """Chaikin Money Flow — 자금 유입/유출 강도."""
    if not {"high", "low", "volume"}.issubset(df.columns):
        return pd.Series(dtype=float, name=f"cmf_{period}")
    hi  = df["high"].astype(float)
    lo  = df["low"].astype(float)
    vol = df["volume"].astype(float)
    mfm = ((close - lo) - (hi - close)) / (hi - lo).replace(0, np.nan)
    mfv = mfm * vol
    return (mfv.rolling(period).sum() / vol.rolling(period).sum().replace(0, np.nan)).rename(f"cmf_{period}")


def price_acceleration(close: pd.Series, period: int = 5) -> pd.Series:
    """가격 가속도 (감마 프록시) — 모멘텀의 변화율.

    모멘텀이 증가하고 있는지(가속) 감소하고 있는지(감속)를 측정.
    양수 = 상승 가속, 음수 = 상승 둔화/하락 가속.
    """
    mom = close.pct_change(period)
    return mom.diff(period).rename(f"price_accel_{period}d")


def vol_of_vol(close: pd.Series, short: int = 10, long: int = 30) -> pd.Series:
    """변동성의 변동성 (VoV) — 변동성 변화 속도.

    VoV 상승 = 시장 불안정성 증가 (리스크오프 시그널).
    """
    rv_short = close.pct_change().rolling(short).std() * np.sqrt(252)
    rv_long  = close.pct_change().rolling(long).std()  * np.sqrt(252)
    return (rv_short / rv_long.replace(0, np.nan)).rename(f"vov_{short}_{long}")


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


def ichimoku_signals(close: pd.Series, df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Ichimoku 이진 신호 파생 — 원시값 대신 구름/크로스 상태를 인코딩.

    룩어헤드 없음: 센코우는 26일 선행이므로 현재 구름은 26일 전 계산값.
    """
    ic = ichimoku(close, df)
    tenkan = ic["ichi_tenkan"]
    kijun  = ic["ichi_kijun"]
    span_a = ic["ichi_senkou_a"]
    span_b = ic["ichi_senkou_b"]

    cloud_top    = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)

    return pd.DataFrame({
        "ichi_above_cloud":  (close > cloud_top).astype(float),    # 구름 위
        "ichi_below_cloud":  (close < cloud_bottom).astype(float),  # 구름 아래
        "ichi_cloud_bull":   (span_a > span_b).astype(float),       # 양운(녹색 구름)
        "ichi_tk_cross_up":  ((tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))).astype(float),  # 황금교차
        "ichi_tk_bull":      (tenkan > kijun).astype(float),        # 전환선 > 기준선
        "ichi_price_vs_kijun": (close / kijun.replace(0, np.nan) - 1),  # 기준선 이격
    })


def ma_cross_signals(close: pd.Series) -> pd.DataFrame:
    """이동평균 크로스오버 신호 — 추세 전환 탐지."""
    ema9   = close.ewm(span=9,   adjust=False).mean()
    ema21  = close.ewm(span=21,  adjust=False).mean()
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    sma20  = close.rolling(20).mean()
    sma5   = close.rolling(5).mean()

    return pd.DataFrame({
        "golden_cross":    (sma50 > sma200).astype(float),          # 골든 크로스 상태
        "ema_bull_short":  (ema9 > ema21).astype(float),            # 단기 EMA 강세
        "ma5_above_ma20":  (sma5 > sma20).astype(float),            # 초단기 강세
        "close_vs_sma20":  (close / sma20.replace(0, np.nan) - 1),  # SMA20 대비 위치
        "close_vs_sma50":  (close / sma50.replace(0, np.nan) - 1),  # SMA50 대비 위치
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
    """기술적 피처 전체 세트 계산.

    입력: OHLCV DataFrame (최소 'close' 필수; open/high/low/volume 선택).
    출력: 룩어헤드 없는 피처 DataFrame (행 t는 t 이전 데이터만 사용).

    피처 그룹:
      이동평균    : SMA(5/10/20/50/200), EMA(12/26/50)
      오실레이터  : RSI(14), RSI 다이버전스, MACD, Stochastic(14,3), Williams %R(14), CCI(20)
      밴드        : Bollinger(20)
      모멘텀      : 1/5/10/21/63/126일, 이격도(20/60/120), 가격가속도(감마)
      변동성      : 실현변동성(10/21/63), ATR(14), VoV(변동성의변동성)
      일목균형표  : 원시값 4개 + 신호 6개 (구름위치, 크로스, 이격)
      MA 크로스   : 골든크로스, EMA단기강세, SMA20/50 대비 위치
      거래량      : OBV, CMF(21), 거래량 비율, 거래량 Z-score
      52주        : 고점/저점 대비 위치
    """
    close = _require_close(df)
    parts: list[pd.DataFrame | pd.Series] = []

    # ── 이동평균 ─────────────────────────────────────────────────────────────
    for w in (5, 10, 20, 50, 200):
        parts.append(sma(close, w))
    for s in (12, 26, 50):
        parts.append(ema(close, s))

    # ── 오실레이터 ───────────────────────────────────────────────────────────
    rsi_14 = rsi(close, 14)
    parts.append(rsi_14)
    parts.append(rsi(close, 7))            # 단기 RSI
    parts.append(rsi_divergence(close, rsi_14))   # 다이버전스(약세-1/강세+1) — 랭커가 가중치 학습
    parts.append(macd(close))
    parts.append(stochastic(close, df))
    parts.append(williams_r(close, df))
    parts.append(cci(close, df))

    # ── 밴드 ─────────────────────────────────────────────────────────────────
    parts.append(bollinger(close, 20))

    # ── 모멘텀 & 이격도 ──────────────────────────────────────────────────────
    for p in (1, 5, 10, 21, 63, 126):
        parts.append(momentum(close, p))
    for d in (20, 60, 120):
        parts.append(disparity(close, d))   # 이격도
    parts.append(price_acceleration(close, 5))   # 단기 가격가속도 (감마)
    parts.append(price_acceleration(close, 20))  # 중기 가격가속도

    # ── 변동성 ───────────────────────────────────────────────────────────────
    for w in (10, 21, 63):
        parts.append(volatility(close, w))
    if include_atr:
        parts.append(atr(df, 14))
    parts.append(vol_of_vol(close))  # 변동성의 변동성

    # ── 일목균형표 ───────────────────────────────────────────────────────────
    if include_ichimoku:
        parts.append(ichimoku(close, df))
        parts.append(ichimoku_signals(close, df))

    # ── MA 크로스오버 신호 ───────────────────────────────────────────────────
    parts.append(ma_cross_signals(close))

    # ── 거래량 ───────────────────────────────────────────────────────────────
    if "volume" in df.columns:
        vol = df["volume"].astype(float)
        parts.append(vol.rolling(20).mean().rename("vol_sma_20"))
        vol_ratio = (vol / vol.rolling(20).mean().replace(0, np.nan)).rename("vol_ratio_20")
        parts.append(vol_ratio)
        vol_zscore = ((vol - vol.rolling(20).mean()) / vol.rolling(20).std().replace(0, np.nan)).rename("vol_zscore_20")
        parts.append(vol_zscore)
        parts.append(obv(close, vol))
        parts.append(cmf(close, df))

    # ── 52주 고저 ────────────────────────────────────────────────────────────
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
