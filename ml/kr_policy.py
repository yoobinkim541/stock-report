"""
kr_policy.py — KR 모의 페이퍼트레이딩 선택 정책 (학습 가능 가중치).

정책 점수 = KR ranker(가치모델) + 규칙 신호(펀더멘털·시그널·확신·모멘텀) + 가격 축
(12-1M 모멘텀·52주고가 근접·저변동)의 가중합. 가중치는 ml.adaptive.learner 가
★목적함수(아웃퍼폼 vs KOSPI·MDD≤지수)로 재학습하고 ml.adaptive.Policy("kr_mock") 가
클램프해 저장/로드. compute_kr_signals 가 소비.

extract_features 는 point-in-time(룩어헤드 없음) 수치 피처 → 결정 원장에 그대로 적재돼
학습 입력이 된다. price_axes 는 12M 종가 시계열 → 가격 축 3종 (그래이스풀 — 이력 부족 시 {}).

★2026-07 backtest/kr_policy_backtest 실증(2001~2026 무생존편향·순비용·워크포워드) 반영:
  - 기존 w_mom(+1M 수익률) 축은 25년 CAGR +4.6%·MDD 87.5% 로 지수(+10.4%) 대비 크게 열위
    → 기본 가중 0.10→0.05 축소(제거 아님 — 라이브 원장 학습 여지 유지).
  - hi52(52주고가 근접)·lowvol(저변동)·mom12(12-1M)는 워크포워드 반복 채택 축(OOS 연결
    +5.5%p/년·MDD≤지수 — 단 DSR 미달 OBSERVE) → 신규 축 추가·보수적 기본 가중.
  판정 상세는 ~/reports/ml-cache/kr_policy_backtest.json.
"""
from __future__ import annotations

from ml.adaptive import Policy

# 정책 가중치(합 자유 — score 에서 사용분 정규화). learner 가 이 안에서 튜닝·클램프.
# (선택은 policy_score 상위 max_positions 랭킹 → 별도 threshold 불필요.)
DEFAULT_POLICY = {
    "w_ranker": 0.30,    # KR ranker(코스피 대비 초과수익 기대) — 가치모델
    "w_fund":   0.15,    # 펀더멘털 점수
    "w_signal": 0.15,    # 일일 시그널 정렬
    "w_conf":   0.05,    # 의사결정 확신도
    "w_mom":    0.05,    # 1개월 모멘텀 — ★백테스트상 열위 축(단기반전) → 축소
    "w_hi52":   0.15,    # ★52주 고가 근접 — 워크포워드 최다 채택 축
    "w_lowvol": 0.10,    # ★저변동성 — MDD≤지수 제약에 기여
    "w_mom12":  0.05,    # ★12-1M 모멘텀 (문헌 표준·단기반전 오염 제거)
}
BOUNDS = {
    "w_ranker": (0.0, 1.0), "w_fund": (0.0, 1.0), "w_signal": (0.0, 1.0),
    "w_conf": (0.0, 1.0), "w_mom": (0.0, 1.0),
    "w_hi52": (0.0, 1.0), "w_lowvol": (0.0, 1.0), "w_mom12": (0.0, 1.0),
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


def price_axes(close) -> dict:
    """12M 종가 시계열(pandas Series·최신이 끝) → 가격 축 3종 [0,1]. 이력 부족 시 {} (graceful).

    point-in-time: 호출 시점까지의 종가만 넣을 것. **수정주가**여야 함(yfinance auto_adjust
    또는 KRX 기준가 조정 등락률 누적 — 무수정 종가는 분할·감자 가짜 점프로 오염).
      mom12  = 252~21일 전 수익률 (12-1M 모멘텀), ±40% → 0~1
      hi52   = 현재가/52주 최고가 (자연히 0~1)
      lowvol = 60일 일수익 연율변동성 10%~60% → 1~0 (낮을수록 높은 점수)
    """
    try:
        c = close.dropna()
        if len(c) < 130:                                   # 최소 ~6개월 (mom12 는 252 요건)
            return {}
        out = {}
        if len(c) >= 253:
            m12 = float(c.iloc[-22] / c.iloc[-253] - 1.0)
            out["mom12"] = round(_clamp01(0.5 + m12 / 0.8), 4)
        hi = float(c.iloc[-min(len(c), 253):].max())
        if hi > 0:
            out["hi52"] = round(_clamp01(float(c.iloc[-1]) / hi), 4)
        r = c.pct_change().dropna().iloc[-60:]
        if len(r) >= 30:
            vol_ann = float(r.std()) * (252 ** 0.5)
            out["lowvol"] = round(_clamp01(1.0 - (vol_ann - 0.10) / 0.50), 4)
        return out
    except Exception:
        return {}


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
