"""ml/data_pipeline.py — 실시장 데이터 파이프라인 (MVP)

데이터 소스:
  - 유니버스  : Wikipedia (S&P500 / NASDAQ100 현재 구성종목)
  - 가격      : yfinance (일봉 5년, 캐시 1일)
  - Fear/Greed: 자체 proxy — VIX + QQQ모멘텀 + 신용스프레드 + 안전자산 강세
  - 매크로    : yfinance (^VIX, ^TNX, HYG, LQD, IEF, TLT)

주의(survivorship bias): 유니버스가 *현재* 구성종목 기준이라 과거 시점에
  탈락·상장폐지된 종목이 빠져 있다. 학습·백테스트는 '살아남은' 종목만 보므로
  과거 성과(CAGR·Sharpe)가 상향 편향될 수 있다 — 정성 추정으로 연 +1~3%p
  CAGR 과대평가 가능(학계 SP500/NASDAQ 연구 통상 범위, 본 유니버스 미측정).
  실거래·학습은 어차피 생존 종목 대상이라 운용엔 영향이 적으나, 보고되는
  백테스트 수치는 낙관 쪽으로 읽을 것. (리포트에 명시 필요)

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
import time
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

# yfinance 배치 다운로드 견고성 파라미터
PRICE_MAX_RETRIES   = 3            # 배치별 최대 재시도 횟수
PRICE_BACKOFF_BASE  = 2.0          # 지수 백오프 기준 (2s·4s·8s)
PRICE_SHRINK_STEPS  = (20, 10, 5)  # 반복 실패 시 배치 크기 동적 축소 단계

# 포트폴리오 보유 종목 (universe 'portfolio' 모드) — 단일 소스에서 파생
try:
    from portfolio_universe import load_portfolio_tickers as _load_portfolio_tickers
    PORTFOLIO_TICKERS = _load_portfolio_tickers()
except Exception:
    PORTFOLIO_TICKERS = ["MSFT", "QQQI", "ORCL", "NVDA", "GOOGL", "SAP", "UNH", "SGOV", "SPMO"]

# 미국 시가총액 상위 100개 (섹터 다변화, 2025-26 기준)
US_TOP100 = [
    # 빅테크 / AI / 클라우드
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "CRM",  # ticker-ok 시장 유니버스
    "ADBE", "INTU", "NOW", "SNOW", "PLTR", "UBER", "SHOP",  # ticker-ok 시장 유니버스
    # 반도체 (미국 + 대만)
    "TSM", "QCOM", "AMD", "INTC", "TXN", "AMAT", "KLAC", "MU", "ASML", "LRCX", "MRVL", "ON",
    # 금융 (은행·결제·자산운용)
    "BRK-B", "JPM", "V", "MA", "BAC", "GS", "MS", "WFC",
    "AXP", "C", "COF", "SCHW", "CME", "BLK", "SPGI", "ICE",
    # 헬스케어 / 바이오 / 보험
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "ISRG",
    "PFE", "GILD", "REGN", "MDT", "CVS", "CI", "ZTS", "BMY",
    # 소비재 / 유통 / 미디어
    "WMT", "COST", "HD", "MCD", "KO", "PEP", "NKE",
    "SBUX", "TGT", "LOW", "DIS", "CMCSA",
    # 에너지
    "XOM", "CVX", "COP", "SLB", "EOG",
    # 산업재 / 항공방산 / 물류
    "GE", "CAT", "HON", "RTX", "LMT", "BA", "UPS", "DE", "ETN",
    # 통신
    "T", "VZ",
    # 부동산 / 인프라 / 유틸리티
    "NEE", "PLD", "AMT",
    # 소재 / 화학
    "LIN",
    # 기타
    "NFLX", "ACN", "PYPL", "CB", "F", "GM", "AMGN",
]
# 하위 호환: 기존 US_TOP50 참조 코드를 위한 별칭
US_TOP50 = US_TOP100

# 한국 시가총액 상위 10개 (KOSPI, 2025-26 기준)
# 표시명: {티커: (한글명, 영문명, 섹터)}
KR_TOP10_META: dict[str, tuple[str, str, str]] = {
    "005930.KS": ("삼성전자",       "Samsung Electronics", "반도체"),
    "000660.KS": ("SK하이닉스",     "SK Hynix",            "반도체"),
    "373220.KS": ("LG에너지솔루션", "LG Energy Solution",  "2차전지"),
    "207940.KS": ("삼성바이오로직스","Samsung Biologics",   "바이오"),
    "005380.KS": ("현대차",         "Hyundai Motor",       "자동차"),
    "005490.KS": ("포스코홀딩스",   "POSCO Holdings",      "철강"),
    "035420.KS": ("NAVER",          "NAVER",               "IT"),
    "035720.KS": ("카카오",         "Kakao",               "IT"),
    "000270.KS": ("기아",           "Kia",                 "자동차"),
    "006400.KS": ("삼성SDI",        "Samsung SDI",         "2차전지"),
}
KR_TOP10 = list(KR_TOP10_META.keys())

# 한국 시가총액 상위 30개 (KR 전용 ML 학습 폭 확보 — KR_TOP10 + 20)
KR_TOP30 = KR_TOP10 + [
    "105560.KS", "055550.KS", "012330.KS", "028260.KS", "066570.KS",
    "051910.KS", "096770.KS", "032830.KS", "015760.KS", "017670.KS",
    "030200.KS", "086790.KS", "000810.KS", "009150.KS", "010130.KS",
    "011200.KS", "018260.KS", "034730.KS", "011070.KS", "003670.KS",
]
# KR 벤치마크 지수 (초과수익·베타 기준) — KOSPI 종합
KR_BENCHMARK = "^KS11"

# Fear/Greed proxy 재료
_MACRO_TICKERS = ["^VIX", "^TNX", "QQQ", "SPY", "HYG", "LQD", "IEF", "TLT", "ACWI"]


# ── 캐시 유틸 ─────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    from ml._safe_cache import harden_cache_dir
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    harden_cache_dir(CACHE_DIR)  # 0700 best-effort — 타 사용자 파일 주입 방지
    h = hashlib.md5(key.encode()).hexdigest()[:8]
    return CACHE_DIR / f"{key[:40]}_{h}.pkl"


def _load_cache(key: str, ttl_hours: float) -> pd.DataFrame | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=ttl_hours):
        return None
    # 안전 로더: 심링크·소유자 검증 후 역직렬화(실패 시 None=캐시 미스)
    from ml._safe_cache import safe_unpickle
    return safe_unpickle(path)


def _save_cache(key: str, df: pd.DataFrame) -> None:
    try:
        import pickle
        _cache_path(key).write_bytes(pickle.dumps(df))
    except Exception as e:
        logger.warning("캐시 저장 실패: %s", e)


# ── 유니버스 ──────────────────────────────────────────────────────────────────

def fetch_universe(
    mode: Literal["portfolio", "nasdaq100", "sp500", "all",
                  "us_top50", "kr_top10", "kr30", "watch"] = "nasdaq100",
) -> list[str]:
    """종목 유니버스 반환.

    mode:
      portfolio  — 현재 보유 포트폴리오 (9종목, 빠름)
      us_top50   — 미국 시가총액 상위 50개 (하드코딩, 안정적)
      kr_top10   — 한국 시가총액 상위 10개 KOSPI (.KS 티커)
      watch      — 포트폴리오 + us_top50 + kr_top10 전체 감시 대상
      nasdaq100  — Wikipedia NASDAQ100 (약 101종목)
      sp500      — Wikipedia S&P500 (약 503종목)
      all        — NASDAQ100 + S&P500 합집합

    survivorship bias: 모든 모드가 *현재* 시점 구성종목을 반환한다 (point-in-time
    재구성 아님). 과거 탈락·상폐 종목이 빠져 백테스트 CAGR 이 상향 편향될 수 있고
    (정성 추정 연 +1~3%p), 학습 역시 생존 종목만 본다. 운용엔 영향이 작지만
    보고 수치는 낙관 쪽으로 해석할 것. (모듈 상단 docstring 참조)
    """
    if mode == "portfolio":
        return list(PORTFOLIO_TICKERS)
    if mode in ("us_top50", "us_top100"):
        return list(US_TOP100)
    if mode == "kr_top10":
        return list(KR_TOP10)
    if mode == "kr30":
        return list(KR_TOP30)
    if mode == "watch":
        combined = list(PORTFOLIO_TICKERS) + list(US_TOP50) + list(KR_TOP10)
        return list(dict.fromkeys(combined))   # 순서 유지 중복 제거

    tickers: list[str] = []
    if mode in ("nasdaq100", "all"):
        tickers += _fetch_nasdaq100()
    if mode in ("sp500", "all"):
        tickers += _fetch_sp500()
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

def _store_batch_result(
    raw: pd.DataFrame,
    batch: list[str],
    days: int,
    result: dict[str, pd.DataFrame],
) -> None:
    """yf.download 응답을 종목별로 분해해 result/캐시에 적재.

    종목 단위 try/except 로 부분 실패를 격리한다 (한 종목 파싱 실패가
    같은 배치의 다른 종목을 막지 않음).
    """
    if raw is None or len(raw) == 0:
        return
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
        # 단일 종목 반환 형태
        ticker = batch[0]
        try:
            df = raw.dropna(how="all").copy()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            if len(df) > 10:
                result[ticker] = df
                _save_cache(f"price_{ticker}_{days}d", df)
        except Exception:
            pass


def _download_batch_with_retry(
    yf,
    batch: list[str],
    period: str,
    days: int,
    result: dict[str, pd.DataFrame],
    batch_seq: int,
) -> bool:
    """단일 배치를 지수 백오프로 재시도하며 다운로드.

    재시도 사이 대기: PRICE_BACKOFF_BASE^attempt 초 (2·4·8s) + 고정 지터.
    지터는 batch_seq 기반 결정론적 값(0~0.5s) — random 미사용으로 재현 가능.

    Returns:
        True  — 배치에서 1종목 이상 적재 성공
        False — 모든 재시도 실패 (적재 0종목)
    """
    before = len(result)
    # 배치 순번 기반 고정 지터 (0.0 ~ 0.45s, 동시 배치 thundering-herd 완화)
    jitter = (batch_seq % 10) / 20.0

    for attempt in range(PRICE_MAX_RETRIES):
        try:
            raw = yf.download(
                batch, period=period, auto_adjust=True,
                progress=False, threads=True,
            )
            _store_batch_result(raw, batch, days, result)
            if len(result) > before:
                return True
            # 예외 없이 빈 응답 — 재시도로 회복될 여지가 있어 동일 백오프 적용
            logger.warning("배치 빈 응답 %s (시도 %d/%d)", batch, attempt + 1, PRICE_MAX_RETRIES)
        except Exception as e:
            logger.warning("배치 다운로드 실패 %s (시도 %d/%d): %s",
                           batch, attempt + 1, PRICE_MAX_RETRIES, e)

        # 마지막 시도가 아니면 백오프 후 재시도
        if attempt < PRICE_MAX_RETRIES - 1:
            delay = PRICE_BACKOFF_BASE ** (attempt + 1) + jitter
            time.sleep(delay)

    return len(result) > before


def fetch_prices(
    tickers: list[str],
    days: int = 1260,   # 약 5년
    batch_size: int = 20,
) -> dict[str, pd.DataFrame]:
    """yfinance로 OHLCV 다운로드. 종목별 캐시 적용.

    견고성:
      - 배치별 지수 백오프 재시도 (최대 PRICE_MAX_RETRIES회, 2·4·8s + 고정 지터)
      - 반복 실패 시 배치 크기 동적 축소 (20→10→5) 후 재시도
      - 종목 단위 부분 실패 격리 (_store_batch_result)
      - 캐시 우선 사용 (TTL 내 캐시는 네트워크 호출 없이 반환)

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

        # 1차 배치 크기는 호출자 지정값, 이후 축소 단계는 PRICE_SHRINK_STEPS 기준
        batch_seq = 0
        for i in range(0, len(to_fetch), batch_size):
            batch = to_fetch[i : i + batch_size]
            ok = _download_batch_with_retry(yf, batch, period, days, result, batch_seq)
            batch_seq += 1

            # 배치 전체 실패 시 더 작은 단위로 쪼개 재시도 (부분 회복 시도).
            # 각 축소 단계는 '아직 미확보' 종목만 더 작은 서브배치로 재시도하고,
            # 전부 확보될 때까지 다음(더 작은) 단계로 계속 내려간다.
            if not ok and len(batch) > PRICE_SHRINK_STEPS[-1]:
                for sub_size in PRICE_SHRINK_STEPS:
                    remaining = [t for t in batch if t not in result]
                    if not remaining:
                        break  # 전 종목 확보 완료
                    if sub_size >= len(remaining):
                        continue  # 현재 미확보 수보다 큰 분할은 의미 없음
                    logger.warning("배치 축소 재시도: %d종목 → %d씩 분할", len(remaining), sub_size)
                    for j in range(0, len(remaining), sub_size):
                        sub = remaining[j : j + sub_size]
                        _download_batch_with_retry(yf, sub, period, days, result, batch_seq)
                        batch_seq += 1

    loaded = len(result)
    requested = len(tickers)
    failed = requested - loaded
    if failed > 0:
        logger.warning("가격 로드 실패 종목 %d/%d개 (캐시·재시도·축소 후에도 미확보)",
                       failed, requested)
    logger.info("가격 로드 완료: %d/%d종목", loaded, requested)
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
    qqq_close: pd.Series | None = None,
    sector_id: int = 0,
) -> pd.DataFrame:
    """단일 종목 전체 피처 생성.

    피처 그룹 (features.py + 추가):
      기술적     : 이동평균(SMA/EMA), 오실레이터(RSI/MACD/Stochastic/Williams%R/CCI)
                   Bollinger, 모멘텀(6개 기간), 이격도(20/60/120), 가격가속도(감마)
                   실현변동성, ATR, VoV(변동성의변동성)
      일목균형표 : 원시값 4개 + 신호 6개 (구름위치, TK크로스, 기준선이격)
      MA크로스   : 골든크로스, EMA단기강세, SMA20/50 위치
      거래량     : OBV, CMF, 거래량비율, 거래량Z-score
      52주       : 고저 대비 위치
      종목고유   : QQQ 초과모멘텀(60d), beta_60d, 섹터ID, 생존편향페널티
      시장공통   : fg_score, vix (market_features에서 병합)
    """
    from ml.features import (
        compute_features, stochastic, williams_r, cci, disparity,
        obv, cmf, price_acceleration, vol_of_vol,
        ichimoku_signals, ma_cross_signals,
    )

    if len(price_df) < 60:
        return pd.DataFrame()

    close = price_df["Close"].copy()

    # OHLCV → features.py compute_features 호환 포맷
    df_feat = price_df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })

    # ── 기술적 피처 전체 세트 ──────────────────────────────────────────────
    tech = compute_features(df_feat, include_ichimoku=True, include_atr=True)

    # ── 종목 고유 피처 ─────────────────────────────────────────────────────
    extra = pd.DataFrame(index=close.index)

    # QQQ 대비 초과 모멘텀 (60일)
    if qqq_close is not None:
        qqq_r = qqq_close.reindex(close.index).ffill()
        extra["excess_mom_60d"] = (close / close.shift(60) - 1) - (qqq_r / qqq_r.shift(60) - 1)
        extra["excess_mom_20d"] = (close / close.shift(20) - 1) - (qqq_r / qqq_r.shift(20) - 1)
        # QQQ 대비 베타 (60일 롤링)
        rets    = close.pct_change()
        qqq_ret = qqq_r.pct_change()
        cov = rets.rolling(60).cov(qqq_ret)
        var = qqq_ret.rolling(60).var().replace(0, np.nan)
        extra["beta_60d"]  = cov / var
        extra["beta_20d"]  = rets.rolling(20).cov(qqq_ret) / qqq_ret.rolling(20).var().replace(0, np.nan)
        # 감마: 베타의 변화율 (베타 가속도)
        extra["beta_gamma"] = extra["beta_60d"].diff(20)
    else:
        for col in ("excess_mom_60d", "excess_mom_20d", "beta_60d", "beta_20d", "beta_gamma"):
            extra[col] = np.nan

    extra["sector_id"] = float(sector_id)

    # ── 합산 ──────────────────────────────────────────────────────────────
    feat = pd.concat([tech, extra], axis=1)
    feat = feat.join(market_features, how="left")
    return feat.dropna(how="all")


