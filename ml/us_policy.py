"""
us_policy.py — US 모의 페이퍼트레이딩 선택 정책 (학습 가능 가중치). kr_policy 해외판.

정책 점수 = US ranker(가치모델) + 규칙 신호(밸류·퀄리티·모멘텀·확신)의 가중합.
가중치는 ml.adaptive.learner 가 ★목적함수(아웃퍼폼 vs QQQ·MDD≤지수)로 재학습(OOS 게이트·
챔피언-챌린저)하고 ml.adaptive.Policy("us_mock") 가 클램프해 저장/로드. us_mock_track 가 소비.

★정직: 6티어가 US 선택 무엣지 입증 → 이 정책의 가치는 *알파 보장*이 아니라 정직한 측정 +
OOS 게이트가 노이즈/열화 채택을 막는 안전 자기개선. extract_features 는 point-in-time(룩어헤드 0).
"""
from __future__ import annotations

import os

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
    "w_pead":    0.0,    # ★어닝 드리프트(PEAD) — 서프라이즈×반응×감쇠 (수집·게이트 채택 대기)
}
BOUNDS = {
    "w_ranker": (0.0, 1.0), "w_value": (0.0, 1.0), "w_quality": (0.0, 1.0),
    "w_mom": (0.0, 1.0), "w_conf": (0.0, 1.0),
    "w_mom12": (0.0, 1.0), "w_hi52": (0.0, 1.0), "w_lowvol": (0.0, 1.0),
    "w_pead": (0.0, 1.0),
}

_SIGNAL_MAP = {"Positive": 1.0, "Neutral": 0.5, "Warning": 0.2, "Critical": 0.0}


def get_policy() -> Policy:
    return Policy("us_mock", DEFAULT_POLICY, BOUNDS)


# 가격축 적응 shadow (crons/us_axes_eval 주기 재검증 — 모의 선택 전용·KR 과 공용 안전장치)
AXES_SHADOW_PATH = os.path.expanduser("~/reports/ml-cache/us_policy_axes_shadow.json")
_AXES_KEYS = ("w_mom12", "w_hi52", "w_lowvol", "w_mom")


def _apply_axes_shadow(params: dict, *, path: str | None = None) -> dict:
    """ADAPTIVE_US_AXES_ENABLED=true + shadow 신선 시 가격축 가중을 권고로 교체. 실패 시 원본.

    안전장치(env 게이트 기본 off·클램프·축합 ≤50% 상한·21일 stale 무시)는
    ml.adaptive.axes_shadow 단일 구현. **모의 선택 전용** — 실계좌 주문 경로 0.
    """
    from ml.adaptive.axes_shadow import apply_axes_shadow
    return apply_axes_shadow(params, env_key="ADAPTIVE_US_AXES_ENABLED",
                             path=path or AXES_SHADOW_PATH, axes_keys=_AXES_KEYS,
                             clamp=get_policy().clamp)


def load_params() -> dict:
    return _apply_axes_shadow(get_policy().load())


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


PEAD_WINDOW_D = 60     # 실적후 드리프트 지속 창 (문헌 표준 ~60일·earnings_reaction 과 정합)


def pead_axis(events: list[dict], closes=None, asof=None) -> float | None:
    """어닝 드리프트(PEAD) 축 [0,1] — 최근 실적의 서프라이즈 방향 × 초기반응 × 시간감쇠. 순수.

    PEAD = 어닝 서프라이즈 방향으로 주가가 ~60일 표류하는 이상현상. 축 정의:
      direction = ½·(clip(surprise%/10) + clip(반응1일/5%))  (반응 없으면 서프라이즈만)
      pead      = 0.5 + 0.5·direction·(1 − days/60)          → beat+상승반응 직후 ≈1, miss ≈0
    events: earnings_data.earnings_history 형식 [{date, surprise_pct, …}] (최신순).
    closes: 일별 종가 Series(반응 산출용·선택). asof: 기준일(기본 오늘).
    반환 None = 신호 없음(최근 60일 내 실적 없음/데이터 부족) → score() 가 재정규화.
    무룩어헤드: asof 이하 데이터만 넣을 것(라이브 수집은 자연 충족 — 과거 실적·과거 종가).
    """
    try:
        import pandas as pd
        ts = pd.Timestamp(asof) if asof is not None else pd.Timestamp.now().normalize()
        ev = None
        for e in (events or []):
            try:
                d = pd.Timestamp(str(e.get("date", ""))[:10])
            except Exception:
                continue
            if d <= ts and e.get("surprise_pct") is not None:
                ev = (d, float(e["surprise_pct"]))
                break                                    # 최신순 → 첫 유효건이 최근 실적
        if ev is None:
            return None
        d, surp = ev
        days = (ts - d).days
        if days < 0 or days > PEAD_WINDOW_D:
            return None
        comp = [max(-1.0, min(1.0, surp / 10.0))]
        if closes is not None:
            try:
                c = closes.dropna()
                c.index = pd.to_datetime(c.index).tz_localize(None)
                before = c[c.index <= d]
                after = c[c.index > d]
                if len(before) and len(after):
                    r1 = float(after.iloc[0]) / float(before.iloc[-1]) - 1.0
                    comp.append(max(-1.0, min(1.0, r1 / 0.05)))
            except Exception:
                pass
        direction = sum(comp) / len(comp)
        decay = 1.0 - days / PEAD_WINDOW_D
        return round(_clamp01(0.5 + 0.5 * direction * decay), 4)
    except Exception:
        return None


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
