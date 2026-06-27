#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ml/validation.py — 백테스트 검증 formalism (Tier 2 · López de Prado).

"백테스트 Sharpe가 진짜 엣지냐, 다중검정·과적합의 환상이냐"를 통계로 판정.
공격 엔진(Tier 3~6)을 라이브에 올리기 전 게이트. **판정·표시 전용 — 전략/배분 불변.**

수렴 도구:
- PSR (Probabilistic Sharpe Ratio): 표본길이·왜도·첨도 보정한 Sharpe>SR* 확률.
- DSR (Deflated Sharpe Ratio): N회 시도(다중검정)의 기대 최대 Sharpe(SR0)를 벤치마크로 쓴 PSR.
- PBO (Probability of Backtest Overfitting, CSCV): config 선택이 과적합인지(IS-best가 OOS서 평범).
- Purged/Embargoed K-Fold: 라벨 겹침 누설 차단 시계열 CV.

규약(중요):
- PSR/DSR의 SR 은 **per-period(비연율)**, T=관측수. skew/kurt 도 per-period 수익률.
- kurtosis 는 **非초과(정규=3)** — scipy 기본 fisher=True(초과)와 다름 → 내부서 fisher=False 사용.
graceful: 데이터부족·config<2 → None.
"""
from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
_EMC = 0.5772156649015329          # Euler–Mascheroni γ


def _arr(x) -> np.ndarray:
    return np.asarray(x, dtype=float).ravel()


def _skew_kurt(returns) -> tuple[float, float]:
    """per-period 왜도·첨도(非초과, 정규=3). scipy fisher=False."""
    from scipy.stats import kurtosis as _kurt, skew as _sk
    r = _arr(returns)
    if len(r) < 3:
        return 0.0, 3.0
    return float(_sk(r, bias=False)), float(_kurt(r, fisher=False, bias=False))


# ══════════════════════════════════════════════════════════════════════
#  Sharpe / PSR / DSR
# ══════════════════════════════════════════════════════════════════════

def sharpe_ratio(returns) -> dict:
    """per-period + 연율 Sharpe (ddof=1·√252). std=0 → 0."""
    r = _arr(returns)
    if len(r) < 2:
        return {"pp": 0.0, "ann": 0.0}
    sd = float(r.std(ddof=1))
    pp = float(r.mean()) / sd if sd > 0 else 0.0
    return {"pp": pp, "ann": pp * (TRADING_DAYS ** 0.5)}


def probabilistic_sharpe_ratio(sr_pp: float, sr_star_pp: float, T: int,
                               skew: float, kurt: float) -> float:
    """PSR = Φ[(SR−SR*)·√(T−1) / √(1 − skew·SR + (kurt−1)/4·SR²)]. SR per-period, kurt 非초과."""
    from scipy.stats import norm
    if T is None or T < 2:
        return float("nan")
    denom = 1.0 - skew * sr_pp + (kurt - 1.0) / 4.0 * sr_pp ** 2
    denom = max(denom, 1e-12)
    z = (sr_pp - sr_star_pp) * math.sqrt(T - 1) / math.sqrt(denom)
    return float(norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """N회 무스킬 시도의 기대 최대 Sharpe SR0 = √V·[(1−γ)Φ⁻¹(1−1/N)+γΦ⁻¹(1−1/(Ne))].

    N≤1 (Φ⁻¹(0)=−∞) 또는 V≤0 → 0.0 (DSR 이 PSR(vs0)로 degrade)."""
    from scipy.stats import norm
    if n_trials is None or n_trials <= 1 or sr_variance is None or sr_variance <= 0:
        return 0.0
    z1 = norm.ppf(1.0 - 1.0 / n_trials)
    z2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(sr_variance) * ((1.0 - _EMC) * z1 + _EMC * z2))


def deflated_sharpe_ratio(returns, n_trials: int, sr_variance: float | None = None,
                          trial_sharpes=None) -> float | None:
    """DSR = PSR(SR0). returns 에서 sr_pp·skew·kurt 도출(오용 방지).

    V: sr_variance(per-period Sharpe 분산) 직접 or trial_sharpes 배열로 산출. 둘 다 없으면 None."""
    r = _arr(returns)
    if len(r) < 2:
        return None
    if sr_variance is None and trial_sharpes is not None:
        ts = _arr(trial_sharpes)
        sr_variance = float(ts.var(ddof=1)) if len(ts) > 1 else None
    if n_trials and n_trials > 1 and (sr_variance is None or sr_variance <= 0):
        return None
    sr_pp = sharpe_ratio(r)["pp"]
    skew, kurt = _skew_kurt(r)
    sr0 = expected_max_sharpe(n_trials, sr_variance if sr_variance else 0.0)
    return probabilistic_sharpe_ratio(sr_pp, sr0, len(r), skew, kurt)


def min_track_record_length(sr_pp: float, sr_star_pp: float, skew: float, kurt: float,
                            p: float = 0.95) -> float:
    """PSR=p 달성에 필요한 최소 관측수 T*. SR≤SR* → inf."""
    from scipy.stats import norm
    if sr_pp <= sr_star_pp:
        return float("inf")
    zp = norm.ppf(p)
    var_term = 1.0 - skew * sr_pp + (kurt - 1.0) / 4.0 * sr_pp ** 2
    return float(1.0 + max(var_term, 0.0) * (zp / (sr_pp - sr_star_pp)) ** 2)


# ══════════════════════════════════════════════════════════════════════
#  PBO (Combinatorially Symmetric Cross-Validation)
# ══════════════════════════════════════════════════════════════════════

def _col_sharpe(M: np.ndarray) -> np.ndarray:
    """열(config)별 per-period Sharpe (mean/std, std=0 보호)."""
    mu = M.mean(axis=0)
    sd = M.std(axis=0, ddof=1)
    return mu / (sd + 1e-12)


def pbo_cscv(perf_matrix, n_splits: int = 10):
    """과적합확률. perf_matrix: T×N per-period 수익률(열=config). N<2 → None.

    S(짝수≤14) 블록 → C(S,S/2) IS/OOS 조합. 각 조합: IS-best config 의 OOS 상대순위
    ω=rank/(N+1)(1=최저), logit λ=ln(ω/(1−ω)). PBO = P(λ≤0) = IS-best 가 OOS 중앙값 이하 비율.
    """
    import itertools
    M = np.asarray(perf_matrix, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2:
        return None
    T, N = M.shape
    S = int(n_splits)
    if S % 2 != 0:
        S -= 1
    if S < 2 or S > 14 or T < S:
        return None
    blocks = np.array_split(np.arange(T), S)
    logits = []
    for combo in itertools.combinations(range(S), S // 2):
        is_rows = np.concatenate([blocks[b] for b in combo])
        oos_rows = np.concatenate([blocks[b] for b in range(S) if b not in combo])
        sr_is = _col_sharpe(M[is_rows])
        sr_oos = _col_sharpe(M[oos_rows])
        n_star = int(np.argmax(sr_is))
        rank = int(np.sum(sr_oos <= sr_oos[n_star]))      # 1..N (1=최저)
        omega = rank / (N + 1.0)
        logits.append(math.log(omega / (1.0 - omega)))
    logits = np.array(logits)
    return {"pbo": float(np.mean(logits <= 0.0)), "logits": logits,
            "n_splits": S, "n_configs": N, "n_combos": len(logits)}


# ══════════════════════════════════════════════════════════════════════
#  Purged & Embargoed K-Fold (라벨 누설 차단)
# ══════════════════════════════════════════════════════════════════════

def purged_kfold_indices(n: int, n_splits: int = 5, label_horizon: int = 0,
                         embargo: int = 0):
    """시계열 CV — 각 test 폴드의 [t0−H, t1+embargo] 와 겹치는 train 표본을 제거.

    반환 list[(train_idx, test_idx)]. (ml/ranker 날짜 퍼지 idiom 의 인덱스 일반화)
    """
    idx = np.arange(n)
    folds = np.array_split(idx, n_splits)
    out = []
    for test in folds:
        if len(test) == 0:
            continue
        t0, t1 = int(test[0]), int(test[-1])
        lo, hi = t0 - label_horizon, t1 + embargo
        train = np.array([i for i in idx if (i < lo or i > hi) and i not in set(test.tolist())])
        out.append((train, test))
    return out


# ══════════════════════════════════════════════════════════════════════
#  편의 오케스트레이터
# ══════════════════════════════════════════════════════════════════════

def validate_strategy(returns, benchmark_returns=None, n_trials: int = 1,
                      sr_variance: float | None = None) -> dict | None:
    """전략 검증 요약. benchmark 주입 시 초과수익(returns−benchmark) PSR 로 '벤치마크 이김?' 판정.

    n_trials>1 + sr_variance → DSR(다중검정 deflate). 데이터부족 → None.
    """
    r = _arr(returns)
    if len(r) < 2:
        return None
    sr = sharpe_ratio(r)
    skew, kurt = _skew_kurt(r)
    out = {
        "sharpe": round(sr["ann"], 3),
        "sharpe_pp": sr["pp"],
        "psr": round(probabilistic_sharpe_ratio(sr["pp"], 0.0, len(r), skew, kurt), 4),
        "n_trials": n_trials,
        "n_obs": len(r),
    }
    if benchmark_returns is not None:
        b = _arr(benchmark_returns)
        m = min(len(r), len(b))
        excess = r[-m:] - b[-m:]                       # 초과수익 시리즈
        if m >= 2:
            esr = sharpe_ratio(excess)["pp"]
            es, ek = _skew_kurt(excess)
            out["psr_excess"] = round(probabilistic_sharpe_ratio(esr, 0.0, m, es, ek), 4)
            out["excess_sharpe"] = round(sharpe_ratio(excess)["ann"], 3)
    dsr = deflated_sharpe_ratio(r, n_trials, sr_variance) if (n_trials and n_trials > 1) else None
    out["dsr"] = (None if dsr is None else round(dsr, 4))
    return out
