"""Cost stress and bootstrap risk primitives."""

from ml.strategy_risk import bootstrap_risk, cost_stress


def test_cost_stress_marks_strategy_fragile_when_cost_multiplier_erases_edge():
    base_returns = [0.002] * 100
    stressed = cost_stress(base_returns, turnover=0.8, base_cost_bps=10.0, multipliers=(1, 3))

    assert stressed[0]["multiplier"] == 1
    assert stressed[0]["total_return"] > stressed[1]["total_return"]
    assert stressed[1]["cost_drag"] > stressed[0]["cost_drag"]


def test_bootstrap_risk_reports_worst_path_and_losing_streak():
    returns = [0.01, -0.02, 0.015, -0.01, 0.005] * 20

    risk = bootstrap_risk(returns, n_paths=200, horizon=40, seed=7)

    assert risk["paths"] == 200
    assert risk["horizon"] == 40
    assert 0 < risk["p05_nav"] < risk["median_nav"]
    assert risk["max_losing_streak_p95"] >= 1
