"""Strategy stress and bootstrap risk helpers."""
from __future__ import annotations

import numpy as np


def _as_returns(returns) -> np.ndarray:
    arr = np.asarray(returns, dtype=float).ravel()
    return arr[np.isfinite(arr)]


def _max_losing_streak(path: np.ndarray) -> int:
    best = cur = 0
    for ret in path:
        if ret < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def cost_stress(returns, *, turnover: float, base_cost_bps: float,
                multipliers=(1, 2, 3)) -> list[dict]:
    r = _as_returns(returns)
    out: list[dict] = []
    turnover = max(0.0, float(turnover))
    base_cost = max(0.0, float(base_cost_bps)) / 10_000.0
    for mult in multipliers:
        m = max(0.0, float(mult))
        drag_per_period = turnover * base_cost * m
        stressed = r - drag_per_period
        out.append({
            "multiplier": m,
            "cost_drag": round(float(drag_per_period * len(r)), 6),
            "total_return": round(float(np.prod(1.0 + stressed) - 1.0), 6) if len(stressed) else 0.0,
            "mean_return": round(float(stressed.mean()), 6) if len(stressed) else 0.0,
        })
    return out


def bootstrap_risk(returns, *, n_paths: int = 1000, horizon: int = 60,
                   seed: int | None = None) -> dict:
    r = _as_returns(returns)
    n_paths = max(1, int(n_paths))
    horizon = max(1, int(horizon))
    if len(r) == 0:
        return {
            "paths": n_paths,
            "horizon": horizon,
            "p05_nav": 1.0,
            "median_nav": 1.0,
            "p95_nav": 1.0,
            "max_losing_streak_p95": 0,
        }
    rng = np.random.default_rng(seed)
    samples = rng.choice(r, size=(n_paths, horizon), replace=True)
    nav = np.prod(1.0 + samples, axis=1)
    streaks = np.array([_max_losing_streak(path) for path in samples], dtype=float)
    return {
        "paths": n_paths,
        "horizon": horizon,
        "p05_nav": round(float(np.quantile(nav, 0.05)), 6),
        "median_nav": round(float(np.quantile(nav, 0.50)), 6),
        "p95_nav": round(float(np.quantile(nav, 0.95)), 6),
        "max_losing_streak_p95": int(np.ceil(float(np.quantile(streaks, 0.95)))),
    }