# ── 섹터 매핑 ────────────────────────────────────────────────────────────────

_SECTOR_LABELS = {
    "Technology": 1, "Communication Services": 2, "Consumer Discretionary": 3,
    "Health Care": 4, "Financials": 5, "Industrials": 6,
    "Consumer Staples": 7, "Energy": 8, "Materials": 9,
    "Real Estate": 10, "Utilities": 11,
}


def _get_sector_map(tickers: list[str]) -> dict[str, int]:
    """yfinance info로 섹터 조회 → 정수 매핑. 실패 시 0."""
    cache_key = "sector_map_" + hashlib.md5(",".join(sorted(tickers)).encode()).hexdigest()[:8]
    cached = _load_cache(cache_key, ttl_hours=168)  # 1주일 캐시
    if cached is not None and "ticker" in cached.columns:
        return dict(zip(cached["ticker"], cached["sector_id"]))

    import yfinance as yf
    result: dict[str, int] = {}
    for ticker in tickers:
        try:
            sector = yf.Ticker(ticker).info.get("sector", "") or ""
            result[ticker] = _SECTOR_LABELS.get(sector, 0)
        except Exception:
            result[ticker] = 0

    df = pd.DataFrame({"ticker": list(result.keys()), "sector_id": list(result.values())})
    _save_cache(cache_key, df)
    return result


