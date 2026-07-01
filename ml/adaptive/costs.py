"""ml/adaptive/costs.py — 모의 거래비용 모델 (순수·env 튜너블).

회전율이 수익을 깎는지 정직하게 계기·평가·보상에 반영하기 위한 단일 진실원.
bps 기본값은 기존 백테스트 상수와 정합(backtest/kr_sideways_backtest.py KR_BUY_BPS=2·KR_SELL_BPS=20).
- KR: 수수료 ~0.015% + **증권거래세 매도 ~0.18%** → 매도가 비쌈(회전율 민감).
- US(KIS 해외): 수수료 ~0.07~0.25% + 환전 스프레드 → 보수적 편도 15bps.
env 는 호출 시점에 읽어 테스트 monkeypatch 가능.
"""
from __future__ import annotations

import os

_DEFAULTS = {
    "KR": ("KR_MOCK_BUY_BPS", 2.0, "KR_MOCK_SELL_BPS", 20.0),
    "US": ("US_MOCK_BUY_BPS", 15.0, "US_MOCK_SELL_BPS", 15.0),
}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _rates(market: str) -> tuple[float, float]:
    """(buy_bps, sell_bps) — env 우선, 미설정 시 기본. 미지 시장은 (0,0)."""
    d = _DEFAULTS.get((market or "").upper())
    if not d:
        return (0.0, 0.0)
    return (_f(d[0], d[1]), _f(d[2], d[3]))


def order_cost(notional, side: str, market: str = "KR") -> float:
    """단일 주문 거래비용(통화). notional=거래대금(qty×price), side='buy'|'sell'."""
    buy_bps, sell_bps = _rates(market)
    bps = buy_bps if side == "buy" else sell_bps
    try:
        return abs(float(notional or 0.0)) * bps / 1e4
    except (TypeError, ValueError):
        return 0.0


def round_trip_frac(market: str = "KR") -> float:
    """왕복(매수+매도) 비용 분수 — 보상 페널티용. 예: KR (2+20)/1e4 = 0.0022 (0.22%)."""
    buy_bps, sell_bps = _rates(market)
    return (buy_bps + sell_bps) / 1e4
