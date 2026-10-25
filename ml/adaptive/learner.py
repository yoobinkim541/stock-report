"""
learner.py — walk-forward 재적합 + ★목적함수 OOS 채택 게이트 + 통계 게이트.

흐름: 원장 학습행(decision⋈outcome) → 날짜순 train/OOS 분할(purge) → fit_fn 으로 후보
파라미터 적합 → eval_fn 으로 OOS에서 후보 vs 현행(챔피언) 평가 → reward.should_adopt
(아웃퍼폼 최우선·MDD≤지수) **그리고** adoption_evidence(독립 표본·다중시도 보정 유의성)를
모두 통과할 때만 policy.save. 표본 부족/미개선/증거 누락·계산 실패면 보류.

통계 게이트는 새 채택에만 적용된다 — 이미 저장된 정책(champion)은 그대로 로드된다.

재사용: backtest/entry_calibration(:182 OOS 게이트), ml/ranker(purge embargo), ml/validation(DSR).
"""
from __future__ import annotations

import logging
import math

from ml.adaptive import reward as _reward

logger = logging.getLogger(__name__)


NEW_AXIS_MIN_PAIRS = 20   # 신규 축(원장 축적 초기)은 일반 축(5)보다 높은 최소표본 요구 (E)

# ── 채택 통계 게이트 (사전 정의 — 결과를 본 뒤 바꾸지 않는다) ────────────────────────
# 보유기간이 겹치지 않는 OOS 평가 단위(eval_fn 의 n_independent) 최소 수. 이보다 적으면
# 어떤 유의성 계산도 채택 근거로 쓰지 않는다.
ADOPT_MIN_INDEPENDENT = 8
# 단측 95%: OOS 평가단위 초과수익의 Sharpe 가 SR0 보다 클 확률(PSR, T=n_independent).
# 같은 표면의 누적 시도 수 n_trials>1 이면 SR0 = 시도별 Sharpe 분산으로 구한 기대 최대
# Sharpe(= DSR, López de Prado). 시도 분산 증거가 없으면 계산 불가 → 보류.
ADOPT_MIN_PSR = 0.95


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trial_history(surface: str, base_dir: str | None = None) -> tuple[int, list[float]]:
    """학습 이력 → (이전에 OOS 평가까지 간 시도 수, 기록된 시도별 Sharpe).

    매주 같은 OOS 구조를 다시 평가하는 것도 시도로 센다(동일 OOS 반복 재사용 보정)."""
    from ml.adaptive import evolution
    hist = evolution.read_learning(surface, base_dir=base_dir)
    tried = [h for h in hist if h.get("excess_challenger") is not None]
    sharpes = [float(h["sharpe_challenger"]) for h in tried if _finite(h.get("sharpe_challenger"))]
    return len(tried), sharpes


def adoption_evidence(challenger: dict, *, n_trials: int = 1, trial_sharpes=None) -> dict:
    """challenger OOS 시계열 → 채택 통계 증거. ok=False 면 채택 보류.

    challenger["series"]: 평가 단위별 OOS 초과수익(시간순),
    challenger["n_independent"]: 그중 보유기간이 겹치지 않는 단위 수(PSR 의 T).
    n_trials: 이번을 포함한 누적 시도 수. trial_sharpes: 이전 시도 Sharpe 기록.
    반환 {"ok", "reason", "psr", "sharpe_pp", "n_trials", "n_independent"} — sharpe_pp 는
    다음 시도의 분산 추정을 위해 이력에 남긴다.
    """
    n_trials = max(1, int(n_trials or 1))
    out = {"ok": False, "reason": None, "psr": None, "sharpe_pp": None,
           "n_trials": n_trials, "n_independent": None}
    series = challenger.get("series") if isinstance(challenger, dict) else None
    n_ind = challenger.get("n_independent") if isinstance(challenger, dict) else None
    if not isinstance(series, (list, tuple)) or len(series) < 2:
        out["reason"] = "evidence_missing"
        return out
    if (not all(_finite(v) for v in series) or not isinstance(n_ind, int) or isinstance(n_ind, bool)
            or not 1 <= n_ind <= len(series)):
        out["reason"] = "evidence_invalid"
        return out
    out["n_independent"] = n_ind
    try:
        from ml import validation
        sharpe = float(validation.sharpe_ratio(series)["pp"])
        out["sharpe_pp"] = round(sharpe, 6)
        if n_ind < ADOPT_MIN_INDEPENDENT:
            out["reason"] = "insufficient_independent"
            return out
        trials = None
        if n_trials > 1:
            trials = [float(v) for v in (trial_sharpes or []) if _finite(v)] + [sharpe]
        psr = validation.deflated_sharpe_ratio(series, n_trials=n_trials, trial_sharpes=trials,
                                               effective_n=n_ind)
    except Exception as e:      # 계산 실패 = 증거 없음
        logger.warning("채택 통계 계산 실패 — 보류: %s", e)
        out["reason"] = "stat_failed"
        return out
    if psr is None or not math.isfinite(psr):
        out["reason"] = "trial_variance_missing" if n_trials > 1 else "stat_failed"
        return out
    out["psr"] = round(float(psr), 6)
    if psr < ADOPT_MIN_PSR:
        out["reason"] = "not_significant"
        return out
    out["ok"] = True
    return out


