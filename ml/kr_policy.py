"""
kr_policy.py — KR 모의 페이퍼트레이딩 선택 정책 (학습 가능 가중치).

정책 점수 = KR ranker(가치모델) + 규칙 신호(펀더멘털·시그널·확신·모멘텀)의 가중합.
가중치는 ml.adaptive.learner 가 ★목적함수(아웃퍼폼 vs KOSPI·MDD≤지수)로 재학습하고
ml.adaptive.Policy("kr_mock") 가 클램프해 저장/로드. compute_kr_signals 가 소비.

extract_features 는 point-in-time(룩어헤드 없음) 수치 피처 → 결정 원장에 그대로 적재돼
학습 입력이 된다.
"""
from __future__ import annotations

from ml.adaptive import Policy

# 정책 가중치(합 자유 — score 에서 사용분 정규화). learner 가 이 안에서 튜닝·클램프.
# (선택은 policy_score 상위 max_positions 랭킹 → 별도 threshold 불필요.)
DEFAULT_POLICY = {
    "w_ranker": 0.40,    # KR ranker(코스피 대비 초과수익 기대) — 가치모델
    "w_fund":   0.20,    # 펀더멘털 점수
    "w_signal": 0.20,    # 일일 시그널 정렬
    "w_conf":   0.10,    # 의사결정 확신도
    "w_mom":    0.10,    # 1개월 모멘텀
}
BOUNDS = {
    "w_ranker": (0.0, 1.0), "w_fund": (0.0, 1.0), "w_signal": (0.0, 1.0),
    "w_conf": (0.0, 1.0), "w_mom": (0.0, 1.0),
}

_SIGNAL_MAP = {"Positive": 1.0, "Neutral": 0.5, "Warning": 0.2, "Critical": 0.0}


def get_policy() -> Policy:
    return Policy("kr_mock", DEFAULT_POLICY, BOUNDS)


def load_params() -> dict:
    return get_policy().load()


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def extract_features(fund: dict | None, sig: dict | None, decision: dict | None) -> dict:
    """결정 시점 point-in-time 피처(모두 [0,1] 정규화). ranker 는 호출부가 추가(횡단면 정규화).

    반환 키: fund, signal, conf, mom  (ranker 는 compute_kr_signals 가 채움).
    """
    fund = fund or {}
    sig = sig or {}
    decision = decision or {}

    fund_score = _clamp01(float(fund.get("total_score", 0) or 0) / 100.0)
    signal = _SIGNAL_MAP.get((sig.get("overall_signal") or "Neutral"), 0.5)
    conf = _clamp01(float(decision.get("confidence", 50) or 50) / 100.0)
    mom_pct = float((sig.get("price_info") or {}).get("1mo_change_pct", 0) or 0)
    # 1M 모멘텀 -20%~+20% → 0~1 (중앙 0.5)
    mom = _clamp01(0.5 + mom_pct / 40.0)

    return {"fund": round(fund_score, 4), "signal": round(signal, 4),
            "conf": round(conf, 4), "mom": round(mom, 4)}


def score(features: dict, params: dict | None = None) -> float:
    """정책 점수(0~1). features 의 각 성분(fund/signal/conf/mom/ranker)을 가중평균.

    누락 성분(예: ranker 미산출)은 자동 제외하고 사용 가중치로 재정규화 → graceful.
    """
    p = params or load_params()
    weights = {k[2:]: float(v) for k, v in p.items() if k.startswith("w_")}
    used = {name: float(features[name]) for name in weights
            if name in features and features[name] is not None}
    total_w = sum(weights[n] for n in used)
    if total_w <= 0:
        return 0.0
    return sum(weights[n] * used[n] for n in used) / total_w
