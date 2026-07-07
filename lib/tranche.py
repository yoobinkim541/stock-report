"""lib/tranche.py — 분할매수·분할매도 (트란치 실행) 순수 로직. KR·US 모의 공용.

한 번에 목표까지 일괄 체결하는 대신 회당 **목표의 1/N** 만 거래 → 진입/청산 가격을
N회에 평균(timing·시장충격 분산). 신규 진입은 N회에 걸쳐 채우고, 청산도 N회에 걸쳐 뺀다.

정직 규율: 분할은 **알파가 아니라 분산 축소**다. 효율적 시장에서 기대수익은 일괄과 동일하거나
추세장에선 소폭 열위(평균단가가 높아짐) — 대신 타이밍운·시장충격 변동을 줄인다. 모의 비용
모델(bps×거래대금)에선 N분할해도 총 거래대금 동일 → **bps 비용 불변**(무료 분산 축소).
상태 없음(stateless): 포지션 크기 자체가 진행도를 인코딩 — 매 실행 리밸런스가 남은 갭을
다시 상한만큼 줄여 N회에 수렴. min_hold(청산 지연)·rebal_band(잔챙이 skip)과 독립 합성.
"""
from __future__ import annotations

import math


def tranche_cap_shares(per_position_value: float, price: float, tranches: int) -> int:
    """균등배분 1종목 목표금액 → 회당 최대 거래 주수 = ceil((목표금액/가격)/N). 최소 1주."""
    if tranches <= 1 or per_position_value <= 0 or price <= 0:
        return 0   # 0 = 상한 없음(호출부가 원본 유지)
    full = per_position_value / price
    return max(1, math.ceil(full / tranches))


def plan_tranches(orders: list[dict], per_position_value: float, price_of,
                  tranches: int, *, id_key: str = "code") -> list[dict]:
    """주문 계획을 회당 full/N 상한으로 분할 (분할매수·분할매도). 순수.

    orders:    plan_rebalance 산출 [{<id_key>, side, qty, reason}, ...].
    per_position_value: 균등배분 1종목 목표 금액(budget/max_positions) — 분할 기준 '풀 사이즈'.
    price_of:  id → 가격 콜러블(호가/현재가). 미상(≤0) 이면 그 주문은 상한 미적용(원본 유지).
    tranches:  N. N<=1 이면 원본 그대로(현행 일괄). N>1 이면 |qty| ≤ ceil((풀주수)/N).
    반환: 상한 적용 주문(수량 0 이 되면 제외 — 다음 실행에서 재개).
    """
    if tranches <= 1:
        return orders
    out: list[dict] = []
    for o in orders:
        cap = tranche_cap_shares(per_position_value, price_of(o.get(id_key)) or 0.0, tranches)
        if cap <= 0:
            out.append(o)                       # 가격 미상 → 분할 불가, 원본 유지
            continue
        q = min(int(o.get("qty", 0) or 0), cap)
        if q <= 0:
            continue
        note = o.get("reason", "")
        out.append({**o, "qty": q,
                    "reason": (f"{note}·{tranches}분할" if note else f"{tranches}분할")})
    return out
