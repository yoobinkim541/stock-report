"""Common GO/OBSERVE/NO-GO gate for strategy research results."""
from __future__ import annotations

import numpy as np

# DSR 하한(다중시도 보정 후 Sharpe>SR0 확률). n_trials>1 일 때만 적용.
MIN_DSR = 0.50


def _nav_from_returns(returns) -> list[float]:
    nav = [1.0]
    for ret in np.asarray(returns, dtype=float).ravel():
        nav.append(nav[-1] * (1.0 + float(ret)))
    return nav


def _paired(returns, benchmark_returns):
    """전략·벤치마크를 같은 관측끼리 정렬하고 **양쪽 모두 유한한 행만** 남긴다.

    둘 다 pandas Series 면 index 교집합으로 정렬한다. 위치 배열(list/ndarray)은 길이가
    같아야 같은 행이 같은 기간이라고 볼 수 있으므로, 길이가 다르면 정렬을 거부한다.
    반환: (r, b, reason). reason 이 있으면 정렬 불가.
    """
    import pandas as pd

    if benchmark_returns is None:
        r = np.asarray(returns, dtype=float).ravel()
        return r[np.isfinite(r)], None, None
    if isinstance(returns, pd.Series) and isinstance(benchmark_returns, pd.Series):
        joined = pd.concat([returns.rename("r"), benchmark_returns.rename("b")], axis=1, join="inner")
        r = joined["r"].to_numpy(dtype=float)
        b = joined["b"].to_numpy(dtype=float)
    else:
        r = np.asarray(returns, dtype=float).ravel()
        b = np.asarray(benchmark_returns, dtype=float).ravel()
        if len(r) != len(b):
            return r[:0], b[:0], "benchmark_misaligned"
    mask = np.isfinite(r) & np.isfinite(b)
    return r[mask], b[mask], None


def strategy_gate(returns, benchmark_returns=None, *, n_trials: int = 1,
                  min_samples: int = 60, trial_sharpes=None,
                  sr_variance: float | None = None) -> dict:
    """전략 수익률 → GO/OBSERVE/NO-GO.

    - 최소 표본은 벤치마크와 **pairwise 정렬·결측 제거 후** 남은 공통 표본에 적용한다.
    - n_trials>1 이면 DSR 로 다중시도를 보정한다. 시도별 Sharpe 분산(trial_sharpes 또는
      sr_variance)이 없으면 DSR 을 만들 수 없으므로 'dsr_unavailable' → NO-GO
      (합성 분산으로 통과시키지 않는다).
    """
    from ml import validation
    from ml.adaptive import reward

    r, b, align_reason = _paired(returns, benchmark_returns)
    reasons: list[str] = []
    if align_reason:
        return {
            "verdict": "NO-GO",
            "reasons": [align_reason],
            "metrics": {"n": 0, "min_samples": int(min_samples)},
        }
    if len(r) < int(min_samples):
        reasons.append("min_samples")
        return {
            "verdict": "NO-GO",
            "reasons": reasons,
            "metrics": {"n": int(len(r)), "min_samples": int(min_samples)},
        }

    cum = float(np.prod(1.0 + r) - 1.0)
    bench_cum = float(np.prod(1.0 + b) - 1.0) if b is not None else 0.0
    excess = cum - bench_cum
    mdd = abs(float(reward.max_drawdown(_nav_from_returns(r))))
    bench_mdd = abs(float(reward.max_drawdown(_nav_from_returns(b)))) if b is not None else None
    sr = validation.sharpe_ratio(r)
    dsr = None
    if n_trials and n_trials > 1:
        dsr = validation.deflated_sharpe_ratio(r, n_trials=n_trials, sr_variance=sr_variance,
                                               trial_sharpes=trial_sharpes)
        if dsr is None or not np.isfinite(dsr):
            dsr = None
            reasons.append("dsr_unavailable")

    if excess <= 0:
        reasons.append("excess")
    if dsr is not None and dsr < MIN_DSR:
        reasons.append("dsr")
    if bench_mdd is not None and mdd > max(bench_mdd * 1.10, 0.10):
        reasons.append("drawdown")

    if not reasons:
        verdict = "GO"
    elif {"excess", "dsr", "dsr_unavailable"} & set(reasons):
        verdict = "NO-GO"
    else:
        verdict = "OBSERVE"
    return {
        "verdict": verdict,
        "reasons": reasons,
        "metrics": {
            "n": int(len(r)),
            "return": round(cum, 6),
            "benchmark_return": round(bench_cum, 6),
            "excess_return": round(excess, 6),
            "mdd": round(mdd, 6),
            "benchmark_mdd": round(bench_mdd, 6) if bench_mdd is not None else None,
            "sharpe_ann": round(float(sr["ann"]), 6),
            "dsr": round(float(dsr), 6) if dsr is not None else None,
        },
    }
