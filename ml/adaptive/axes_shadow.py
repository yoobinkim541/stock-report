"""ml/adaptive/axes_shadow.py — 가격축 권고 shadow 게이트 머지 (KR·US 공용 단일 구현).

주간 재검증 크론(kr_axes_eval·us_axes_eval)이 기록한 축 권고를 정책 load_params 가
머지할 때의 공통 안전장치: env 게이트(기본 off)·stale 무시·클램프·축 합 상한.
kr_policy._apply_axes_shadow 에서 일반화 — 수식·가드 동일(모의 선택 전용·실계좌 경로 0).
"""
from __future__ import annotations

import os

MAX_SHARE_DEFAULT = 0.5     # 가격축 합 ≤ 전체 가중의 절반 (축 독식 방지)
MAX_AGE_D_DEFAULT = 21      # 주간 크론 2회 이상 누락 시 stale → 무시


def apply_axes_shadow(params: dict, *, env_key: str, path: str, axes_keys: tuple,
                      clamp, max_share: float = MAX_SHARE_DEFAULT,
                      max_age_d: int = MAX_AGE_D_DEFAULT) -> dict:
    """env 게이트 통과 + shadow 신선 시 가격축 가중을 권고로 교체. 실패/미충족 시 원본.

    clamp: bounds 강제 함수(Policy.clamp). 축소 후 비중이 상한이 되도록
    cap = rest·S/(1−S) (전체합 기준 cap 은 축소 후에도 비율 초과 — 폐구간 해).
    """
    if os.getenv(env_key, "false").lower() != "true":
        return params
    try:
        import json
        from datetime import datetime
        with open(path, encoding="utf-8") as f:
            shadow = json.load(f)
        asof = str(shadow.get("asof", ""))[:10]
        if (datetime.now() - datetime.strptime(asof, "%Y-%m-%d")).days > max_age_d:
            return params
        pw = shadow.get("policy_weights") or {}
        if not any(k in pw for k in axes_keys):
            return params
        out = dict(params)
        for k in axes_keys:
            out[k] = float(pw.get(k, 0.0))
        axes_sum = sum(out.get(k, 0.0) for k in axes_keys)
        rest_sum = sum(v for k, v in out.items() if k.startswith("w_") and k not in axes_keys)
        cap = rest_sum * max_share / max(1e-9, 1.0 - max_share)
        if rest_sum > 0 and axes_sum > cap:
            scale = cap / axes_sum
            for k in axes_keys:
                out[k] = round(out[k] * scale, 4)
        return clamp(out)
    except Exception:
        return params
