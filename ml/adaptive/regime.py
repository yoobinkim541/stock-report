"""
regime.py — 레짐 식별 + 최근성 가중("변화하는 시장 적응").

- recency_weights: 최근 표본에 더 큰 가중(지수 감쇠) → 학습이 최신 레짐을 추종.
- current_regime: 현 시장 레짐(risk_on/off/neutral) best-effort(meta_allocator 위임).
재사용: ml/meta_allocator(_determine_regime), ml/entry_analyzer._find_similar(거리/레짐 가중).
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def recency_weights(dates: list, *, half_life_days: float = 180.0) -> list[float]:
    """날짜 리스트 → 최근일수록 큰 가중(반감기 지수 감쇠), 합=len(가중)으로 정규화.

    dates: 'YYYY-MM-DD' 등 정렬가능. 가장 최근 날짜의 가중이 가장 큼.
    half_life_days: 가중이 절반이 되는 경과일.
    """
    if not dates:
        return []
    import datetime as _dt

    def _parse(d):
        try:
            return _dt.date.fromisoformat(str(d)[:10]).toordinal()
        except Exception:
            return None

    ords = [_parse(d) for d in dates]
    valid = [o for o in ords if o is not None]
    if not valid:
        return [1.0] * len(dates)
    latest = max(valid)
    lam = math.log(2.0) / max(1.0, half_life_days)
    raw = []
    for o in ords:
        if o is None:
            raw.append(0.5)   # 파싱 불가 → 중립 가중
        else:
            raw.append(math.exp(-lam * (latest - o)))
    s = sum(raw)
    if s <= 0:
        return [1.0] * len(dates)
    # 합을 표본수로 정규화(평균가중 1 유지 — 스케일 안정)
    k = len(raw) / s
    return [w * k for w in raw]


def current_regime() -> tuple[str, float]:
    """현 시장 레짐 best-effort. 실패 시 ('neutral', 0.0).

    반환: (regime in {'risk_on','risk_off','neutral'}, confidence 0~1).
    """
    try:
        from ml import meta_allocator
        # meta_allocator 가 노출하는 레짐 산출 경로를 best-effort 로 사용.
        for fn in ("current_regime", "get_regime"):
            f = getattr(meta_allocator, fn, None)
            if callable(f):
                r = f()
                if isinstance(r, tuple) and len(r) >= 2:
                    return str(r[0]), float(r[1])
    except Exception as e:
        logger.debug("레짐 식별 실패 — neutral: %s", e)
    return "neutral", 0.0
