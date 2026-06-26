"""
champion_challenger.py — 신규 정책(챌린저) 섀도 평가 → 승격 판정.

라이브에 바로 적용하지 않고, 챔피언(현행) 대비 OOS 우위 + ★목적함수(MDD≤지수) 충족
+ 표본 충분일 때만 승격. 패턴 출처: crons/paper_track(meta vs rule, 독립블록 보정).
"""
from __future__ import annotations

from ml.adaptive import reward as _reward


def independent_blocks(n_samples: int, horizon: int) -> int:
    """겹치는 전방수익률 표본의 대략적 독립 블록 수(자기상관 보정 참고용)."""
    if horizon <= 0:
        return n_samples
    return max(1, n_samples // horizon)


def evaluate(champion_eval: dict | None, challenger_eval: dict, index_mdd: float,
             *, min_samples: int = 40, horizon: int = 20) -> dict:
    """승격 판정.

    *_eval = {"excess": float, "mdd": float(양수), "n": int}.
    반환: {"promote": bool, "reason": str, "indep_blocks": int}.
    """
    n = challenger_eval.get("n", 0)
    indep = independent_blocks(n, horizon)
    promote = _reward.should_adopt(challenger_eval, champion_eval, index_mdd, min_samples=min_samples)

    if n < min_samples:
        reason = f"표본 부족 {n}/{min_samples} — 챔피언 유지"
    elif not promote:
        ce = challenger_eval.get("excess", 0.0)
        mdd = challenger_eval.get("mdd", 0.0)
        if index_mdd is not None and index_mdd >= 0 and mdd > index_mdd:
            reason = f"MDD 제약 위반 {mdd:.3f}>지수 {index_mdd:.3f} — 승격 거부"
        else:
            champ_e = (champion_eval or {}).get("excess", 0.0)
            reason = f"아웃퍼폼 미달 {ce:+.4f}≤{champ_e:+.4f} — 승격 거부"
    else:
        reason = (f"승격 ✅ 초과수익 {challenger_eval.get('excess'):+.4f} · "
                  f"MDD {challenger_eval.get('mdd'):.3f}≤지수 {index_mdd:.3f} · 독립블록≈{indep}")
    return {"promote": promote, "reason": reason, "indep_blocks": indep}
