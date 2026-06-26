"""
learner.py — walk-forward 재적합 + ★목적함수 OOS 채택 게이트.

흐름: 원장 학습행(decision⋈outcome) → 날짜순 train/OOS 분할(purge) → fit_fn 으로 후보
파라미터 적합 → eval_fn 으로 OOS에서 후보 vs 현행(챔피언) 평가 → reward.should_adopt
(아웃퍼폼 최우선·MDD≤지수)를 통과할 때만 policy.save. 표본 부족/미개선이면 보류.

재사용: backtest/entry_calibration(:182 OOS 게이트), ml/ranker(purge embargo).
"""
from __future__ import annotations

import logging

from ml.adaptive import reward as _reward

logger = logging.getLogger(__name__)


def walk_forward_split(dates: list, *, train_frac: float = 0.6, embargo: int = 0):
    """날짜순 train/OOS 마스크. **embargo 는 고유 거래일(날짜) 단위** purge.

    하루 다종목(멀티 행/일)이라도 경계 누수가 없도록, 분할·purge 를 *고유 날짜* 기준으로
    수행한다(샘플 인덱스 단위 X — ml/ranker 의 date-space purge 와 동일 철학).

    dates: 각 표본의 날짜('YYYY-MM-DD' 등 정렬가능). 표본 순서와 동일 길이.
    반환: (train_mask, oos_mask) — 각 len(dates).
    """
    n = len(dates)
    if n == 0:
        return [], []
    uniq = sorted(set(dates))
    if len(uniq) < 2:
        return [False] * n, [False] * n
    split_idx = int(len(uniq) * train_frac)
    if split_idx <= 0 or split_idx >= len(uniq):
        return [False] * n, [False] * n
    oos_start = uniq[split_idx]                       # OOS 시작 날짜(포함)
    purge_date = uniq[max(0, split_idx - embargo)]    # train 은 이 날짜 이전까지만(embargo 거래일 purge)
    train_mask = [d < purge_date for d in dates]
    oos_mask = [d >= oos_start for d in dates]
    return train_mask, oos_mask


def refit_and_adopt(rows: list[dict], policy, fit_fn, eval_fn, *, index_mdd: float,
                    min_samples: int = 40, train_frac: float = 0.6, embargo: int = 20) -> dict:
    """walk-forward 재적합 + ★목적함수 OOS 게이트로 정책 채택 결정.

    rows:    학습행(각 dict 에 'date' 포함; decision⋈outcome).
    policy:  ml.adaptive.Policy — 현행(챔피언) 파라미터 소스이자 채택 대상.
    fit_fn:  (train_rows) -> candidate_params(dict).
    eval_fn: (oos_rows, params) -> {"excess": float, "mdd": float(양수), "n": int}.
    index_mdd: 동기간 지수 MDD(양수) — MDD 제약 기준.

    반환: {"adopted": bool, "reason": str, "challenger": {...}, "champion": {...}}.
    """
    if len(rows) < min_samples:
        return {"adopted": False, "reason": f"표본 부족 {len(rows)}/{min_samples} — 보류(콜드스타트 유지)",
                "challenger": None, "champion": None}

    dates = [r.get("date", "") for r in rows]
    train_mask, oos_mask = walk_forward_split(dates, train_frac=train_frac, embargo=embargo)
    train_rows = [r for r, m in zip(rows, train_mask) if m]
    oos_rows = [r for r, m in zip(rows, oos_mask) if m]
    if not train_rows or not oos_rows:
        return {"adopted": False, "reason": "분할 불가(표본 편중) — 보류",
                "challenger": None, "champion": None}

    try:
        candidate = fit_fn(train_rows)
    except Exception as e:
        logger.warning("fit_fn 실패 — 보류: %s", e)
        return {"adopted": False, "reason": f"적합 실패: {e}", "challenger": None, "champion": None}

    candidate = policy.clamp(candidate)            # 극단 차단(안전)
    champion_params = policy.load()
    chal = eval_fn(oos_rows, candidate)
    champ = eval_fn(oos_rows, champion_params)

    # 총표본 게이트는 위 len(rows)>=min_samples 로 이미 적용. should_adopt 의 n 은 *OOS* 표본수이므로
    # train_frac 분할 후 OOS 가 min_samples 에 못 미쳐 영구 미채택되는 문제를 피해 별도(작은) 임계값 사용.
    oos_min = max(10, min_samples // 3)
    adopt = _reward.should_adopt(chal, champ, index_mdd, min_samples=oos_min)
    if adopt:
        policy.save(candidate, meta={"oos_excess": chal.get("excess"), "oos_mdd": chal.get("mdd"),
                                     "index_mdd": index_mdd, "n_oos": chal.get("n")})
        reason = (f"채택 ✅ OOS 초과수익 {chal.get('excess'):+.4f}>{champ.get('excess'):+.4f} "
                  f"· MDD {chal.get('mdd'):.3f}≤지수 {index_mdd:.3f}")
    else:
        reason = (f"보류 ⏸️ (OOS 초과수익 {chal.get('excess'):+.4f} vs {champ.get('excess'):+.4f} "
                  f"· MDD {chal.get('mdd'):.3f} vs 지수 {index_mdd:.3f})")
    return {"adopted": adopt, "reason": reason, "challenger": chal, "champion": champ,
            "candidate_params": candidate}