# ── 메인 데이터셋 빌더 ────────────────────────────────────────────────────────

def index_multitf_rsi(close: "pd.Series") -> "pd.DataFrame":
    """지수(벤치마크) 일봉/주봉/월봉 RSI(14) — 일별 인덱스로 정렬.

    룩어헤드 방지: 일봉 RSI 는 당일 종가까지(시점 정합), 주/월봉 RSI 는 **직전 완성 봉**만
    사용(shift(1)) 후 ffill — 진행 중인 주/월의 미완성 종가를 쓰지 않는다.
    """
    from ml.features import rsi
    out = pd.DataFrame(index=close.index)
    c = close.dropna()
    out["idx_rsi_d"] = rsi(c, 14).reindex(close.index)
    wk = rsi(c.resample("W").last(), 14).shift(1)        # 직전 완성 주봉
    out["idx_rsi_w"] = wk.reindex(close.index, method="ffill")
    mo = rsi(c.resample("ME").last(), 14).shift(1)       # 직전 완성 월봉
    out["idx_rsi_m"] = mo.reindex(close.index, method="ffill")
    return out


def build_ml_dataset(
    mode: Literal["portfolio", "nasdaq100", "sp500", "all", "kr_top10", "kr30"] = "nasdaq100",
    days: int = 1260,
    forward_days: int = 20,
    benchmark_ticker: str = "QQQ",
) -> dict:
    """ML 학습용 데이터셋 구성.

    benchmark_ticker: 초과수익·베타 기준 지수(미국=QQQ, 한국=^KS11 KOSPI).
                      excess/beta/excess_mom 피처와 라벨이 이 벤치마크 대비로 계산됨.

    Returns:
        features  : pd.DataFrame  (date × ticker → flat index, 피처 컬럼)
        returns   : pd.Series     (forward_days 후 수익률, 타겟)
        excess    : pd.Series     (벤치마크 대비 초과수익률, 타겟)
        universe  : list[str]
        fg_score  : pd.Series     (Fear/Greed proxy)
        meta      : dict          (mode, days, forward_days, benchmark, bias_warning)
    """
    logger.info("ML 데이터셋 구성 시작 (mode=%s, days=%d, fwd=%d일, bench=%s)",
                mode, days, forward_days, benchmark_ticker)

    universe = fetch_universe(mode)
    logger.info("유니버스: %d종목", len(universe))

    # 가격 다운로드 (벤치마크 포함)
    all_tickers = list(set(universe + [benchmark_ticker, "QQQ", "SPY", "^VIX", "HYG", "LQD", "IEF", "TLT"]))
    prices = fetch_prices(all_tickers, days=days)

    # Fear/Greed proxy
    fg = build_fear_greed_proxy(days=days)

    # 시장 공통 피처 (Fear/Greed + VIX + 매크로)
    vix_df = prices.get("^VIX")
    market_feat = fg.to_frame("fg_score")
    if vix_df is not None:
        market_feat["vix"] = vix_df["Close"]

    market_feat = market_feat.ffill(limit=5)
    # 참고: 매크로 피처(수익률곡선·크레딧·달러 등)는 종목 간 동일값이므로
    # 크로스섹셔널 Ranker에 포함하지 않음. LeverageModel/MetaAllocator에서 별도 사용.

    # 벤치마크 선행 수익률 (초과수익·베타 계산용 — 미국 QQQ / 한국 KOSPI)
    bench_df = prices.get(benchmark_ticker)
    qqq_close = bench_df.get("Close") if bench_df is not None else None
    if qqq_close is None:
        logger.warning("벤치마크 %s 가격 없음 — 초과수익이 절대수익으로 폴백", benchmark_ticker)
    else:
        # 지수 다중 타임프레임 RSI(일/주/월)를 시장공통 피처로 추가(전 종목 broadcast)
        try:
            market_feat = market_feat.join(index_multitf_rsi(qqq_close), how="left").ffill(limit=5)
        except Exception as e:
            logger.warning("지수 다중TF RSI 생성 실패: %s", e)

    all_features: list[pd.DataFrame] = []
    all_returns:  list[pd.Series]    = []
    all_excess:   list[pd.Series]    = []

    # 섹터 매핑 (GICS 11개 섹터 정수 인코딩)
    sector_map = _get_sector_map(universe)

    for ticker in universe:
        df = prices.get(ticker)
        if df is None or len(df) < 126:
            continue

        sector_id = sector_map.get(ticker, 0)
        feat = build_stock_features(ticker, df, market_feat,
                                    qqq_close=qqq_close, sector_id=sector_id)
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
            "benchmark": benchmark_ticker,
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
      features   — pd.DataFrame  (8개 피처)

    피처:
      momentum    — 20일 수익률
      momentum_60 — 60일 수익률 (중기 트렌드)
      volatility  — 20일 실현변동성
      sentiment   — (RSI14 - 50) / 50  ([-1, 1])
      above_ma200 — 200일 MA 위 여부 (0/1)
      vix_norm    — VIX 백분위 역수 (높을수록 탐욕)
      credit_sprd — HYG/IEF 비율 정규화 (높을수록 신용 낙관)
      fg_proxy    — Fear/Greed proxy 백분위 (0~1)
    """
    macro_tickers = list({asset_ticker, "SPY", "QQQ", "^VIX", "HYG", "IEF"})
    prices  = fetch_prices(macro_tickers, days=days + 60)   # 60일 여유

    def _close(t: str) -> pd.Series | None:
        df = prices.get(t)
        return df["Close"] if df is not None and "Close" in df.columns else None

    asset = _close(asset_ticker)
    spy   = _close("SPY")
    qqq   = _close("QQQ")
    vix   = _close("^VIX")
    hyg   = _close("HYG")
    ief   = _close("IEF")

    if asset is None:
        raise ValueError(f"{asset_ticker} 가격 조회 실패")

    # 공통 날짜 인덱스
    idx = asset.dropna().index
    for s in (spy, qqq):
        if s is not None:
            idx = idx.intersection(s.dropna().index)
    idx = idx[-days:]   # 최신 days일만 사용

    asset = asset.reindex(idx)
    spy   = spy.reindex(idx)  if spy  is not None else asset.copy().rename("SPY")
    qqq   = qqq.reindex(idx)  if qqq  is not None else asset.copy().rename("QQQ")

    # 기본 피처
    mom   = asset.pct_change(20).fillna(0)
    mom60 = asset.pct_change(60).fillna(0)
    vol   = asset.pct_change().rolling(20, min_periods=5).std().fillna(0)

    delta = asset.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=5).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=5).mean()
    rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    sent  = ((rsi - 50) / 50).fillna(0)

    ma200       = asset.rolling(200, min_periods=100).mean()
    above_ma200 = (asset > ma200).astype(float).fillna(0.5)

    # VIX 백분위 역수 (낮은 VIX = 낙관 → 1에 가까움)
    if vix is not None:
        vix_r = vix.reindex(idx).ffill()
        vix_norm = 1 - vix_r.rolling(252, min_periods=60).rank(pct=True).fillna(0.5)
    else:
        vix_norm = pd.Series(0.5, index=idx)

    # 신용 스프레드 (HYG/IEF 비율 백분위)
    if hyg is not None and ief is not None:
        hyg_r  = hyg.reindex(idx).ffill()
        ief_r  = ief.reindex(idx).ffill()
        credit = (hyg_r / ief_r).rolling(252, min_periods=60).rank(pct=True).fillna(0.5)
    else:
        credit = pd.Series(0.5, index=idx)

    # Fear/Greed proxy (0~100 → 0~1)
    try:
        fg = build_fear_greed_proxy(days=days + 60)
        fg_aligned = fg.reindex(idx).ffill().fillna(50.0) / 100.0
    except Exception:
        fg_aligned = pd.Series(0.5, index=idx)

    features = pd.DataFrame({
        "momentum":    mom,
        "momentum_60": mom60,
        "volatility":  vol,
        "sentiment":   sent,
        "above_ma200": above_ma200,
        "vix_norm":    vix_norm,
        "credit_sprd": credit,
        "fg_proxy":    fg_aligned,
    }, index=idx).fillna(0)

    return {
        "close":     asset.rename(asset_ticker),
        "spy_close": spy.rename("SPY"),
        "qqq_close": qqq.rename("QQQ"),
        "features":  features,
    }
