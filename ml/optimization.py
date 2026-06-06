"""p8 — Hyper-parameter optimization skeleton.

Two backends (first available wins):
  1. Optuna  — full TPE search via optimize_parameters()
  2. Built-in grid search — deterministic, no extra dependencies

Public API
----------
optuna_available()                    — True if optuna is importable
composite_score(cagr, mdd, turnover, excess_return)  — single optimisation objective
optimize_parameters(objective_factory, param_space, n_trials)
grid_search_parameters(objective_fn, param_grid)      — always available fallback
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Callable, Optional

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def optuna_available() -> bool:
    """Return True if optuna is importable."""
    return _OPTUNA_AVAILABLE


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def composite_score(
    cagr: Optional[float],
    max_drawdown: float,
    turnover: float,
    excess_return: float,
    cagr_weight: float = 1.0,
    mdd_penalty: float = 1.5,
    turnover_penalty: float = 0.5,
    excess_weight: float = 0.5,
) -> float:
    """Combine performance metrics into a single optimisation objective (higher = better).

    Args:
        cagr: Compound annual growth rate (e.g. 0.12 → 12%). None treated as 0.
        max_drawdown: Maximum drawdown (negative, e.g. -0.25 → -25%).
        turnover: Mean daily weight change (positive).
        excess_return: Return above benchmark (positive = outperformance).
        cagr_weight: Scaling factor for CAGR contribution.
        mdd_penalty: Scaling factor for drawdown penalty.
        turnover_penalty: Scaling factor for turnover penalty.
        excess_weight: Scaling factor for benchmark excess return (default 0.5 to avoid
                       double-counting: CAGR already embeds absolute level, so excess_return
                       adds an incremental relative bonus rather than equal weight).

    Returns:
        Scalar score. Invalid inputs (NaN/Inf) return -inf.
    """
    cagr_val = cagr if cagr is not None else 0.0
    score = (
        cagr_weight * cagr_val
        + mdd_penalty * max_drawdown        # mdd < 0, so this subtracts
        - turnover_penalty * abs(turnover)
        + excess_weight * excess_return     # partial weight reduces CAGR double-counting
    )
    if math.isnan(score) or math.isinf(score):
        return float("-inf")
    return float(score)


# ---------------------------------------------------------------------------
# Optuna optimiser
# ---------------------------------------------------------------------------

def optimize_parameters(
    objective_factory: Callable[..., Callable[["Any"], float]],
    param_space: dict,
    n_trials: int = 30,
    direction: str = "maximize",
    seed: int = 42,
) -> dict:
    """Run Optuna TPE search over param_space.

    Args:
        objective_factory: Callable that receives a dict of hyper-parameters
                           and returns the score (float).
        param_space: Dict mapping param name → (type, low, high[, step]) or
                     (type, choices) where type is 'float', 'int', or 'categorical'.
                     Example::

                       {
                           "lr":       ("float", 1e-4, 1e-1),
                           "n_leaves": ("int", 4, 64),
                           "loss":     ("categorical", ["l1", "l2"]),
                       }
        n_trials: Number of Optuna trials.
        direction: 'maximize' or 'minimize'.
        seed: Random seed for reproducibility.

    Returns:
        dict with keys 'best_params', 'best_value', 'n_trials'.

    Raises:
        RuntimeError: If optuna is not installed.
    """
    if not _OPTUNA_AVAILABLE:
        raise RuntimeError(
            "optuna is not installed. Install it with: pip install optuna  "
            "or use grid_search_parameters() instead."
        )

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction=direction, sampler=sampler)

    def _objective(trial: "optuna.Trial") -> float:
        params: dict[str, Any] = {}
        for name, spec in param_space.items():
            kind = spec[0]
            if kind == "float":
                params[name] = trial.suggest_float(name, spec[1], spec[2])
            elif kind == "int":
                params[name] = trial.suggest_int(name, spec[1], spec[2])
            elif kind == "categorical":
                params[name] = trial.suggest_categorical(name, spec[1])
            else:
                raise ValueError(f"Unknown param type '{kind}' for '{name}'")
        return objective_factory(params)

    study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)
    return {
        "best_params": study.best_params,
        "best_value": study.best_value,
        "n_trials": len(study.trials),
    }


# ---------------------------------------------------------------------------
# Deterministic grid search (always available)
# ---------------------------------------------------------------------------

def grid_search_parameters(
    objective_fn: Callable[[dict], float],
    param_grid: dict[str, list],
    direction: str = "maximize",
) -> dict:
    """Exhaustive grid search over a small param_grid dict.

    Args:
        objective_fn: Callable (params dict) → score (float).
        param_grid: Dict mapping param name → list of values to try.
                    Example: {"lr": [0.01, 0.1], "depth": [3, 5]}
        direction: 'maximize' (pick highest score) or 'minimize' (pick lowest).

    Returns:
        dict with keys 'best_params', 'best_value', 'n_trials'.
    """
    if not param_grid:
        return {"best_params": {}, "best_value": float("-inf"), "n_trials": 0}

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    best_params: dict = {}
    best_value: float = float("-inf") if direction == "maximize" else float("inf")
    n_trials = 0

    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        score = objective_fn(params)
        n_trials += 1
        if direction == "maximize" and score > best_value:
            best_value = score
            best_params = dict(params)
        elif direction == "minimize" and score < best_value:
            best_value = score
            best_params = dict(params)

    return {
        "best_params": best_params,
        "best_value": best_value,
        "n_trials": n_trials,
    }
