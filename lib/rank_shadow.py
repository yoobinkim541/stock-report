"""lib/rank_shadow.py — 랭킹 섀도 원장 (편입/편출 예측력 측정용).

문제(2026-08 감사): 모의 트래커는 매일 유니버스 20종목을 점수화하지만, 실제 주문된
~3건만 원장에 남기고 17건을 버린다. 이 때문에 두 가지가 동시에 망가진다.

  1) 표본 기근 — 콜드스타트(40건) 도달에 수개월. 국내는 최소보유 60일까지 겹쳐 정체.
  2) **구간 제한(range restriction)** — 상위 3개만 남으면 policy_score 분산이 잘려
     Pearson IC 가 구조적으로 0 쪽으로 감쇠한다. 즉 관측된 "IC≈0" 은 "선택 스킬 없음"의
     증거가 아니라 **측정 자체가 불가능한 상태**의 증거다(감쇠 방향이 알려진 편향).

해결: 주문 여부와 무관하게 **점수화된 전 후보**를 별도 섀도 표면(`{kr,us}_mock_shadow`)에
남긴다. 라이브 원장·회전율·비용 현실성은 전혀 건드리지 않고(주문 안 냄), 선택편향 없는
전 구간 IC 를 얻는다. 기존 `*_llm_shadow` 표면과 같은 계보.

섀도 결정은 side="관측" — 라이브 편입/증액 통계와 절대 섞이지 않는다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SIDE = "관측"


def log_ranked_candidates(ledger, signals, *, today: str, market: str,
                          limit: int | None = None) -> int:
    """점수화된 전 후보를 섀도 원장에 적재. 적재 건수 반환.

    signals: compute_*_signals() 출력(주문 계획 전 원본 — 상위 N 절단 이전).
    실패해도 예외를 올리지 않는다 — 섀도는 부가 기능이라 라이브 주문 경로를 막으면 안 됨.
    """
    rows = [s for s in (signals or []) if (s or {}).get("policy_score") is not None]
    if not rows:
        return 0
    ranked = sorted(rows, key=lambda s: -float(s["policy_score"]))
    if limit:
        ranked = ranked[:limit]
    universe = len(ranked)
    n = 0
    for i, sig in enumerate(ranked, start=1):
        try:
            ledger.log_decision({
                "date": today,
                "ticker": sig.get("ticker"),
                "code": sig.get("code"),
                "side": SIDE,                    # 관측 — 실제 주문 아님
                "shadow": True,
                "rank": i,
                "universe": universe,
                "price": sig.get("price"),
                "action": sig.get("action"),
                "base_score": sig.get("base_score"),
                "policy_score": sig.get("policy_score"),
                "selection_score": sig.get("selection_score"),
                "regime": sig.get("regime"),
                "market": market,
                "features": sig.get("features"),   # point-in-time — 학습 입력
                "is_buy": bool(sig.get("is_buy")),
                "ok": True,                        # 섀도는 집행 개념이 없음(팬텀 제외 규칙 통과용)
            })
            n += 1
        except Exception as e:                     # noqa: BLE001 — 섀도 실패는 라이브 무영향
            logger.warning("랭킹 섀도 적재 실패(무시): %s", e)
            return n
    return n
