"""ml/data_pipeline.py — 실시장 데이터 파이프라인 (MVP)

데이터 소스:
  - 유니버스  : Wikipedia (S&P500 / NASDAQ100 현재 구성종목)
  - 가격      : yfinance (일봉 5년, 캐시 1일)
  - Fear/Greed: 자체 proxy — VIX + QQQ모멘텀 + 신용스프레드 + 안전자산 강세
  - 매크로    : yfinance (^VIX, ^TNX, HYG, LQD, IEF, TLT)

주의: 현재 구성종목 기준 → survivorship bias 있음 (리포트에 명시 필요)

공개 API:
  fetch_universe(mode)         → list[str] 티커
  fetch_prices(tickers, days)  → dict[str, pd.DataFrame]  (OHLCV)
  build_fear_greed_proxy(days) → pd.Series  (0=극도공포, 100=극도탐욕)
  get_fg_proxy_score()         → float  (오늘 proxy 점수, 캐시 1h, 빠름)
  build_stock_features(ticker, prices, market) → pd.DataFrame
  build_ml_dataset(mode, days) → dict  {features, returns, market, universe}
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

CACHE_DIR   = Path(os.path.expanduser("~/reports/ml-cache"))
HEADERS     = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
PRICE_TTL_H = 6   # 가격 캐시 유효시간 (시간)

# 포트폴리오 보유 종목 (universe 'portfolio' 모드)
PORTFOLIO_TICKERS = ["MSFT", "QQQI", "ORCL", "NVDA", "GOOGL", "SAP", "UNH", "SGOV", "SPMO"]

# Fear/Greed proxy 재료
_MACRO_TICKERS = ["^VIX", "^TNX", "QQQ", "SPY", "HYG", "LQD", "IEF", "TLT", "ACWI"]


# ── 캐시 유틸 ─────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(key.encode()).hexdigest()[:8]
    return CACHE_DIR / f"{key[:40]}_{h}.pkl"


def _load_cache(key: str, ttl_hours: float) -> pd.DataFrame | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=ttl_hours):
        return None
    try:
        import pickle
        return pickle.loads(path.read_bytes())
    except Exception:
        return None


def _save_cache(key: str, df: pd.DataFrame) -> None:
    try:
        import pickle
        _cache_path(key).write_bytes(pickle.dumps(df))
    except Exception as e:
        logger.warning("캐시 저장 실패: %s", e)


# ── 유니버스 ──────────────────────────────────────────────────────────────────

def fetch_universe(
    mode: Literal["portfolio", "nasdaq100", "sp500", "all"] = "nasdaq100",
) -> list[str]:
    """종목 유니버스 반환.

    mode:
      portfolio  — 현재 보유 포트폴리오 (9종목, 빠름)
      nasdaq100  — Wikipedia NASDAQ100 (약 101종목)
      sp500      — Wikipedia S&P500 (약 503종목)
      all        — NASDAQ100 + S&P500 합집합
    """
    if mode == "portfolio":
        return list(PORTFOLIO_TICKERS)

    tickers: list[str] = []

    if mode in ("nasdaq100", "all"):
        tickers += _fetch_nasdaq100()
    if mode in ("sp500", "all"):
        tickers += _fetch_sp500()

    # 중복 제거, 정렬
    return sorted(set(tickers))


def _fetch_nasdaq100() -> list[str]:
    cache_key = "universe_nasdaq100"
    cached = _load_cache(cache_key, ttl_hours=24)
    if cached is not None:
        return cached["ticker"].tolist()

    try:
        r = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=HEADERS, timeout=15)
        tables = pd.read_html(io.StringIO(r.text), flavor="lxml")
        for t in tables:
            for col in ("Ticker", "Symbol"):
                if col in t.columns:
                    tickers = [s for s in t[col].tolist() if isinstance(s, str) and s.isalpha()]
                    df = pd.DataFrame({"ticker": tickers})
                    _save_cache(cache_key, df)
                    logger.info("NASDAQ100 유니버스: %d종목", len(tickers))
                    return tickers
    except Exception as e:
        logger.warning("NASDAQ100 유니버스 로드 실패: %s", e)

    # fallback: 핵심 30종목
    return ["AAPL","MSFT","NVDA","GOOGL","META","AMZN","TSLA","AVGO","COST",
            "NFLX","ASML","AMD","QCOM","INTC","INTU","AMAT","MU","LRCX","MRVL",
            "PANW","CDNS","SNPS","FTNT","KLAC","MCHP","ADI","ON","MPWR","TEAM","ZM"]


def _fetch_sp500() -> list[str]:
    cache_key = "universe_sp500"
    cached = _load_cache(cache_key, ttl_hours=24)
    if cached is not None:
        return cached["ticker"].tolist()

    try:
        r = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers=HEADERS, timeout=15,
        )
        tables = pd.read_html(io.StringIO(r.text), flavor="lxml")
        tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        df = pd.DataFrame({"ticker": tickers})
        _save_cache(cache_key, df)
        logger.info("S&P500 유니버스: %d종목", len(tickers))
        return tickers
    except Exception as e:
        logger.warning("S&P500 유니버스 로드 실패: %s", e)
        return []


# ── 가격 데이터 ───────────────────────────────────────────────────────────────

def fetch_prices(
    tickers: list[str],
    days: int = 1260,   # 약 5년
    batch_size: int = 20,
) -> dict[str, pd.DataFrame]:
    """yfinance로 OHLCV 다운로드. 종목별 캐시 적용.

    Returns:
        {ticker: DataFrame(Date, Open, High, Low, Close, Volume)}
    """
    import yfinance as yf

    result: dict[str, pd.DataFrame] = {}
    to_fetch: list[str] = []

    for ticker in tickers:
        key = f"price_{ticker}_{days}d"
        cached = _load_cache(key, ttl_hours=PRICE_TTL_H)
        if cached is not None:
            result[ticker] = cached
        else:
            to_fetch.append(ticker)

    if to_fetch:
        logger.info("가격 다운로드: %d종목", len(to_fetch))
        period = f"{days // 252 + 1}y"

        # 배치로 다운로드
        for i in range(0, len(to_fetch), batch_size):
            batch = to_fetch[i : i + batch_size]
            try:
                raw = yf.download(
                    batch, period=period, auto_adjust=True,
                    progress=False, threads=True,
                )
                if isinstance(raw.columns, pd.MultiIndex):
                    for ticker in batch:
                        try:
                            df = raw.xs(ticker, axis=1, level=1).dropna(how="all").copy()
                            df.index = pd.to_datetime(df.index).tz_localize(None)
                            if len(df) > 10:
                                result[ticker] = df
                                _save_cache(f"price_{ticker}_{days}d", df)
                        except Exception:
                            pass
                else:
                    # 단일 종목 반환
                    ticker = batch[0]
                    df = raw.dropna(how="all").copy()
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    if len(df) > 10:
                        result[ticker] = df
                        _save_cache(f"price_{ticker}_{days}d", df)
            except Exception as e:
                logger.warning("배치 다운로드 실패 %s: %s", batch, e)

    logger.info("가격 로드 완료: %d/%d종목", len(result), len(tickers))
    return result


# ── Fear / Greed Proxy ────────────────────────────────────────────────────────

def build_fear_greed_proxy(days: int = 1260) -> pd.Series:
    """자체 Fear/Greed proxy 지수 (0=극도공포, 100=극도탐욕).

    구성 요소 (각 0~100으로 정규화 후 평균):
      1. VIX 역수         — VIX 낮을수록 탐욕
      2. QQQ 125일 모멘텀 — 상승 추세일수록 탐욕
      3. 신용 스프레드    — HYG/IEF 비율 높을수록 탐욕 (정크 수요 강)
      4. 안전자산 강세    — TLT/SPY 비율 낮을수록 탐욕
      5. SPY RSI(14)      — RSI 높을수록 탐욕
    """
    cache_key = f"fear_greed_{days}d"
    cached = _load_cache(cache_key, ttl_hours=PRICE_TTL_H)
    if cached is not None:
        return cached["fg_score"]

    prices = fetch_prices(_MACRO_TICKERS, days=days)

    def _close(ticker: str) -> pd.Series | None:
        df = prices.get(ticker)
        return df["Close"] if df is not None and "Close" in df.columns else None

    def _rank_normalize(s: pd.Series, window: int = 252) -> pd.Series:
        """252일 롤링 백분위 → 0~100"""
        return s.rolling(window, min_periods=60).rank(pct=True) * 100

    components: list[pd.Series] = []

    # 1. VIX 역수 (VIX 높으면 공포)
    vix = _close("^VIX")
    if vix is not None:
        components.append(_rank_normalize(-vix).rename("inv_vix"))

    # 2. QQQ 125일 모멘텀
    qqq = _close("QQQ")
    if qqq is not None:
        mom = qqq / qqq.shift(125) - 1
        components.append(_rank_normalize(mom).rename("qqq_mom"))

    # 3. 신용 스프레드 (HYG/IEF — 정크 대 국채)
    hyg, ief = _close("HYG"), _close("IEF")
    if hyg is not None and ief is not None:
        credit = (hyg / ief).dropna()
        aligned = credit.reindex(hyg.index)
        components.append(_rank_normalize(aligned).rename("credit_demand"))

    # 4. 안전자산 역수 (TLT/SPY 높으면 공포)
    tlt, spy = _close("TLT"), _close("SPY")
    if tlt is not None and spy is not None:
        safe_haven = (tlt / spy).dropna()
        aligned = safe_haven.reindex(tlt.index)
        components.append(_rank_normalize(-aligned).rename("inv_safe_haven"))

    # 5. SPY RSI(14)
    if spy is not None:
        delta = spy.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        components.append(rsi.rename("spy_rsi"))

    if not components:
        logger.warning("Fear/Greed proxy 구성 실패 — 빈 시리즈 반환")
        return pd.Series(dtype=float, name="fg_score")

    # 공통 날짜 기준 평균
    df_all = pd.concat(components, axis=1).dropna(how="all")
    fg = df_all.mean(axis=1).rename("fg_score").clip(0, 100)

    _save_cache(cache_key, fg.to_frame())
    return fg


def get_fg_proxy_score() -> float:
    """오늘 Fear/Greed proxy 점수 반환 (0=극도공포, 100=극도탐욕).

    1년치 데이터만 사용해 빠르게 계산. 캐시 1시간.
    네트워크/계산 실패 시 -1 반환.
    """
    cache_key = "fg_proxy_today"
    cached = _load_cache(cache_key, ttl_hours=1.0)
    if cached is not None and "score" in cached.columns:
        return float(cached["score"].iloc[0])

    try:
        fg = build_fear_greed_proxy(days=252)
        if fg.empty:
            return -1.0
        score = float(fg.dropna().iloc[-1])
        import pickle
        _cache_path(cache_key).write_bytes(pickle.dumps(
            pd.DataFrame({"score": [score]})
        ))
        return score
    except Exception as e:
        logger.warning("get_fg_proxy_score 실패: %s", e)
        return -1.0


# ── 종목별 피처 ───────────────────────────────────────────────────────────────

def build_stock_features(
    ticker: str,
    price_df: pd.DataFrame,
    market_features: pd.DataFrame,
) -> pd.DataFrame:
    """단일 종목 피처 생성.

    피처 목록:
      가격 기반  : mom_20d, mom_60d, mom_125d (벤치마크 초과 모멘텀)
                   vol_20d (실현변동성), dist_52w_high, dist_52w_low
                   rsi_14, above_ma50, above_ma200
      시장 공통  : fg_score, vix (Fear/Greed + VIX)
    """
    close = price_df["Close"].copy()
    if len(close) < 60:
        return pd.DataFrame()

    feat = pd.DataFrame(index=close.index)

    # 모멘텀 (절대)
    for w in (20, 60, 125):
        feat[f"mom_{w}d"] = close / close.shift(w) - 1

    # 실현변동성 (20일)
    feat["vol_20d"] = close.pct_change().rolling(20).std() * np.sqrt(252)

    # 52주 고저 대비
    high_52w = close.rolling(252, min_periods=60).max()
    low_52w  = close.rolling(252, min_periods=60).min()
    feat["dist_52w_high"] = close / high_52w - 1   # ≤ 0
    feat["dist_52w_low"]  = close / low_52w - 1    # ≥ 0

    # RSI(14)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    feat["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # MA 위치
    feat["above_ma50"]  = (close > close.rolling(50).mean()).astype(float)
    feat["above_ma200"] = (close > close.rolling(200).mean()).astype(float)

    # 시장 공통 피처 병합
    feat = feat.join(market_features, how="left")

    return feat.dropna(how="all")


# ── 메인 데이터셋 빌더 ────────────────────────────────────────────────────────

def build_ml_dataset(
    mode: Literal["portfolio", "nasdaq100", "sp500", "all"] = "nasdaq100",
    days: int = 1260,
    forward_days: int = 20,
) -> dict:
    """ML 학습용 데이터셋 구성.

    Returns:
        features  : pd.DataFrame  (date × ticker → flat index, 피처 컬럼)
        returns   : pd.Series     (forward_days 후 수익률, 타겟)
        excess    : pd.Series     (QQQ 대비 초과수익률, 타겟)
        universe  : list[str]
        fg_score  : pd.Series     (Fear/Greed proxy)
        meta      : dict          (mode, days, forward_days, bias_warning)
    """
    logger.info("ML 데이터셋 구성 시작 (mode=%s, days=%d, fwd=%d일)", mode, days, forward_days)

    universe = fetch_universe(mode)
    logger.info("유니버스: %d종목", len(universe))

    # 가격 다운로드 (벤치마크 포함)
    all_tickers = list(set(universe + ["QQQ", "SPY", "^VIX", "HYG", "LQD", "IEF", "TLT"]))
    prices = fetch_prices(all_tickers, days=days)

    # Fear/Greed proxy
    fg = build_fear_greed_proxy(days=days)

    # 시장 공통 피처 (Fear/Greed + VIX)
    vix_df = prices.get("^VIX")
    market_feat = fg.to_frame("fg_score")
    if vix_df is not None:
        market_feat["vix"] = vix_df["Close"]
    market_feat = market_feat.ffill()

    # QQQ 선행 수익률 (초과수익 계산용)
    qqq_close = prices.get("QQQ", pd.DataFrame()).get("Close")

    all_features: list[pd.DataFrame] = []
    all_returns:  list[pd.Series]    = []
    all_excess:   list[pd.Series]    = []

    for ticker in universe:
        df = prices.get(ticker)
        if df is None or len(df) < 126:
            continue

        feat = build_stock_features(ticker, df, market_feat)
        if feat.empty:
            continue

        close = df["Close"].reindex(feat.index)
        fwd_ret = close.pct_change(forward_days).shift(-forward_days)

        # QQQ 초과수익
        if qqq_close is not None:
            qqq_fwd = qqq_close.reindex(feat.index).pct_change(forward_days).shift(-forward_days)
            excess = fwd_ret - qqq_fwd
        else:
            excess = fwd_ret.copy()

        # MultiIndex (date, ticker)
        feat.index = pd.MultiIndex.from_arrays(
            [feat.index, [ticker] * len(feat)], names=["date", "ticker"]
        )
        fwd_ret.index = feat.index
        excess.index  = feat.index

        all_features.append(feat)
        all_returns.append(fwd_ret)
        all_excess.append(excess)

    if not all_features:
        logger.warning("유효 종목 없음 — 빈 데이터셋 반환")
        return {"features": pd.DataFrame(), "returns": pd.Series(), "excess": pd.Series(),
                "universe": [], "fg_score": fg, "meta": {}}

    features = pd.concat(all_features)
    returns  = pd.concat(all_returns).rename("fwd_return")
    excess   = pd.concat(all_excess).rename("fwd_excess")

    logger.info(
        "데이터셋 완성: %d행 × %d피처 | 종목 %d개",
        len(features), features.shape[1],
        features.index.get_level_values("ticker").nunique(),
    )

    return {
        "features": features,
        "returns":  returns,
        "excess":   excess,
        "universe": universe,
        "fg_score": fg,
        "meta": {
            "mode": mode,
            "days": days,
            "forward_days": forward_days,
            "bias_warning": "현재 구성종목 기준 — survivorship bias 있음",
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# ── sweet_spot 호환 실데이터 빌더 ──────────────────────────────────────────────

def build_real_sweetspot_data(
    asset_ticker: str = "QQQ",
    days: int = 756,
) -> dict:
    """실시장 데이터를 sweet_spot.optimize_sweet_spot() 호환 포맷으로 반환.

    generate_synthetic_market_data()와 동일한 키 구조:
      close      — pd.Series  (asset 종가)
      spy_close  — pd.Series  (SPY 종가)
      qqq_close  — pd.Series  (QQQ 종가)
      features   — pd.DataFrame  (momentum, volatility, sentiment)

    피처:
      momentum   — 20일 수익률 (실제 모멘텀)
      volatility — 20일 실현변동성
      sentiment  — (RSI14 - 50) / 50  ([-1, 1] 정규화, 과매도→음수)
    """
    tickers = list({asset_ticker, "SPY", "QQQ"})
    prices  = fetch_prices(tickers, days=days)

    def _close(t: str) -> pd.Series | None:
        df = prices.get(t)
        return df["Close"] if df is not None and "Close" in df.columns else None

    asset = _close(asset_ticker)
    spy   = _close("SPY")
    qqq   = _close("QQQ")

    if asset is None:
        raise ValueError(f"{asset_ticker} 가격 조회 실패")

    # 공통 날짜 인덱스
    idx = asset.dropna().index
    if spy is not None:
        idx = idx.intersection(spy.dropna().index)
    if qqq is not None:
        idx = idx.intersection(qqq.dropna().index)

    asset = asset.reindex(idx)
    spy   = (spy.reindex(idx)   if spy   is not None else asset.copy().rename("SPY"))
    qqq   = (qqq.reindex(idx)   if qqq   is not None else asset.copy().rename("QQQ"))

    # 피처 계산
    mom  = asset.pct_change(20).fillna(0)
    vol  = asset.pct_change().rolling(20, min_periods=1).std().fillna(0)
    delta = asset.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    sent  = ((rsi - 50) / 50).fillna(0)

    features = pd.DataFrame({
        "momentum":   mom,
        "volatility": vol,
        "sentiment":  sent,
    }, index=idx)

    return {
        "close":     asset.rename(asset_ticker),
        "spy_close": spy.rename("SPY"),
        "qqq_close": qqq.rename("QQQ"),
        "features":  features,
    }