def _pearson(xs: list, ys: list) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    dy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def robust_axis_weight(pairs: list, *, min_pairs: int = 5, stability: bool = False):
    """(피처값, 실현보상) 쌍 → 축 가중 후보 max(0, pearson). 표본<min_pairs → None(미측정).

    stability=True (신규 축): 표본을 전/후반으로 갈라 **두 반쪽의 상관 부호가 모두 양(+)**
    일 때만 인정 — 소표본 노이즈로 신규 축이 가중 승격되는 것 방지(E). 불일치 → 0.0
    (측정됐으나 무신호로 취급 — DEFAULT 프라이어 쏠림 함정과 별개).
    """
    if len(pairs) < min_pairs:
        return None
    xs, ys = [a for a, _ in pairs], [b for _, b in pairs]
    full = max(0.0, _pearson(xs, ys))
    if not stability or full <= 0.0:
        return full
    h = len(pairs) // 2
    first = _pearson(xs[:h], ys[:h])
    second = _pearson(xs[h:], ys[h:])
    return full if (first > 0 and second > 0) else 0.0


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
                    min_samples: int = 40, train_frac: float = 0.6, embargo: int = 20,
                    n_trials: int = 1, trial_sharpes=None) -> dict:
    """walk-forward 재적합 + ★목적함수 OOS 게이트 + 통계 게이트로 정책 채택 결정.

    rows:    학습행(각 dict 에 'date' 포함; decision⋈outcome).
    policy:  ml.adaptive.Policy — 현행(챔피언) 파라미터 소스이자 채택 대상.
    fit_fn:  (train_rows) -> candidate_params(dict).
    eval_fn: (oos_rows, params) -> {"excess": float, "mdd": float(양수), "n": int(평가 단위 수),
             "series": [평가 단위별 초과수익], "n_independent": int}. series/n_independent 가
             없으면 통계 증거 누락으로 보류.
    index_mdd: 동기간 지수 MDD(양수) — MDD 제약 기준.
    n_trials/trial_sharpes: 이 표면의 누적 시도 수(이번 포함)·이전 시도 Sharpe (trial_history).

    반환: {"adopted": bool, "reason": str, "challenger": {...}, "champion": {...}, "evidence": {...}}.
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

    # 총표본 게이트는 위 len(rows)>=min_samples 로 이미 적용. should_adopt 의 n 은 *OOS 평가 단위* 수이므로
    # train_frac 분할 후 OOS 가 min_samples 에 못 미쳐 영구 미채택되는 문제를 피해 별도(작은) 임계값 사용.
    oos_min = max(10, min_samples // 3)
    objective_ok = _reward.should_adopt(chal, champ, index_mdd, min_samples=oos_min)
    evidence = adoption_evidence(chal, n_trials=n_trials, trial_sharpes=trial_sharpes)
    adopt = bool(objective_ok and evidence["ok"])
    stat_note = (f"PSR {evidence['psr']} · 독립 {evidence['n_independent']} · 시도 {evidence['n_trials']}"
                 if evidence.get("psr") is not None else
                 f"{evidence['reason']} · 독립 {evidence['n_independent']} · 시도 {evidence['n_trials']}")
    if adopt:
        policy.save(candidate, meta={"oos_excess": chal.get("excess"), "oos_mdd": chal.get("mdd"),
                                     "index_mdd": index_mdd, "n_oos": chal.get("n"),
                                     "n_independent": evidence["n_independent"],
                                     "psr": evidence["psr"], "n_trials": evidence["n_trials"]})
        reason = (f"채택 ✅ OOS 초과수익 {chal.get('excess'):+.4f}>{champ.get('excess'):+.4f} "
                  f"· MDD {chal.get('mdd'):.3f}≤지수 {index_mdd:.3f} · {stat_note}")
    elif objective_ok:
        reason = (f"보류 ⏸️ 통계 게이트 미통과 (OOS 초과수익 {chal.get('excess'):+.4f} "
                  f"vs {champ.get('excess'):+.4f} · {stat_note})")
    else:
        reason = (f"보류 ⏸️ (OOS 초과수익 {chal.get('excess'):+.4f} vs {champ.get('excess'):+.4f} "
                  f"· MDD {chal.get('mdd'):.3f} vs 지수 {index_mdd:.3f})")
    return {"adopted": adopt, "reason": reason, "challenger": chal, "champion": champ,
            "candidate_params": candidate, "evidence": evidence}
