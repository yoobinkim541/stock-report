"""ml/technical_rating.py — TradingView식 기술 등급 + 피벗 포인트

가격 데이터(OHLCV)만으로 계산하는 참고 지표:
  compute_technical_rating(df) — MA 13개 + 오실레이터 8개의 매수/매도/중립 합산 게이지
  pivot_points(df)             — 전월 H/L/C 기준 클래식·피보나치 피벗 (지지/저항 참고선)
  build_reference_brief(...)   — 텔레그램용 참고지표 블록 (기술등급+피벗+옵션)

등급 규칙 (TradingView 근사):
  score = (매수 - 매도) / 전체
  score ≥ 0.5 강한 매수 | ≥ 0.1 매수 | > -0.1 중립 | > -0.5 매도 | 이하 강한 매도
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RATING_LABELS = [
    (0.5, "🟢 강한 매수"),
    (0.1, "🟢 매수"),
    (-0.1, "⚪ 중립"),
    (-0.5, "🔴 매도"),
    (-999, "🔴 강한 매도"),
]


def _label(score: float) -> str:
    for th, lab in RATING_LABELS:
        if score >= th:
            return lab
    return "⚪ 중립"


def _ma_signals(close: pd.Series, volume: pd.Series | None) -> list[int]:
    """이동평균 신호: 종가 > MA → +1(매수), < → -1(매도)."""
    px = float(close.iloc[-1])
    sigs = []
    for n in (10, 20, 30, 50, 100, 200):
        for ma in (close.rolling(n).mean(), close.ewm(span=n, adjust=False).mean()):
            v = float(ma.iloc[-1])
            if np.isfinite(v):
                sigs.append(1 if px > v else (-1 if px < v else 0))
    # VWMA(20)
    if volume is not None and volume.notna().any():
        vwma = (close * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
        v = float(vwma.iloc[-1])
        if np.isfinite(v):
            sigs.append(1 if px > v else (-1 if px < v else 0))
    return sigs


def _osc_signals(df: pd.DataFrame) -> list[int]:
    """오실레이터 신호 (TradingView 규칙 근사)."""
    close, high, low = df["Close"], df["High"], df["Low"]
    sigs = []

    # RSI(14): <30 상승전환 매수 / >70 하락전환 매도
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    r, r1 = float(rsi.iloc[-1]), float(rsi.iloc[-2])
    sigs.append(1 if (r < 30 and r > r1) else (-1 if (r > 70 and r < r1) else 0))

    # Stochastic %K(14,3): <20 & K>D 매수 / >80 & K<D 매도
    ll, hh = low.rolling(14).min(), high.rolling(14).max()
    k = (100 * (close - ll) / (hh - ll).replace(0, np.nan)).rolling(3).mean()
    d = k.rolling(3).mean()
    kv, dv = float(k.iloc[-1]), float(d.iloc[-1])
    sigs.append(1 if (kv < 20 and kv > dv) else (-1 if (kv > 80 and kv < dv) else 0))

    # CCI(20): <-100 상승전환 매수 / >100 하락전환 매도
    tp  = (high + low + close) / 3
    sma = tp.rolling(20).mean()
    mad = (tp - sma).abs().rolling(20).mean()
    cci = (tp - sma) / (0.015 * mad.replace(0, np.nan))
    c, c1 = float(cci.iloc[-1]), float(cci.iloc[-2])
    sigs.append(1 if (c < -100 and c > c1) else (-1 if (c > 100 and c < c1) else 0))

    # Momentum(10): 상승 매수 / 하락 매도
    mom = close.diff(10)
    sigs.append(1 if float(mom.iloc[-1]) > float(mom.iloc[-2]) else -1)

    # MACD(12,26,9): MACD > signal 매수
    macd   = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    sigs.append(1 if float(macd.iloc[-1]) > float(signal.iloc[-1]) else -1)

    # Williams %R(14): <-80 매수 / >-20 매도
    wr = -100 * (hh - close) / (hh - ll).replace(0, np.nan)
    w = float(wr.iloc[-1])
    sigs.append(1 if w < -80 else (-1 if w > -20 else 0))

    # Awesome Oscillator: 중앙선 상회+상승 매수
    mp = (high + low) / 2
    ao = mp.rolling(5).mean() - mp.rolling(34).mean()
    a, a1 = float(ao.iloc[-1]), float(ao.iloc[-2])
    sigs.append(1 if (a > 0 and a > a1) else (-1 if (a < 0 and a < a1) else 0))

    # Ultimate Oscillator(7,14,28): >70 매수 / <30 매도
    prev_c = close.shift(1)
    bp = close - pd.concat([low, prev_c], axis=1).min(axis=1)
    tr = pd.concat([high, prev_c], axis=1).max(axis=1) - pd.concat([low, prev_c], axis=1).min(axis=1)
    avg = lambda n: bp.rolling(n).sum() / tr.rolling(n).sum().replace(0, np.nan)
    uo  = 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7
    u = float(uo.iloc[-1])
    sigs.append(1 if u > 70 else (-1 if u < 30 else 0))

    return [s for s in sigs if np.isfinite(s)]


def compute_technical_rating(df: pd.DataFrame) -> dict | None:
    """일봉 OHLCV → MA/오실레이터/종합 등급."""
    if df is None or len(df) < 210:
        return None
    try:
        ma  = _ma_signals(df["Close"].dropna(), df.get("Volume"))
        osc = _osc_signals(df)
        def _pack(sigs: list[int]) -> dict:
            buy, sell = sum(1 for s in sigs if s > 0), sum(1 for s in sigs if s < 0)
            score = (buy - sell) / len(sigs) if sigs else 0.0
            return {"buy": buy, "sell": sell, "neutral": len(sigs) - buy - sell,
                    "score": round(score, 3), "rating": _label(score)}
        ma_p, osc_p = _pack(ma), _pack(osc)
        total = ma + osc
        return {"ma": ma_p, "osc": osc_p, "summary": _pack(total)}
    except Exception as e:
        logger.warning("technical rating 실패: %s", e)
        return None


def pivot_points(df: pd.DataFrame, method: str = "classic") -> dict | None:
    """전월 H/L/C 기준 피벗 포인트 (월 단위 지지/저항 참고선)."""
    if df is None or len(df) < 40:
        return None
    try:
        monthly = df.resample("ME").agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(monthly) < 2:
            return None
        h, l, c = (float(monthly.iloc[-2][k]) for k in ("High", "Low", "Close"))
        p = (h + l + c) / 3
        if method == "fibonacci":
            r = {"R3": p + (h - l), "R2": p + 0.618 * (h - l), "R1": p + 0.382 * (h - l),
                 "P": p,
                 "S1": p - 0.382 * (h - l), "S2": p - 0.618 * (h - l), "S3": p - (h - l)}
        else:
            r = {"R3": h + 2 * (p - l), "R2": p + (h - l), "R1": 2 * p - l,
                 "P": p,
                 "S1": 2 * p - h, "S2": p - (h - l), "S3": l - 2 * (h - p)}
        return {k: round(v, 2) for k, v in r.items()}
    except Exception as e:
        logger.warning("pivot 계산 실패: %s", e)
        return None


def _fmt_level(ticker: str, value: float) -> str:
    if ticker.endswith((".KS", ".KQ")):
        return f"₩{value:,.0f}"
    return f"${value:,.2f}"


def _latest_close(df: pd.DataFrame | None) -> float | None:
    if df is None or "Close" not in df:
        return None
    close = df["Close"].dropna()
    if close.empty:
        return None
    try:
        return float(close.iloc[-1])
    except Exception:
        return None


def _reference_interpretation(
    ticker: str,
    rating: dict | None,
    piv: dict | None,
    current_price: float | None,
) -> str:
    notes: list[str] = []
    if rating:
        label = str(rating["summary"].get("rating", ""))
        if "매도" in label:
            notes.append("기술 추세는 아직 약세라 통계 신호와 충돌")
        elif "매수" in label:
            notes.append("기술 추세는 통계 신호와 같은 방향")
        else:
            notes.append("기술 추세는 중립이라 가격 확인 필요")

    if piv and current_price is not None:
        p, s1, r1 = float(piv["P"]), float(piv["S1"]), float(piv["R1"])
        if current_price < s1:
            notes.append(f"S1({_fmt_level(ticker, s1)}) 아래라 변동성 확대 구간")
        elif current_price < p:
            notes.append(f"월 피벗({_fmt_level(ticker, p)}) 회복 전까지 반등 신뢰도 낮음")
        elif current_price > r1:
            notes.append(f"R1({_fmt_level(ticker, r1)}) 위라 단기 과열 여부 확인")
        else:
            notes.append("월 피벗 위, R1 아래의 중립 가격대")

    return " · ".join(notes[:2])


def build_reference_brief(ticker: str, df: pd.DataFrame | None = None,
                          include_options: bool = True) -> str:
    """텔레그램용 참고지표 블록 — 기술등급 + 월간 피벗 + 옵션 지표."""
    if df is None:
        try:
            from ml.data_pipeline import fetch_prices
            df = fetch_prices([ticker], days=400).get(ticker)
        except Exception:
            df = None

    lines = ["", "[ 참고 지표 ]"]
    rating = compute_technical_rating(df) if df is not None else None
    if rating:
        s, m, o = rating["summary"], rating["ma"], rating["osc"]
        lines.append(
            f"  기술등급: {s['rating']} "
            f"(이평 {m['buy']}↑/{m['sell']}↓ · 오실 {o['buy']}↑/{o['sell']}↓)"
        )
    piv = pivot_points(df) if df is not None else None
    if piv:
        lines.append(
            f"  월 피벗:  P {_fmt_level(ticker, piv['P'])} | "
            f"S1 {_fmt_level(ticker, piv['S1'])} / R1 {_fmt_level(ticker, piv['R1'])}"
        )

    interp = _reference_interpretation(ticker, rating, piv, _latest_close(df))
    if interp:
        lines.append(f"  해석: {interp}")

    if include_options and not ticker.endswith((".KS", ".KQ")):
        try:
            from ml.options_features import fetch_option_metrics
            om = fetch_option_metrics(ticker)
            if om:
                pcr = f" · P/C {om['pcr_volume']:.2f}" if om.get("pcr_volume") is not None else ""
                lines.append(
                    f"  옵션({om['dte']}D): ATM IV {om['atm_iv']*100:.0f}%{pcr}"
                    f" · 30d 기대변동 ±{om['expected_move_pct']*100:.1f}%"
                )
                if om.get("skew_ratio") is not None:
                    lines.append(f"  풋/콜 스큐: {om['skew_ratio']:.2f}× (1↑ = 하방 헤지 수요 우세)")
        except Exception as e:
            logger.debug("옵션 지표 생략 (%s): %s", ticker, e)

    return "\n".join(lines) if len(lines) > 2 else ""
