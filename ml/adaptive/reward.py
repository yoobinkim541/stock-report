"""
reward.py — 보상·MDD·★목적함수.

★ 목적함수(전 학습/채택/승격의 기준):
  1순위 = 지수 대비 아웃퍼폼(초과수익).  2순위(제약) = MDD ≤ 지수 MDD.

부호 규약: MDD 는 **양수 크기**(0.20 = -20% 낙폭). 작을수록 좋음.
재사용: paper_track._weighted_forward_return(전방수익률), portfolio_tracker(drawdown).
"""
from __future__ import annotations

import math

# ── 수익률 ────────────────────────────────────────────────────────────────────

def forward_return(prices, start_idx: int, horizon: int) -> float | None:
    """prices(list|seq)에서 start_idx 대비 horizon 후 단순수익률. 데이터 부족 시 None."""
    n = len(prices)
    if start_idx < 0 or start_idx + horizon >= n:
        return None
    p0, p1 = prices[start_idx], prices[start_idx + horizon]
    if not p0:
        return None
    return float(p1) / float(p0) - 1.0


def excess_return(stock_ret: float | None, index_ret: float | None) -> float | None:
    """초과수익 = 종목수익 − 지수수익(아웃퍼폼). 둘 중 하나라도 None 이면 None."""
    if stock_ret is None or index_ret is None:
        return None
    return stock_ret - index_ret


# ── MDD (양수 크기) ───────────────────────────────────────────────────────────

def max_drawdown(nav) -> float:
    """NAV/가치 시계열의 최대낙폭 = 양수 크기(0.20 = 20% 낙폭). 빈 입력 0.0."""
    peak = -math.inf
    mdd = 0.0
    seen = False
    for v in nav:
        if v is None:
            continue
        v = float(v)
        seen = True
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak       # 양수
            if dd > mdd:
                mdd = dd
    return mdd if seen else 0.0


def information_ratio(excess_series) -> float:
    """평균초과수익 / 추적오차(표준편차). 표본<2 또는 표준편차 0 이면 0."""
    xs = [x for x in excess_series if x is not None]
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    sd = math.sqrt(var)
    return mean / sd if sd > 0 else 0.0


# ── ★목적함수 ─────────────────────────────────────────────────────────────────

# MDD 가 지수의 이 배수를 초과하면 후보 탈락(하드 디스퀄리파이).
HARD_MDD_MULT = 1.3
# MDD 초과 패널티 계수(아웃퍼폼이 지배하도록 보조적).
MDD_PENALTY_LAMBDA = 1.0


def objective_score(strat_excess: float, strat_mdd: float, index_mdd: float,
                    *, lam: float = MDD_PENALTY_LAMBDA, hard_mult: float = HARD_MDD_MULT) -> float | None:
    """★목적함수 점수. 아웃퍼폼 최우선, MDD>지수 일 때만 패널티.

    반환 None = 하드 디스퀄리파이(전략 MDD 가 지수의 hard_mult 배 초과 → 후보 탈락).
    그 외 = strat_excess − lam·max(0, strat_mdd − index_mdd)  (높을수록 좋음).
    """
    if index_mdd is not None and index_mdd >= 0 and strat_mdd > index_mdd * hard_mult:
        return None
    over = max(0.0, strat_mdd - (index_mdd or 0.0))
    return strat_excess - lam * over


def should_adopt(challenger: dict, champion: dict | None, index_mdd: float,
                 *, min_samples: int) -> bool:
    """신규(challenger) 채택 여부 — ★목적함수 게이트.

    challenger/champion = {"excess": float, "mdd": float(양수), "n": int}.
    채택 조건(모두 충족):
      (a) 표본 n >= min_samples,
      (b) MDD 제약: challenger.mdd <= index_mdd (지수 이하),
      (c) 아웃퍼폼: champion 없으면 challenger.excess>0; 있으면 challenger.excess>champion.excess.
    """
    if challenger.get("n", 0) < min_samples:
        return False
    if index_mdd is not None and index_mdd >= 0 and challenger.get("mdd", 1.0) > index_mdd:
        return False   # MDD 제약 위반 → 아웃퍼폼이 좋아도 채택 안 함
    ce = challenger.get("excess", 0.0)
    # ★1순위 = 지수 대비 절대 아웃퍼폼(초과수익>0). 단지 직전 챔피언을 이기는 것만으론 부족
    # (언더퍼폼 정책 채택 방지). 챔피언 존재 시 추가로 챔피언보다 나아야 함.
    if ce <= 0.0:
        return False
    if champion is None:
        return True
    return ce > champion.get("excess", 0.0)
