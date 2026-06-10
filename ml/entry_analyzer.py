"""ml/entry_analyzer.py — 레버리지 ETF + 개별주 진입 타점 분석

현재 시장 지표 + 과거 유사 조건 분포 → 진입 확률·기대수익·신호 산출.

알고리즘:
  1. 현재 특징 벡터 추출 (낙폭, RSI, 모멘텀, VIX, 거래량)
  2. 과거 5년 동일 특징 벡터 계산
  3. 코사인 유사도 상위 N개 유사 기간 탐색 (k-NN)
  4. 유사 기간의 10/20/60일 선행 수익률 분포 → 승률·기대수익·손실위험 계산
  5. 복합 진입 점수 산출 → 신호 분류 (enter / wait / avoid)

공개 API:
  analyze_entry(ticker, ...)         → EntryScore 단일 종목
  analyze_all_entries()              → list[EntryScore] 전체 포트폴리오
  format_entry_report(scores)        → 텔레그램 텍스트
  check_alert_signals(prev_state)    → 신규 알림 대상 list[EntryScore]
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ── 대상 종목 ─────────────────────────────────────────────────────────────────

PORTFOLIO_STOCKS    = ["MSFT", "NVDA", "GOOGL", "ORCL", "SAP", "UNH", "SPMO", "QQQI"]
LEVERAGE_ETFS       = ["QLD", "TQQQ", "UPRO"]
LEVERAGE_UNDERLYING = {"QLD": "QQQ", "TQQQ": "QQQ", "UPRO": "SPY"}

ALERT_STATE_PATH = Path(os.path.expanduser("~/.cache/entry_alert_state.json"))
ALERT_COOLDOWN_H = 6      # 동일 종목 재알림 최소 간격 (시간)
ALERT_SCORE_MIN  = 0.60   # 알림 발송 최소 점수

# ── 진입 점수 파라미터 (backtest/entry_calibration.py가 walk-forward로 재추정) ──
SCORE_PARAMS_PATH = Path(os.path.expanduser("~/reports/ml-cache/entry_score_params.json"))
DEFAULT_SCORE_PARAMS = {
    "w_win": 0.40, "w_rr": 0.30, "w_rsi": 0.15, "w_dd": 0.15,
    "enter_threshold": 0.62, "wait_threshold": 0.40,
}
_score_params_cache: dict | None = None
_score_params_ts: float = 0.0
_SCORE_PARAMS_TTL = 6 * 3600   # 상시 실행 봇이 월간 재캘리브레이션을 재시작 없이 반영


def get_score_params() -> dict:
    """캘리브레이션된 점수 파라미터 로드 (없으면 기본값, 6시간 TTL 캐시)."""
    global _score_params_cache, _score_params_ts
    import time as _time
    if _score_params_cache is not None and _time.time() - _score_params_ts < _SCORE_PARAMS_TTL:
        return _score_params_cache
    params = dict(DEFAULT_SCORE_PARAMS)
    try:
        if SCORE_PARAMS_PATH.exists():
            loaded = json.loads(SCORE_PARAMS_PATH.read_text())
            params.update({k: float(v) for k, v in loaded.items() if k in params})
            logger.info("캘리브레이션 점수 파라미터 적용: %s", params)
    except Exception as e:
        logger.warning("점수 파라미터 로드 실패 — 기본값 사용: %s", e)
    _score_params_cache = params
    _score_params_ts = _time.time()
    return params

# ── 한국 주식 메타데이터 ──────────────────────────────────────────────────────
# {ticker: (한글명, 영문명, 섹터)}
KR_META: dict[str, tuple[str, str, str]] = {
    "005930.KS": ("삼성전자",        "Samsung Electronics",  "반도체"),
    "000660.KS": ("SK하이닉스",      "SK Hynix",             "반도체"),
    "373220.KS": ("LG에너지솔루션",  "LG Energy Solution",   "2차전지"),
    "207940.KS": ("삼성바이오로직스", "Samsung Biologics",    "바이오"),
    "005380.KS": ("현대차",          "Hyundai Motor",        "자동차"),
    "005490.KS": ("포스코홀딩스",    "POSCO Holdings",       "철강"),
    "035420.KS": ("NAVER",           "NAVER",                "IT"),
    "035720.KS": ("카카오",          "Kakao",                "IT"),
    "000270.KS": ("기아",            "Kia",                  "자동차"),
    "006400.KS": ("삼성SDI",         "Samsung SDI",          "2차전지"),
}

def is_kr_stock(ticker: str) -> bool:
    return ticker.endswith(".KS") or ticker.endswith(".KQ")

# ── 데이터 컨테이너 ───────────────────────────────────────────────────────────

@dataclass
class EntryScore:
    ticker:      str
    category:    str          # "leverage" | "stock"
    underlying:  str          # 기초지수 (QQQ/SPY) 또는 자기자신

    # ── 현재 상태 ──
    current_drawdown: float   # 52주 고점 대비 낙폭 (음수)
    current_rsi:      float
    current_vix:      float
    current_mom_20d:  float
    current_mom_60d:  float
    current_price:    float

    # ── 유사 기간 분석 ──
    n_similar:        int
    win_prob_20d:     float   # 20일 후 양수 수익 확률
    win_prob_60d:     float   # 60일 후 양수 수익 확률
    expected_ret_20d: float   # 20일 중앙값 기대수익
    expected_ret_60d: float   # 60일 중앙값 기대수익
    downside_p25_20d: float   # 20일 25분위 (하방 위험)
    upside_p75_20d:   float   # 20일 75분위 (상방 기대)

    # ── 신호 ──
    score:    float            # 0~1 진입 점수
    signal:   str              # "enter" / "wait" / "avoid"
    reasons:  list[str] = field(default_factory=list)
    timestamp: str = ""

    # ── 한국 주식 전용 ──
    currency:      str = "USD"   # "KRW" for Korean stocks
    display_name:  str = ""      # 한글명 (한국 주식) 또는 티커


# ── 피처 계산 ─────────────────────────────────────────────────────────────────

def _compute_ticker_features(price: pd.Series, vix: pd.Series) -> pd.DataFrame:
    """단일 종목 특징 벡터 시계열 계산."""
    feat = pd.DataFrame(index=price.index)

    # 낙폭 (52주 고점 대비)
    high_52w = price.rolling(252, min_periods=60).max()
    feat["drawdown"] = (price / high_52w - 1).fillna(0)

    # RSI(14)
    delta = price.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    feat["rsi"] = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50)

    # 모멘텀
    feat["mom_20d"] = price.pct_change(20).fillna(0)
    feat["mom_60d"] = price.pct_change(60).fillna(0)

    # 변동성 (20일)
    feat["vol_20d"] = price.pct_change().rolling(20).std().fillna(0.02)

    # VIX (시장 공포)
    vix_r = vix.reindex(price.index).ffill()
    feat["vix"] = vix_r.fillna(20)

    # BB %B (20일)
    ma20  = price.rolling(20).mean()
    std20 = price.rolling(20).std().replace(0, np.nan)
    feat["bb_pct_b"] = ((price - (ma20 - 2 * std20)) / (4 * std20)).clip(0, 1).fillna(0.5)

    return feat.dropna(how="any")


def _vix_regime_bounds(vix_value: float) -> tuple[float, float]:
    """VIX 레짐 경계: 저(<18) / 중(18~28) / 고(>28)."""
    if vix_value < 18:
        return (0.0, 18.0)
    if vix_value <= 28:
        return (18.0, 28.0)
    return (28.0, float("inf"))


def _find_similar(
    current: pd.Series,
    history: pd.DataFrame,
    n: int = 30,
    lookback: int = 10,
) -> tuple[pd.Index, np.ndarray]:
    """현재 특징 벡터와 유사한 과거 기간 탐색 (정규화 유클리드 거리).

    lookback: 최근 n일은 제외 (최신 데이터 리크 방지).
    VIX 레짐 조건부: 같은 변동성 레짐 안에서만 탐색 (표본 60건 미만이면 전체로 폴백)
    — "차트 모양은 비슷하지만 시장 환경이 다른" 기간 혼입 방지.

    Returns:
        (유사 기간 인덱스, 거리 역수 커널 가중치 — 합 1로 정규화)
    """
    hist = history.iloc[:-lookback] if len(history) > lookback else history
    if hist.empty:
        return pd.Index([]), np.array([])

    if "vix" in hist.columns and np.isfinite(current.get("vix", np.nan)):
        lo, hi = _vix_regime_bounds(float(current["vix"]))
        regime = hist[(hist["vix"] >= lo) & (hist["vix"] < hi)]
        if len(regime) >= 60:
            hist = regime

    # z-score 정규화
    mu  = hist.mean()
    std = hist.std().replace(0, 1)
    h_n = (hist - mu) / std
    c_n = (current - mu) / std

    dists = np.sqrt(((h_n - c_n) ** 2).sum(axis=1))
    top   = dists.nsmallest(n)
    w     = 1.0 / (1.0 + top.to_numpy())
    w     = w / w.sum() if w.sum() > 0 else np.full(len(w), 1.0 / max(len(w), 1))
    return top.index, w


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """가중 분위수 (선형 보간)."""
    order = np.argsort(values)
    v, w  = values[order], weights[order]
    cw    = np.cumsum(w) - 0.5 * w
    cw    = cw / w.sum()
    return float(np.interp(q, cw, v))


def compute_entry_score(
    win_20: float,
    exp_20: float,
    p25_20: float,
    rsi_v:  float,
    dd_v:   float,
    category: str = "stock",
    params: dict | None = None,
) -> tuple[float, float]:
    """복합 진입 점수 (0~1)와 손익비 계산 — analyze_entry와 캘리브레이션 공용.

    가중치는 get_score_params()에서 로드 (walk-forward 캘리브레이션 결과 반영).
    """
    p = params or get_score_params()

    # 1. 승률
    win_s = max(0.0, min(1.0, (win_20 - 0.3) / 0.5))

    # 2. 손익비
    rr   = abs(exp_20 / p25_20) if p25_20 < 0 and np.isfinite(p25_20) else 1.0
    rr_s = max(0.0, min(1.0, (rr - 0.5) / 2.5))

    # 3. RSI 과매도 보너스
    rsi_s = max(0.0, min(1.0, (55 - rsi_v) / 35))

    # 4. 낙폭 위치 — 많이 빠질수록 유리 (단, 과도한 낙폭 제외)
    if category == "leverage":
        dd_s = max(0.0, min(1.0, (-dd_v - 0.03) / 0.18)) if dd_v < -0.03 else 0.0
    else:
        dd_s = max(0.0, min(1.0, (-dd_v - 0.05) / 0.25)) if dd_v < -0.05 else 0.0

    score = win_s * p["w_win"] + rr_s * p["w_rr"] + rsi_s * p["w_rsi"] + dd_s * p["w_dd"]
    return score, rr


# ── 단일 종목 분석 ────────────────────────────────────────────────────────────

def analyze_entry(
    ticker:    str,
    price_df:  pd.DataFrame,
    vix:       pd.Series,
    n_similar: int = 30,
    category:  str = "stock",
    underlying_price: pd.Series | None = None,
) -> Optional[EntryScore]:
    """단일 종목 진입 점수 계산.

    레버리지 ETF: underlying_price(QQQ/SPY)로 낙폭·신호 계산.
    개별주:       자기 자신 가격으로 낙폭·신호 계산.
    """
    if price_df is None or len(price_df) < 120:
        return None

    price = price_df["Close"].dropna()
    if len(price) < 120:
        return None

    # 신호 계산 기준 가격 (레버리지는 기초지수 기준)
    signal_price = underlying_price if underlying_price is not None else price

    try:
        feat = _compute_ticker_features(signal_price, vix)
        if feat.empty or len(feat) < 60:
            return None

        # 현재 특징
        cur = feat.iloc[-1]

        # 유사 기간 탐색 (거리 커널 가중)
        sim_idx, sim_w = _find_similar(cur, feat, n=n_similar)
        if len(sim_idx) == 0:
            return None
        w_all = pd.Series(sim_w, index=sim_idx)

        # 선행 수익률 분포 (레버리지 ETF는 실제 ETF 가격 기준)
        fwd_price = price.reindex(feat.index)
        fwd_10d   = fwd_price.pct_change(10).shift(-10)
        fwd_20d   = fwd_price.pct_change(20).shift(-20)
        fwd_60d   = fwd_price.pct_change(60).shift(-60)

        r20 = fwd_20d.reindex(sim_idx)
        r60 = fwd_60d.reindex(sim_idx)
        rets_20, w20 = r20[r20.notna()], w_all[r20.notna()]
        rets_60, w60 = r60[r60.notna()], w_all[r60.notna()]

        if len(rets_20) < 3:
            return None

        # 거리 가중 통계: 더 유사한 기간일수록 분포 추정에 큰 영향
        win_20  = float(np.average(rets_20 > 0, weights=w20))
        win_60  = float(np.average(rets_60 > 0, weights=w60)) if len(rets_60) >= 3 else win_20
        exp_20  = _weighted_quantile(rets_20.to_numpy(), w20.to_numpy(), 0.5)
        exp_60  = _weighted_quantile(rets_60.to_numpy(), w60.to_numpy(), 0.5) if len(rets_60) >= 3 else exp_20
        p25_20  = _weighted_quantile(rets_20.to_numpy(), w20.to_numpy(), 0.25)
        p75_20  = _weighted_quantile(rets_20.to_numpy(), w20.to_numpy(), 0.75)

        # 현재 시장 상태
        high_52w   = price.rolling(252, min_periods=60).max().iloc[-1]
        cur_dd     = float(price.iloc[-1] / high_52w - 1) if high_52w > 0 else 0.0
        cur_price  = float(price.iloc[-1])
        und_label  = LEVERAGE_UNDERLYING.get(ticker, ticker)

        # ── 진입 점수 계산 ──────────────────────────────────────────────────
        rsi_v = float(cur["rsi"])
        dd_v  = float(cur["drawdown"])
        score, rr = compute_entry_score(win_20, exp_20, p25_20, rsi_v, dd_v, category)

        reasons: list[str] = []
        if win_20 >= 0.65:
            reasons.append(f"승률 {win_20*100:.0f}% (강세)")
        elif win_20 >= 0.55:
            reasons.append(f"승률 {win_20*100:.0f}% (보통)")
        else:
            reasons.append(f"승률 {win_20*100:.0f}% (약세)")
        if rr >= 2.0:
            reasons.append(f"손익비 {rr:.1f}× (양호)")
        elif rr >= 1.2:
            reasons.append(f"손익비 {rr:.1f}× (보통)")
        else:
            reasons.append(f"손익비 {rr:.1f}× (불리)")
        if rsi_v < 35:
            reasons.append(f"RSI {rsi_v:.0f} (과매도)")
        elif rsi_v > 65:
            reasons.append(f"RSI {rsi_v:.0f} (과매수)")

        # 신호 분류
        sp = get_score_params()
        if score >= sp["enter_threshold"]:
            signal = "enter"
        elif score >= sp["wait_threshold"]:
            signal = "wait"
        else:
            signal = "avoid"

        # 한국 주식 메타
        currency     = "KRW" if is_kr_stock(ticker) else "USD"
        kr_info      = KR_META.get(ticker)
        display_name = kr_info[0] if kr_info else ticker

        return EntryScore(
            ticker           = ticker,
            category         = category,
            underlying       = und_label,
            current_drawdown = cur_dd,
            current_rsi      = rsi_v,
            current_vix      = float(cur["vix"]),
            current_mom_20d  = float(cur["mom_20d"]),
            current_mom_60d  = float(cur["mom_60d"]),
            current_price    = cur_price,
            n_similar        = len(rets_20),
            win_prob_20d     = round(win_20, 3),
            win_prob_60d     = round(win_60, 3),
            expected_ret_20d = round(exp_20, 4),
            expected_ret_60d = round(exp_60, 4),
            downside_p25_20d = round(p25_20, 4),
            upside_p75_20d   = round(p75_20, 4),
            score            = round(score, 3),
            signal           = signal,
            reasons          = reasons,
            timestamp        = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
            currency         = currency,
            display_name     = display_name,
        )

    except Exception as e:
        logger.warning("analyze_entry(%s) 실패: %s", ticker, e)
        return None


# ── 전체 분석 ─────────────────────────────────────────────────────────────────

def analyze_all_entries(
    days:        int = 756,
    n_similar:   int = 30,
    universe:    str = "portfolio",   # portfolio | us_top50 | kr_top10 | watch | leverage
    extra_tickers: list[str] | None = None,
) -> list[EntryScore]:
    """포트폴리오·확장 유니버스 진입 분석.

    universe:
      portfolio  — 보유 종목 + 레버리지 ETF (기본)
      us_top50   — 미국 시총 상위 50
      kr_top10   — 한국 시총 상위 10
      leverage   — 레버리지 ETF만
      watch      — 전체 감시 (portfolio + us_top50 + kr_top10 + leverage)
    """
    from ml.data_pipeline import fetch_prices, US_TOP50, KR_TOP10

    # 대상 티커 결정
    if universe == "portfolio":
        stock_tickers = list(PORTFOLIO_STOCKS)
        lev_tickers   = list(LEVERAGE_ETFS)
    elif universe == "us_top50":
        stock_tickers = list(US_TOP50)
        lev_tickers   = []
    elif universe == "kr_top10":
        stock_tickers = list(KR_TOP10)
        lev_tickers   = []
    elif universe == "leverage":
        stock_tickers = []
        lev_tickers   = list(LEVERAGE_ETFS)
    elif universe == "watch":
        stock_tickers = list(dict.fromkeys(
            list(PORTFOLIO_STOCKS) + list(US_TOP50) + list(KR_TOP10)
        ))
        lev_tickers   = list(LEVERAGE_ETFS)
    else:
        stock_tickers = list(PORTFOLIO_STOCKS)
        lev_tickers   = list(LEVERAGE_ETFS)

    if extra_tickers:
        stock_tickers = list(dict.fromkeys(stock_tickers + extra_tickers))

    all_tickers = list(set(stock_tickers + lev_tickers + ["QQQ", "SPY", "^VIX"]))
    logger.info("진입 분석 가격 로드: %d종목 (universe=%s)", len(all_tickers), universe)
    prices = fetch_prices(all_tickers, days=days)

    vix_df = prices.get("^VIX", pd.DataFrame())
    vix_s  = vix_df.get("Close") if hasattr(vix_df, "get") else None
    if vix_s is None or len(vix_s) == 0:
        vix_s = pd.Series(20.0, index=pd.date_range("2020-01-01", periods=1))

    qqq = prices.get("QQQ", pd.DataFrame())
    qqq = qqq.get("Close") if hasattr(qqq, "get") else None
    spy = prices.get("SPY", pd.DataFrame())
    spy = spy.get("Close") if hasattr(spy, "get") else None

    scores: list[EntryScore] = []

    # 레버리지 ETF
    for ticker in lev_tickers:
        df = prices.get(ticker)
        if df is None:
            continue
        und      = LEVERAGE_UNDERLYING.get(ticker, "QQQ")
        und_px   = qqq if und == "QQQ" else spy
        s = analyze_entry(ticker, df, vix_s, n_similar=n_similar,
                          category="leverage", underlying_price=und_px)
        if s:
            scores.append(s)

    # 개별주 (미국 + 한국)
    for ticker in stock_tickers:
        df = prices.get(ticker)
        if df is None:
            continue
        s = analyze_entry(ticker, df, vix_s, n_similar=n_similar, category="stock")
        if s:
            scores.append(s)

    logger.info("진입 분석 완료: %d종목", len(scores))
    return scores


# ── 텔레그램 포맷 ─────────────────────────────────────────────────────────────

_SIGNAL_EMOJI  = {"enter": "🟢", "wait": "🟡", "avoid": "🔴"}
_SIGNAL_LABEL  = {"enter": "진입 유리", "wait": "대기", "avoid": "진입 불리"}
_TICKER_NAME: dict[str, str] = {
    # 포트폴리오
    "MSFT": "Microsoft", "NVDA": "NVIDIA",   "GOOGL": "Alphabet",
    "ORCL": "Oracle",    "SAP":  "SAP",      "UNH":   "UnitedHealth",
    "SPMO": "S&P Momentum", "QQQI": "QQQI",
    # 레버리지
    "QLD":  "QLD(2×QQQ)",  "TQQQ": "TQQQ(3×QQQ)", "UPRO": "UPRO(3×SPY)",
    # 미국 주요 종목
    "AAPL": "Apple",    "AMZN": "Amazon",   "META": "Meta",    "TSLA": "Tesla",
    "AVGO": "Broadcom", "TSM":  "TSMC",     "QCOM": "Qualcomm","AMD":  "AMD",
    "LLY":  "Eli Lilly","JPM":  "JPMorgan", "V":    "Visa",    "MA":   "Mastercard",
    "WMT":  "Walmart",  "COST": "Costco",   "HD":   "Home Depot",
    "XOM":  "Exxon",    "CVX":  "Chevron",  "NFLX": "Netflix", "ADBE": "Adobe",
    "BRK-B":"Berkshire","GS":   "Goldman",  "GE":   "GE",      "LIN":  "Linde",
}


def _fmt_pct(v: float) -> str:
    return f"{v*100:+.1f}%"


def _price_str(s: EntryScore) -> str:
    """현재가 표시 (KRW는 원화 형식)."""
    if s.currency == "KRW":
        return f"₩{s.current_price:,.0f}"
    return f"${s.current_price:.2f}"


def _fmt_price(value: float, currency: str) -> str:
    if currency == "KRW":
        return f"₩{value:,.0f}"
    return f"${value:.2f}"


def trade_level_values(s: EntryScore) -> tuple[float, float, float]:
    """유사기간 수익 분포 기반 (권장 매수 하단, 목표가, 손절가) 수치 산출.

    목표가  = 현재가 × (1 + 상방 P75), 최소 +2%
    손절가  = 현재가 × (1 + 하방 P25), 최소 -3% (분포가 양수여도 손절선 확보)
    권장 매수 = 하방 P25의 절반 되돌림 ~ 현재가 (분할 매수 구간)
    """
    p = s.current_price
    target = p * (1 + max(s.upside_p75_20d, 0.02))
    stop   = p * (1 + min(s.downside_p25_20d, -0.03))
    buy_lo = p * (1 + min(s.downside_p25_20d, 0) / 2)
    return buy_lo, target, stop


def _trade_levels(s: EntryScore) -> tuple[str, str, str]:
    """trade_level_values 표시용 포맷 (통화별)."""
    buy_lo, target, stop = trade_level_values(s)
    cur = s.currency
    buy_str = f"{_fmt_price(buy_lo, cur)} ~ {_fmt_price(s.current_price, cur)}"
    return buy_str, _fmt_price(target, cur), _fmt_price(stop, cur)


def _render_score(s: EntryScore) -> list[str]:
    """단일 종목 분석 결과 렌더링."""
    emoji  = _SIGNAL_EMOJI[s.signal]
    label  = _SIGNAL_LABEL[s.signal]
    # 표시명: 한국주식은 한글명, 미국은 영문명
    if s.currency == "KRW":
        kr_info = KR_META.get(s.ticker)
        name    = f"{kr_info[0]}({kr_info[1]})" if kr_info else s.ticker
    else:
        name = _TICKER_NAME.get(s.ticker, s.ticker)

    rr     = abs(s.expected_ret_20d / s.downside_p25_20d) if s.downside_p25_20d < 0 else 0
    rr_str = f"{rr:.1f}×" if rr > 0 else "—"
    price  = _price_str(s)

    out = [
        f"{emoji} {s.ticker}  {name}  [{label}]  점수:{s.score:.2f}",
        f"   현재가 {price}  낙폭 {_fmt_pct(s.current_drawdown)}  RSI {s.current_rsi:.0f}",
        f"   20d {_fmt_pct(s.current_mom_20d)}  VIX {s.current_vix:.1f}",
        f"   유사기간 {s.n_similar}건 | "
        f"승률 20d {s.win_prob_20d*100:.0f}% / 60d {s.win_prob_60d*100:.0f}%",
        f"   기대수익 {_fmt_pct(s.expected_ret_20d)} (20d) / {_fmt_pct(s.expected_ret_60d)} (60d)",
        f"   하방25% {_fmt_pct(s.downside_p25_20d)}  상방75% {_fmt_pct(s.upside_p75_20d)}  손익비 {rr_str}",
    ]
    buy_str, target_str, stop_str = _trade_levels(s)
    out.append(f"   매수 {buy_str}  목표 {target_str}  손절 {stop_str}")
    if s.reasons:
        out.append(f"   💡 {' · '.join(s.reasons[:2])}")
    return out


def format_entry_report(scores: list[EntryScore], title: str = "📊 진입 타점 분석") -> str:
    """진입 분석 전체 텔레그램 리포트 (유니버스 혼합 지원)."""
    if not scores:
        return "⚠️ 진입 분석 데이터 없음"

    ts  = scores[0].timestamp if scores else ""
    lev = [s for s in scores if s.category == "leverage"]
    kr  = [s for s in scores if s.category == "stock" and s.currency == "KRW"]
    us  = [s for s in scores if s.category == "stock" and s.currency == "USD"]

    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"({ts})",
        "",
    ]

    if lev:
        lines.append("[ 📈 레버리지 ETF ]")
        for s in sorted(lev, key=lambda x: -x.score):
            lines.extend(_render_score(s))
            lines.append("")

    if us:
        lines.append("[ 🇺🇸 미국주식 ]")
        for s in sorted(us, key=lambda x: -x.score):
            lines.extend(_render_score(s))
            lines.append("")

    if kr:
        lines.append("[ 🇰🇷 한국주식 ]")
        for s in sorted(kr, key=lambda x: -x.score):
            lines.extend(_render_score(s))
            lines.append("")

    # 요약 추천
    enters = [s for s in scores if s.signal == "enter"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    if enters:
        top3 = sorted(enters, key=lambda x: -x.score)[:5]
        names = ", ".join(s.ticker for s in top3)
        lines += [
            f"⚡ 진입 검토 ({len(enters)}건): {names}",
            "⚠️ 분할 매수 + 손절 설정 필수",
        ]
    else:
        lines.append("⏳ 현재 진입 유리한 종목 없음 — 추가 조정 대기")

    return "\n".join(lines)


def format_alert_message(s: EntryScore) -> str:
    """단일 종목 알림 메시지 (한국/미국/레버리지 공통)."""
    emoji = _SIGNAL_EMOJI[s.signal]
    if s.currency == "KRW":
        kr_info = KR_META.get(s.ticker)
        name    = f"{kr_info[0]} ({kr_info[1]})" if kr_info else s.ticker
    else:
        name = _TICKER_NAME.get(s.ticker, s.ticker)
    rr    = abs(s.expected_ret_20d / s.downside_p25_20d) if s.downside_p25_20d < 0 else 0
    buy_str, target_str, stop_str = _trade_levels(s)

    lines = [
        f"🔔 진입 기회 감지 — {s.ticker}",
        f"{emoji} {name}",
        f"",
        f"[ 현재 상태 ]",
        f"  낙폭:  {_fmt_pct(s.current_drawdown)}  RSI: {s.current_rsi:.0f}",
        f"  20d:   {_fmt_pct(s.current_mom_20d)}    VIX: {s.current_vix:.1f}",
        f"",
        f"[ 유사 과거 {s.n_similar}건 분석 ]",
        f"  승률:     20일 {s.win_prob_20d*100:.0f}% / 60일 {s.win_prob_60d*100:.0f}%",
        f"  기대수익: {_fmt_pct(s.expected_ret_20d)} (20d) / {_fmt_pct(s.expected_ret_60d)} (60d)",
        f"  하방위험: {_fmt_pct(s.downside_p25_20d)} (P25)",
        f"  손익비:   {rr:.1f}×" if rr > 0 else "  손익비:   —",
        f"",
        f"[ 매매 가이드 ]",
        f"  현재가:     {_price_str(s)}",
        f"  권장 매수:  {buy_str} (분할)",
        f"  목표가:     {target_str} (20d 상방 P75)",
        f"  손절가:     {stop_str} (20d 하방 P25)",
        f"",
        f"진입 점수: {s.score:.2f} / 1.00",
        f"💡 {' · '.join(s.reasons[:3])}",
        f"",
        f"⚠️ 분할 매수 / 손절 필수 — 과거 분포 기반, 미래 보장 없음",
    ]
    return "\n".join(lines)


# ── 알림 상태 관리 ────────────────────────────────────────────────────────────

def _load_alert_state() -> dict:
    if ALERT_STATE_PATH.exists():
        try:
            return json.loads(ALERT_STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_alert_state(state: dict) -> None:
    ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.rename(ALERT_STATE_PATH)


def check_alert_signals(scores: list[EntryScore]) -> list[EntryScore]:
    """알림 발송 대상 필터링.

    조건:
      1. signal == "enter" and score >= ALERT_SCORE_MIN
      2. 이전 신호가 "enter"가 아니었거나 (새로 진입 신호)
      3. 마지막 알림 이후 ALERT_COOLDOWN_H 이상 경과
    """
    state   = _load_alert_state()
    now     = datetime.now(KST)
    to_alert: list[EntryScore] = []

    for s in scores:
        if s.signal != "enter" or s.score < ALERT_SCORE_MIN:
            continue

        prev = state.get(s.ticker, {})
        last_alert_str = prev.get("last_alert", "")
        last_signal    = prev.get("last_signal", "")

        # 쿨다운 체크
        if last_alert_str:
            try:
                last_dt = datetime.fromisoformat(last_alert_str)
                if (now - last_dt).total_seconds() < ALERT_COOLDOWN_H * 3600:
                    continue   # 아직 쿨다운 중
            except Exception:
                pass

        # 신호 변화 체크 (wait/avoid → enter 전환 or 처음)
        if last_signal == "enter":
            continue   # 이미 enter 신호였으면 스킵

        to_alert.append(s)

    # 상태 업데이트 (모든 종목의 현재 신호 기록)
    for s in scores:
        entry = state.setdefault(s.ticker, {})
        if s.ticker in [a.ticker for a in to_alert]:
            entry["last_alert"]  = now.isoformat()
        entry["last_signal"] = s.signal

    _save_alert_state(state)
    return to_alert


def reset_alert_state(ticker: str | None = None) -> None:
    """알림 상태 초기화 (특정 종목 또는 전체)."""
    state = _load_alert_state()
    if ticker:
        state.pop(ticker, None)
    else:
        state = {}
    _save_alert_state(state)
