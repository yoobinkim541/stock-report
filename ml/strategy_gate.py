"""Common GO/OBSERVE/NO-GO gate for strategy research results."""
from __future__ import annotations

import numpy as np


def _nav_from_returns(returns) -> list[float]:
    nav = [1.0]
    for ret in np.asarray(returns, dtype=float).ravel():
        nav.append(nav[-1] * (1.0 + float(ret)))
    return nav


def strategy_gate(returns, benchmark_returns=None, *, n_trials: int = 1,
                  min_samples: int = 60) -> dict:
    from ml import validation
    from ml.adaptive import reward

    r = np.asarray(returns, dtype=float).ravel()
    r = r[np.isfinite(r)]
    reasons: list[str] = []
    if len(r) < int(min_samples):
        reasons.append("min_samples")
        return {
            "verdict": "NO-GO",
            "reasons": reasons,
            "metrics": {"n": int(len(r)), "min_samples": int(min_samples)},
        }

    b = None
    if benchmark_returns is not None:
        b = np.asarray(benchmark_returns, dtype=float).ravel()
        b = b[np.isfinite(b)]
        n = min(len(r), len(b))
        r, b = r[-n:], b[-n:]

    cum = float(np.prod(1.0 + r) - 1.0)
    bench_cum = float(np.prod(1.0 + b) - 1.0) if b is not None and len(b) else 0.0
    excess = cum - bench_cum
    mdd = abs(float(reward.max_drawdown(_nav_from_returns(r))))
    bench_mdd = abs(float(reward.max_drawdown(_nav_from_returns(b)))) if b is not None and len(b) else None
    sr = validation.sharpe_ratio(r)
    dsr = validation.deflated_sharpe_ratio(r, n_trials=n_trials, trial_sharpes=[sr["pp"], 0.0]) \
        if n_trials and n_trials > 1 else None

    if excess <= 0:
        reasons.append("excess")
    if dsr is not None and dsr < 0.50:
        reasons.append("dsr")
    if bench_mdd is not None and mdd > max(bench_mdd * 1.10, 0.10):
        reasons.append("drawdown")

    if not reasons:
        verdict = "GO"
    elif "excess" in reasons or "dsr" in reasons:
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
