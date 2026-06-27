"""lib/price_utils.py — 공유 yfinance 종가 fetch + 실적전 모멘텀/변동성 윈도 (ml 중복 제거, 행위 보존).

earnings_predictor·earnings_move_predictor 의 동일한 `_price_feats`/`_closes`, earnings_reaction 의
`_closes` 를 통합. 행위(로직·반올림·결측 처리)는 원본과 동일.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fetch_closes(ticker: str, *, period: str = "6y"):
    """yfinance 일별 종가 Series(tz-naive). 실패/빈 데이터 시 None."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if h is None or len(h) == 0:
            return None
        c = h["Close"].dropna()
        if getattr(c.index, "tz", None) is not None:
            c.index = c.index.tz_localize(None)
        return c
    except Exception:
        return None


def window_feats(closes, ref_date, *, lookback: int = 21):
    """ref_date 직전 lookback 거래일 (모멘텀, 변동성). closes=종가 Series. 부족/실패 시 (None, None).

    원본 _price_feats 와 동일: 21봉 윈도, mom=마지막/처음-1, vol=일수익 std, 소수 4자리.
    """
    try:
        import pandas as pd
        d = pd.Timestamp(ref_date)
        pre = closes[closes.index < d]
        if len(pre) < lookback:
            return None, None
        w = pre.iloc[-lookback:]
        mom = float(w.iloc[-1] / w.iloc[0] - 1.0)
        rets = w.pct_change().dropna()
        vol = float(rets.std()) if len(rets) > 1 else None
        return round(mom, 4), (round(vol, 4) if vol is not None else None)
    except Exception:
        return None, None
