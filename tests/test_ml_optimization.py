import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.optimization import (
    composite_score,
    grid_search_parameters,
    optuna_available,
    optimize_parameters,
)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def test_optuna_available_returns_bool():
    assert isinstance(optuna_available(), bool)


# ---------------------------------------------------------------------------
# composite_score
# ---------------------------------------------------------------------------

def test_composite_score_basic():
    score = composite_score(cagr=0.12, max_drawdown=-0.15, turnover=0.02, excess_return=0.03)
    assert isinstance(score, float)
    assert score != float("-inf")  # valid inputs produce a finite score


def test_composite_score_none_cagr():
    score = composite_score(cagr=None, max_drawdown=-0.10, turnover=0.01, excess_return=0.0)
    assert isinstance(score, float)
    assert score != float("-inf")


def test_composite_score_bad_inputs_return_neginf():
    import math
    score = composite_score(cagr=float("nan"), max_drawdown=0.0, turnover=0.0, excess_return=0.0)
    assert score == float("-inf")


def test_composite_score_penalises_large_drawdown():
    good = composite_score(cagr=0.10, max_drawdown=-0.05, turnover=0.01, excess_return=0.02)
    bad = composite_score(cagr=0.10, max_drawdown=-0.50, turnover=0.01, excess_return=0.02)
    assert good > bad


def test_composite_score_penalises_high_turnover():
    low_t = composite_score(cagr=0.10, max_drawdown=-0.10, turnover=0.01, excess_return=0.02)
    high_t = composite_score(cagr=0.10, max_drawdown=-0.10, turnover=0.50, excess_return=0.02)
    assert low_t > high_t


# ---------------------------------------------------------------------------
# grid_search_parameters (always available)
# ---------------------------------------------------------------------------

def test_grid_search_finds_best_param():
    def objective(params):
        return -(params["x"] - 3) ** 2  # maximum at x=3

    result = grid_search_parameters(
        objective_fn=objective,
        param_grid={"x": [1, 2, 3, 4, 5]},
        direction="maximize",
    )
    assert result["best_params"]["x"] == 3
    assert result["best_value"] == pytest.approx(0.0)
    assert result["n_trials"] == 5


def test_grid_search_minimize():
    def objective(params):
        return (params["x"] - 2) ** 2

    result = grid_search_parameters(
        objective_fn=objective,
        param_grid={"x": [0, 1, 2, 3]},
        direction="minimize",
    )
    assert result["best_params"]["x"] == 2
    assert result["best_value"] == pytest.approx(0.0)


def test_grid_search_multi_param():
    def objective(params):
        return params["a"] + params["b"]

    result = grid_search_parameters(
        objective_fn=objective,
        param_grid={"a": [1, 2], "b": [10, 20]},
        direction="maximize",
    )
    assert result["best_params"] == {"a": 2, "b": 20}
    assert result["n_trials"] == 4


def test_grid_search_empty_grid():
    result = grid_search_parameters(lambda p: 0.0, param_grid={})
    assert result["n_trials"] == 0
    assert result["best_params"] == {}


# ---------------------------------------------------------------------------
# optimize_parameters (skip if optuna not installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not optuna_available(), reason="optuna not installed")
def test_optimize_parameters_finds_optimum():
    def factory(params):
        return -(params["x"] - 3.0) ** 2

    result = optimize_parameters(
        objective_factory=factory,
        param_space={"x": ("float", 0.0, 6.0)},
        n_trials=20,
        seed=0,
    )
    assert abs(result["best_params"]["x"] - 3.0) < 0.5
    assert result["n_trials"] == 20


@pytest.mark.skipif(not optuna_available(), reason="optuna not installed")
def test_optimize_parameters_int_and_categorical():
    def factory(params):
        return float(params["n"] == 5) + float(params["mode"] == "fast")

    result = optimize_parameters(
        objective_factory=factory,
        param_space={
            "n": ("int", 1, 10),
            "mode": ("categorical", ["slow", "fast"]),
        },
        n_trials=30,
        seed=1,
    )
    assert result["best_params"]["n"] == 5
    assert result["best_params"]["mode"] == "fast"


def test_optimize_parameters_raises_without_optuna(monkeypatch):
    import ml.optimization as optmod
    monkeypatch.setattr(optmod, "_OPTUNA_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="optuna"):
        optimize_parameters(lambda p: 0.0, param_space={"x": ("float", 0.0, 1.0)}, n_trials=1)


# ---------------------------------------------------------------------------
# Grid search used as optuna fallback (no optuna dependency)
# ---------------------------------------------------------------------------

def test_grid_search_as_optimization_fallback():
    """Demonstrate using grid_search as a drop-in when optuna is absent."""
    param_grid = {"lr": [0.01, 0.1], "depth": [3, 5]}

    def objective(params):
        return params["lr"] * 10 + params["depth"]

    result = grid_search_parameters(objective, param_grid=param_grid, direction="maximize")
    assert result["best_params"]["lr"] == 0.1
    assert result["best_params"]["depth"] == 5
