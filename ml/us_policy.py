"""
us_policy.py — US 모의 페이퍼트레이딩 선택 정책 (학습 가능 가중치). kr_policy 해외판.

정책 점수 = US ranker(가치모델) + 규칙 신호(밸류·퀄리티·모멘텀·확신)의 가중합.
가중치는 ml.adaptive.learner 가 ★목적함수(아웃퍼폼 vs QQQ·MDD≤지수)로 재학습(OOS 게이트·
챔피언-챌린저)하고 ml.adaptive.Policy("us_mock") 가 클램프해 저장/로드. us_mock_track 가 소비.

★정직: 6티어가 US 선택 무엣지 입증 → 이 정책의 가치는 *알파 보장*이 아니라 정직한 측정 +
OOS 게이트가 노이즈/열화 채택을 막는 안전 자기개선. extract_features 는 point-in-time(룩어헤드 0).
"""
from __future__ import annotations

from ml.adaptive import Policy

# 정책 가중치(합 자유 — score 가 사용분 정규화). learner 가 이 안에서 튜닝·클램프.
# ★가격 축 3종(mom12·hi52·lowvol)은 KR 25년 백테스트(kr_policy_backtest) 근거로 **수집만**
# 시작 — US 는 이 환경서 백테스트 불가라 기본 가중 0(라이브 무영향), 원장 축적 후 주간
# 학습 OOS 게이트(us_mock_learn)가 유효하면 가중을 올린다 (정직: 무검증 축은 0).
DEFAULT_POLICY = {
    "w_ranker":  0.40,   # US LightGBM ranker (기대 초과수익) — 가치모델
    "w_value":   0.20,   # 밸류 (저PER/저PBR)
    "w_quality": 0.20,   # 퀄리티 (ROE)
    "w_mom":     0.10,   # 1개월 모멘텀
    "w_conf":    0.10,   # 의사결정 확신도
    "w_mom12":   0.0,    # 12-1M 모멘텀 (수집·게이트 채택 대기)
    "w_hi52":    0.0,    # 52주 고가 근접 (수집·게이트 채택 대기)
    "w_lowvol":  0.0,    # 저변동성 (수집·게이트 채택 대기)
}
BOUNDS = {
    "w_ranker": (0.0, 1.0), "w_value": (0.0, 1.0), "w_quality": (0.0, 1.0),
    "w_mom": (0.0, 1.0), "w_conf": (0.0, 1.0),
    "w_mom12": (0.0, 1.0), "w_hi52": (0.0, 1.0), "w_lowvol": (0.0, 1.0),
}

_SIGNAL_MAP = {"Positive": 1.0, "Neutral": 0.5, "Warning": 0.2, "Critical": 0.0}


def get_policy() -> Policy:
    return Policy("us_mock", DEFAULT_POLICY, BOUNDS)


def load_params() -> dict:
    return get_policy().load()


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def extract_features(fund: dict | None, earnings: dict | None, sig: dict | None) -> dict:
    """결정 시점 point-in-time 피처(모두 [0,1] 정규화). ranker 는 호출부가 추가(횡단면 정규화).

    반환 키: value, quality, mom, conf  (ranker 는 compute_us_signals 가 채움).
    """
    earnings = earnings or {}
    sig = sig or {}
    fund = fund or {}

    per = float(earnings.get("per", 0) or 0)
    pbr = float(earnings.get("pbr", 0) or 0)
    if per > 0 or pbr > 0:                                  # 저PER/저PBR = 높은 밸류점수
        v_per = (1.0 - min(per, 40.0) / 40.0) if per > 0 else 0.5
        v_pbr = (1.0 - min(pbr, 10.0) / 10.0) if pbr > 0 else 0.5
        value = _clamp01(0.5 * v_per + 0.5 * v_pbr)
    else:
        value = 0.5
    roe = float(earnings.get("roe", 0) or 0)               # ROE 0~30% → 0~1
    quality = _clamp01(roe / 30.0) if roe else 0.5
    mom_pct = float((sig.get("price_info") or {}).get("1mo_change_pct", 0) or 0)
    mom = _clamp01(0.5 + mom_pct / 40.0)                   # ±20% → 0~1
    conf = _clamp01(float(fund.get("confidence", 50) or 50) / 100.0)

    return {"value": round(value, 4), "quality": round(quality, 4),
            "mom": round(mom, 4), "conf": round(conf, 4)}


def price_axes(close) -> dict:
    """12M 종가 → {mom12, hi52, lowvol} [0,1] — kr_policy.price_axes 재사용 (단일 구현)."""
    from ml.kr_policy import price_axes as _pa
    return _pa(close)


def score(features: dict, params: dict | None = None) -> float:
    """정책 점수(0~1). features 성분(ranker/value/quality/mom/conf) 가중평균.

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
