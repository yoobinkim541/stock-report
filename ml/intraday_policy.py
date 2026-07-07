#!/usr/bin/env python3
"""intraday_policy.py — 단기(1m/5m) 모의 트레이딩 선택 정책 (kr_intraday·us_intraday).

축 가중 + 진입/청산 파라미터를 Policy(클램프·주간 학습 갱신)로 관리.
score() 는 kr_policy.score 와 동일 규약 — 결측 축(None) 은 제외하고 사용 가중 재정규화.

축 (ml/intraday_axes 산출, 전부 [0,1]·롱 전용):
  orb      시가범위(15분) 돌파 + 거래량 확인
  vwap     VWAP 과매도 반전 / 리클레임
  volspike 시간대 정규화 거래량 z + 가격 임펄스
  ofi      호가 잔량 불균형 (KR 10단계 — US 는 1호가라 저가중)
  news     뉴스 이벤트 창(60분) 방향×강도
  ema/rsi/bb  기존 intraday_signal 지표 — 6티어상 무엣지 성향이라 저가중(학습이 0 으로
              보내면 수용). 초기 가중은 docs/intraday-mock-trading-design.md §1.1.
"""
from __future__ import annotations

from ml.adaptive import Policy

AXES = ("orb", "vwap", "volspike", "ofi", "news", "ema", "rsi", "bb")

_COMMON = {
    "theta_entry": 0.55,     # 진입 점수 문턱
    "theta_exit": 0.25,      # 신호 붕괴 청산 문턱
    "stop_atr_mult": 1.2,    # 손절 = 진입가 − mult×ATR(14,1m)
    "target_r": 2.0,         # 목표 = 진입가 + R×손절폭
    "timestop_min": 90,      # 무진전 타임스톱(분)
}

DEFAULTS = {
    "kr": {"w_orb": 0.20, "w_vwap": 0.15, "w_volspike": 0.20, "w_ofi": 0.15,
           "w_news": 0.20, "w_ema": 0.04, "w_rsi": 0.03, "w_bb": 0.03, **_COMMON},
    # US 는 실시간 1호가뿐 → ofi 저가중, 잔여를 이벤트·돌파축에 배분
    "us": {"w_orb": 0.22, "w_vwap": 0.17, "w_volspike": 0.22, "w_ofi": 0.05,
           "w_news": 0.22, "w_ema": 0.04, "w_rsi": 0.04, "w_bb": 0.04, **_COMMON},
}

BOUNDS = {**{f"w_{a}": (0.0, 0.5) for a in AXES},
          "theta_entry": (0.40, 0.75), "theta_exit": (0.10, 0.45),
          "stop_atr_mult": (0.8, 2.0), "target_r": (1.0, 3.0),
          "timestop_min": (30, 180)}


def _mk(market: str) -> str:
    m = (market or "kr").lower()
    return m if m in ("kr", "us") else "kr"


def get_policy(market: str = "kr") -> Policy:
    m = _mk(market)
    return Policy(f"{m}_intraday", DEFAULTS[m], BOUNDS)


def load_params(market: str = "kr") -> dict:
    return get_policy(market).load()


def score(features: dict, params: dict | None = None, market: str = "kr") -> float:
    """정책 점수(0~1) — 축 가중평균. 결측 축(None/부재)은 제외·사용 가중 재정규화."""
    p = params or load_params(market)
    weights = {k[2:]: float(v) for k, v in p.items() if k.startswith("w_")}
    used = {name: float(features[name]) for name in weights
            if features.get(name) is not None}
    total_w = sum(weights[n] for n in used)
    if not used or total_w <= 0:
        return 0.0
    return round(sum(used[n] * weights[n] for n in used) / total_w, 6)
