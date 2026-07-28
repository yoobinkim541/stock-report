"""Common strategy validation gate."""

from ml.strategy_gate import strategy_gate


def test_strategy_gate_rejects_small_samples():
    result = strategy_gate([0.01] * 10, min_samples=60)

    assert result["verdict"] == "NO-GO"
    assert "min_samples" in result["reasons"]


def test_strategy_gate_go_for_positive_excess_with_controlled_drawdown():
    returns = [0.002, -0.001, 0.003, 0.001] * 30
    benchmark = [0.001, -0.001, 0.001, 0.0] * 30

    result = strategy_gate(returns, benchmark, min_samples=60, n_trials=1)

    assert result["verdict"] == "GO"
    assert result["metrics"]["n"] == 120
    assert result["metrics"]["excess_return"] > 0


def test_strategy_gate_observe_when_drawdown_is_much_worse_than_benchmark():
    returns = [0.01] * 70 + [-0.20] + [0.01] * 70
    benchmark = [0.001] * len(returns)

    result = strategy_gate(returns, benchmark, min_samples=60, n_trials=1)

    assert result["verdict"] == "OBSERVE"
    assert "drawdown" in result["reasons"]
