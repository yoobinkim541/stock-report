"""
learner.py — walk-forward 재적합 + ★목적함수 OOS 채택 게이트 + 통계 게이트.

흐름: 원장 학습행(decision⋈outcome) → 날짜순 train/OOS 분할(purge) → fit_fn 으로 후보
파라미터 적합 → eval_fn 으로 OOS에서 후보 vs 현행(챔피언) 평가 → 위험 증거(MDD·지수 MDD 산출)
확인 → reward.should_adopt(아웃퍼폼 최우선·MDD≤지수) **그리고** adoption_evidence(비중첩 표본·
다중시도 보정 유의성)를 모두 통과할 때만 policy.save. 표본 부족/미개선/증거 누락·계산 실패면 보류.

통계 게이트는 새 채택에만 적용된다 — 이미 저장된 정책(champion)은 그대로 로드된다.

재사용: backtest/entry_calibration(:182 OOS 게이트), ml/ranker(purge embargo), ml/validation(DSR).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics

from ml.adaptive import reward as _reward

logger = logging.getLogger(__name__)


NEW_AXIS_MIN_PAIRS = 20   # 신규 축(원장 축적 초기)은 일반 축(5)보다 높은 최소표본 요구 (E)

# ── 채택 통계 게이트 (사전 정의 — 결과를 본 뒤 바꾸지 않는다) ────────────────────────
# 보유기간이 겹치지 않는 OOS 관측(eval_fn 의 series — 길이 = n_independent) 최소 수. 이보다 적으면
# 어떤 유의성 계산도 채택 근거로 쓰지 않는다.
ADOPT_MIN_INDEPENDENT = 8
# 단측 95%: 비중첩 OOS 관측 초과수익의 Sharpe 가 SR0 보다 클 확률(PSR, T=len(series), 모멘트도 같은
# series). 같은 표면의 시도 수 n_trials>1 이면 SR0 = 시도별 Sharpe 분산으로 구한 기대 최대
# Sharpe(= DSR, López de Prado). 시도 분산 증거가 충분하지 않으면 계산하지 않고 보류.
ADOPT_MIN_PSR = 0.95
# 다중시도 분산 증거 충분성(n_trials>1). 시도 = (후보 파라미터, OOS 행 집합) 지문이 서로 다른 평가.
# 같은 지문의 재평가는 새 시도가 아니다(시도 수·분산 모두 한 번만 반영). 지문이 없는 legacy 이력은
# 시도 수에는 센다(보수적 — SR0 를 키움). 그러나 그 Sharpe 는 분산 증거로 쓰지 않는다. 이유는
# 표시용 반올림값이고, 동일 평가 중복 여부와 평가 단위(중첩열)가 불명이기 때문이다.
TRIAL_MIN_SHARPES = 5          # 분산 추정에 쓰는 지문별 Sharpe 최소 개수(현재 시도 포함)
TRIAL_MIN_COVERAGE = 0.5       # 그 개수 / 전체 시도 수 하한 — 대부분 누락된 이력으로 분산 추정 금지
TRIAL_MIN_SR_STD = 1e-6        # 시도 Sharpe 표준편차 하한 — 미만이면 탐색 분산 정보 없음(수치적 퇴화)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def trial_fingerprint(params, oos_rows: list[dict]) -> str:
    """시도 지문 = (후보 파라미터, OOS 행 id 집합). 같은 지문 = 같은 가설의 같은 표본 재평가."""
    from ml.adaptive.oos_eval import row_id
    payload = {"params": params, "oos": sorted(row_id(r) for r in oos_rows)}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def trial_history(surface: str, base_dir: str | None = None) -> dict:
    """학습 이력 → {"legacy": 지문 없는 평가 시도 수, "trials": {지문: 미반올림 Sharpe|None}}.

    OOS 평가까지 간 기록(excess_challenger 존재)만 시도다. 같은 지문은 처음 기록만 쓴다."""
    from ml.adaptive import evolution
    legacy = 0
    trials: dict[str, float | None] = {}
    for h in evolution.read_learning(surface, base_dir=base_dir):
        if h.get("excess_challenger") is None:
            continue
        key = h.get("trial_key")
        if not isinstance(key, str) or not key:
            legacy += 1
            continue
        if key not in trials:
            sharpe = h.get("trial_sharpe")
            trials[key] = float(sharpe) if _finite(sharpe) else None
    return {"legacy": legacy, "trials": trials}


def adoption_evidence(challenger: dict, *, trial_log: dict | None = None,
                      trial_key: str | None = None) -> dict:
    """challenger 비중첩 OOS 관측열 → 채택 통계 증거. ok=False 면 채택 보류.

    challenger["series"]: 보유기간이 겹치지 않는 OOS 관측 초과수익(시간순) — PSR/DSR 의 T 와
    모든 모멘트를 이 열에서 계산한다. challenger["n_independent"] 는 len(series) 와 같아야 한다.
    trial_log: trial_history 결과, trial_key: 이번 시도 지문(trial_fingerprint).
    반환 {"ok", "reason", "psr", "sharpe_pp"(표시용 반올림), "trial_sharpe"(미반올림 — 이력 저장용),
          "trial_key", "n_trials", "n_trial_sharpes", "n_independent"}.
    """
    log = trial_log if isinstance(trial_log, dict) else {}
    legacy = max(0, int(log.get("legacy") or 0))
    trials = dict(log.get("trials") or {})
    out = {"ok": False, "reason": None, "psr": None, "sharpe_pp": None, "trial_sharpe": None,
           "trial_key": trial_key, "n_trials": None, "n_trial_sharpes": None, "n_independent": None}
    series = challenger.get("series") if isinstance(challenger, dict) else None
    n_ind = challenger.get("n_independent") if isinstance(challenger, dict) else None
    sharpe = None
    if isinstance(series, (list, tuple)) and len(series) >= 2 and all(_finite(v) for v in series):
        try:
            from ml import validation
            sharpe = float(validation.sharpe_ratio(series)["pp"])
        except Exception as e:  # 계산 실패 = 증거 없음
            logger.warning("Sharpe 계산 실패 — 보류: %s", e)
    key = trial_key if trial_key else object()        # 지문 없음 = 구별 불가한 새 시도
    if key not in trials:
        trials[key] = sharpe if sharpe is not None and math.isfinite(sharpe) else None
    known = [float(v) for v in trials.values() if _finite(v)]
    n_trials = legacy + len(trials)
    out.update(n_trials=n_trials, n_trial_sharpes=len(known))
    if sharpe is not None and math.isfinite(sharpe):
        out["trial_sharpe"] = sharpe
        out["sharpe_pp"] = round(sharpe, 6)
    if not isinstance(series, (list, tuple)) or len(series) < 2:
        out["reason"] = "evidence_missing"
        return out
    if (not all(_finite(v) for v in series) or not isinstance(n_ind, int) or isinstance(n_ind, bool)
            or n_ind != len(series)):
        out["reason"] = "evidence_invalid"
        return out
    out["n_independent"] = n_ind
    if out["trial_sharpe"] is None:
        out["reason"] = "stat_failed"
        return out
    if n_ind < ADOPT_MIN_INDEPENDENT:
        out["reason"] = "insufficient_independent"
        return out
    if n_trials > 1:
        if len(known) < TRIAL_MIN_SHARPES or len(known) < TRIAL_MIN_COVERAGE * n_trials:
            out["reason"] = "trial_evidence_insufficient"
            return out
        if statistics.stdev(known) < TRIAL_MIN_SR_STD:
            out["reason"] = "trial_variance_degenerate"
            return out
    try:
        from ml import validation
        psr = validation.deflated_sharpe_ratio(series, n_trials=n_trials,
                                               trial_sharpes=known if n_trials > 1 else None)
    except Exception as e:      # 계산 실패 = 증거 없음
        logger.warning("채택 통계 계산 실패 — 보류: %s", e)
        out["reason"] = "stat_failed"
        return out
    if psr is None or not math.isfinite(psr):
        out["reason"] = "stat_failed"
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


def refit_and_adopt(rows: list[dict], policy, fit_fn, eval_fn, *, index_mdd: float | None = None,
                    min_samples: int = 40, train_frac: float = 0.6, embargo: int = 20,
                    trial_log: dict | None = None) -> dict:
    """walk-forward 재적합 + ★목적함수 OOS 게이트 + 통계 게이트로 정책 채택 결정.

    rows:    학습행(각 dict 에 'date' 포함; decision⋈outcome).
    policy:  ml.adaptive.Policy — 현행(챔피언) 파라미터 소스이자 채택 대상.
    fit_fn:  (train_rows) -> candidate_params(dict).
    eval_fn: (oos_rows, params) -> {"excess": float, "mdd": float(양수)|None, "n": int(평가 단위 수),
             "series": [비중첩 OOS 관측 초과수익], "n_independent": int(=len(series)),
             선택 "index_mdd": 같은 보유 날짜의 지수 MDD}. series 가 없으면 통계 증거 누락으로 보류.
    index_mdd: eval_fn 이 index_mdd 를 내지 않을 때의 지수 MDD(양수) — MDD 제약 기준.
             eval_fn 이 index_mdd 키를 내면 그 값(같은 보유 날짜)이 우선이며 None 이면 위험 증거 없음.
    trial_log: 이 표면의 시도 이력(trial_history). 이번 시도 지문은 (후보, OOS 행 집합)으로 만든다.

    반환: {"adopted": bool, "reason": str, "challenger": {...}, "champion": {...}, "evidence": {...},
          "risk_index_mdd": float|None}.
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

    # 위험 증거: 챌린저 MDD 와 같은 보유 날짜의 지수 MDD 가 모두 산출돼야 MDD 제약을 판정할 수 있다.
    risk_idx = chal.get("index_mdd") if "index_mdd" in chal else index_mdd
    risk_ok = _finite(chal.get("mdd")) and _finite(risk_idx) and risk_idx >= 0
    # 총표본 게이트는 위 len(rows)>=min_samples 로 이미 적용. should_adopt 의 n 은 *OOS 평가 단위* 수이므로
    # train_frac 분할 후 OOS 가 min_samples 에 못 미쳐 영구 미채택되는 문제를 피해 별도(작은) 임계값 사용.
    oos_min = max(10, min_samples // 3)
    objective_ok = bool(risk_ok and _reward.should_adopt(chal, champ, risk_idx, min_samples=oos_min))
    evidence = adoption_evidence(chal, trial_log=trial_log,
                                 trial_key=trial_fingerprint(candidate, oos_rows))
    adopt = bool(objective_ok and evidence["ok"])
    stat_note = (f"PSR {evidence['psr']} · 독립 {evidence['n_independent']} · 시도 {evidence['n_trials']}"
                 if evidence.get("psr") is not None else
                 f"{evidence['reason']} · 독립 {evidence['n_independent']} · 시도 {evidence['n_trials']}")
    if adopt:
        policy.save(candidate, meta={"oos_excess": chal.get("excess"), "oos_mdd": chal.get("mdd"),
                                     "index_mdd": risk_idx, "mdd_basis": chal.get("mdd_basis"),
                                     "n_oos": chal.get("n"),
                                     "n_independent": evidence["n_independent"],
                                     "psr": evidence["psr"], "n_trials": evidence["n_trials"],
                                     "trial_key": evidence["trial_key"]})
        reason = (f"채택 ✅ OOS 초과수익 {chal.get('excess'):+.4f}>{champ.get('excess'):+.4f} "
                  f"· MDD {chal.get('mdd'):.3f}≤지수 {risk_idx:.3f} · {stat_note}")
    elif not risk_ok:
        reason = (f"보류 ⏸️ 위험 증거 없음 — 보유기간 경로 MDD 산출 불가 "
                  f"(MDD {chal.get('mdd')} · 지수 {risk_idx} · 기준 {chal.get('mdd_basis')} · {stat_note})")
    elif objective_ok:
        reason = (f"보류 ⏸️ 통계 게이트 미통과 (OOS 초과수익 {chal.get('excess'):+.4f} "
                  f"vs {champ.get('excess'):+.4f} · {stat_note})")
    else:
        reason = (f"보류 ⏸️ (OOS 초과수익 {chal.get('excess'):+.4f} vs {champ.get('excess'):+.4f} "
                  f"· MDD {chal.get('mdd'):.3f} vs 지수 {risk_idx:.3f})")
    return {"adopted": adopt, "reason": reason, "challenger": chal, "champion": champ,
            "candidate_params": candidate, "evidence": evidence,
            "risk_index_mdd": risk_idx if _finite(risk_idx) else None}
