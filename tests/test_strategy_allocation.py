import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.strategy_studio import StrategySpec, run_strategy_backtest
from ml.strategy_studio.allocation import (
    allocate_targets,
    estimate_shrunk_covariance,
)
from ml.strategy_studio.signals import SignalPanel
from ml.optimization import cost_aware_objective


def test_allocate_targets_obeys_position_gross_and_turnover_limits():
    idx = pd.date_range("2026-01-01", periods=3)
    scores = pd.DataFrame({"A": [1.0, 1.0, -1.0], "B": [0.0, 1.0, 1.0]}, index=idx)
    returns = pd.DataFrame({"A": [0.01, -0.02, 0.01], "B": [0.02, 0.01, -0.01]}, index=idx)

    result = allocate_targets(
        SignalPanel.from_score("factor", scores, confidence=1.0),
        returns,
        {
            "optimizer": "cost_aware_risk_budget",
            "max_position_pct": 0.6,
            "max_gross_exposure": 1.0,
            "max_turnover": 0.25,
            "target_volatility": 0.2,
        },
        {"fees_bps": 5, "slippage_bps": 10, "spread_bps": 5},
    )

    assert (result.weights.abs().sum(axis=1) <= 1.0 + 1e-9).all()
    assert (result.weights.abs() <= 0.6 + 1e-9).all().all()
    assert "turnover_limit" in {diagnostic["type"] for diagnostic in result.diagnostics}


def test_shrunk_covariance_is_symmetric_positive_semidefinite():
    returns = pd.DataFrame({"A": [0.01, 0.02, -0.01], "B": [0.02, 0.01, -0.02]})

    covariance = estimate_shrunk_covariance(returns)

    assert covariance.equals(covariance.T)
    assert np.linalg.eigvalsh(covariance.to_numpy()).min() >= -1e-10


def test_cost_aware_objective_applies_risk_turnover_and_cost_penalties():
    objective = cost_aware_objective(
        scores=np.array([1.0, 0.5]),
        weights=np.array([0.4, 0.2]),
        covariance=np.diag([0.1, 0.2]),
        previous_weights=np.array([0.1, 0.1]),
        risk_aversion=2.0,
        turnover_penalty=0.3,
        cost_bps=20.0,
    )

    expected = (
        1.0 * 0.4 + 0.5 * 0.2
        - 2.0 * (0.4**2 * 0.1 + 0.2**2 * 0.2)
        - 0.3 * (abs(0.4 - 0.1) + abs(0.2 - 0.1))
        - 20.0 * (abs(0.4 - 0.1) + abs(0.2 - 0.1)) / 10000.0
    )

    assert objective == pytest.approx(expected)


def test_allocator_zeroes_invalid_and_low_confidence_scores_with_diagnostics():
    idx = pd.date_range("2026-01-01", periods=2)
    panel = SignalPanel(
        "factor",
        pd.DataFrame({"A": [1.0, np.nan], "B": [1.0, 1.0]}, index=idx),
        pd.DataFrame({"A": [0.4, np.nan], "B": [0.9, 0.9]}, index=idx),
    )
    returns = pd.DataFrame({"A": [0.01, 0.01], "B": [0.01, 0.01]}, index=idx)

    result = allocate_targets(
        panel,
        returns,
        {"max_position_pct": 1.0, "min_confidence": 0.5},
        {},
    )

    assert result.weights.loc[idx[0], "A"] == pytest.approx(0.0)
    assert result.weights.loc[idx[0], "B"] != pytest.approx(0.0)
    assert result.weights.loc[idx[1], "A"] == pytest.approx(0.0)
    assert any(diagnostic["constraint"] == "min_confidence" for diagnostic in result.diagnostics)
    assert any(diagnostic["constraint"] == "valid_signal" for diagnostic in result.diagnostics)


def test_allocator_reports_cost_and_objective_diagnostics():
    idx = pd.date_range("2026-01-01", periods=2)
    panel = SignalPanel.from_score(
        "factor",
        pd.DataFrame({"A": [1.0, 0.0]}, index=idx),
        confidence=1.0,
    )
    returns = pd.DataFrame({"A": [0.01, 0.02]}, index=idx)

    result = allocate_targets(
        panel,
        returns,
        {"optimizer": "cost_aware_risk_budget", "max_turnover": 1.0},
        {"fees_bps": 5, "slippage_bps": 10, "spread_bps": 5},
    )

    types = {diagnostic["type"] for diagnostic in result.diagnostics}
    assert "objective" in types
    assert "transaction_cost" in types
    cost_rows = [d for d in result.diagnostics if d["type"] == "transaction_cost"]
    assert cost_rows[0]["cost_bps"] == pytest.approx(20.0)
    assert cost_rows[0]["estimated_cost"] >= 0.0


def test_allocator_scales_targets_to_target_volatility():
    idx = pd.date_range("2026-01-01", periods=8)
    panel = SignalPanel.from_score(
        "factor",
        pd.DataFrame({"A": [1.0] * len(idx)}, index=idx),
        confidence=1.0,
    )
    returns = pd.DataFrame({"A": [0.1, -0.1] * 4}, index=idx)

    result = allocate_targets(
        panel,
        returns,
        {"max_position_pct": 1.0, "target_volatility": 0.01},
        {},
    )

    covariance = estimate_shrunk_covariance(returns)
    realized_volatility = np.sqrt(
        result.weights["A"].iloc[-1] ** 2 * float(covariance.loc["A", "A"])
    )
    assert (result.weights.abs() <= 1.0 + 1e-9).all().all()
    assert realized_volatility <= 0.01 + 1e-9
    assert "target_volatility" in {diagnostic["type"] for diagnostic in result.diagnostics}


def test_point_in_time_covariance_ignores_returns_after_each_target_timestamp():
    target_index = pd.date_range("2026-01-01", periods=3)
    panel = SignalPanel.from_score(
        "factor",
        pd.DataFrame({"A": [1.0] * 3, "B": [1.0] * 3}, index=target_index),
        confidence=1.0,
    )
    returns = pd.DataFrame(
        {"A": [0.10, -0.10, 0.10], "B": [0.01, -0.01, 0.01]},
        index=target_index,
    )
    future_returns = pd.DataFrame(
        {"A": [0.001, 0.001], "B": [0.50, -0.50]},
        index=pd.date_range("2026-01-04", periods=2),
    )

    base = allocate_targets(panel, returns, {"optimizer": "risk_budget"}, {})
    extended = allocate_targets(
        panel,
        pd.concat([returns, future_returns]),
        {"optimizer": "risk_budget"},
        {},
    )

    pd.testing.assert_frame_equal(base.weights, extended.weights)


def test_string_false_allow_short_is_respected_and_final_weights_are_bounded():
    idx = pd.date_range("2026-01-01", periods=2)
    panel = SignalPanel.from_score(
        "factor",
        pd.DataFrame({"A": [1.0, -1.0], "B": [-1.0, 1.0]}, index=idx),
        confidence=1.0,
    )

    result = allocate_targets(
        panel,
        pd.DataFrame({"A": [0.01, -0.01], "B": [-0.01, 0.01]}, index=idx),
        {
            "optimizer": "equal_weight",
            "allow_short": "false",
            "max_position_pct": 0.2,
            "max_gross_exposure": 0.3,
            "max_turnover": 0.1,
            "previous_weights": {"A": 2.0, "B": -2.0},
        },
        {},
    )

    assert (result.weights >= -1e-12).all().all()
    assert (result.weights.abs() <= 0.2 + 1e-12).all().all()
    assert (result.weights.abs().sum(axis=1) <= 0.3 + 1e-12).all()
    assert any(diagnostic["constraint"] == "max_position_pct" for diagnostic in result.diagnostics)


def test_empty_returns_use_finite_psd_covariance_and_visible_fallback_diagnostic():
    idx = pd.date_range("2026-01-01", periods=1)
    returns = pd.DataFrame(columns=["A", "B"], index=idx, dtype="float64")

    covariance = estimate_shrunk_covariance(returns)
    result = allocate_targets(
        SignalPanel.from_score(
            "factor",
            pd.DataFrame({"A": [1.0], "B": [1.0]}, index=idx),
            confidence=1.0,
        ),
        returns,
        {"optimizer": "risk_budget"},
        {},
    )

    assert np.isfinite(covariance.to_numpy()).all()
    assert np.linalg.eigvalsh(covariance.to_numpy()).min() >= -1e-12
    assert any(diagnostic["type"] == "covariance_fallback" for diagnostic in result.diagnostics)
    assert any("covariance" in warning for warning in result.warnings)


def test_forced_covariance_fallback_is_visible(monkeypatch):
    import ml.strategy_studio.allocation as allocation_module

    monkeypatch.setattr(allocation_module, "LedoitWolf", None)
    returns = pd.DataFrame({"A": [0.01, -0.02], "B": [0.02, -0.01]})

    covariance = allocation_module.estimate_shrunk_covariance(returns)
    result = allocate_targets(
        SignalPanel.from_score("factor", pd.DataFrame({"A": [1.0]}, index=[0]), confidence=1.0),
        returns,
        {"optimizer": "risk_budget"},
        {},
    )

    assert np.isfinite(covariance.to_numpy()).all()
    assert np.linalg.eigvalsh(covariance.to_numpy()).min() >= -1e-12
    assert any("Ledoit-Wolf" in warning for warning in result.warnings)


def test_null_optional_contract_blocks_preserve_legacy_routing():
    prices = pd.DataFrame(
        {"QQQ": [100, 98, 96, 94, 92, 95, 98, 101]},
        index=pd.date_range("2026-01-01", periods=8),
    )
    run = run_strategy_backtest({
        "name": "legacy null contracts",
        "base_symbol": "QQQ",
        "data_profile": "generic",
        "signal": None,
        "portfolio": None,
        "execution": None,
        "indicators": [{"name": "rsi", "kind": "rsi", "period": 2}],
        "rules": {"entry": [{"field": "rsi", "op": "<", "value": 40}], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 0.5},
    }, prices)

    assert run.ok is True
    assert run.allocation_diagnostics == []
    assert "allocation_diagnostics" not in run.signals


def test_singular_cost_bps_is_included_in_engine_net_cost_drag():
    prices = pd.DataFrame(
        {"A": [100.0, 110.0, 121.0]},
        index=pd.date_range("2026-01-01", periods=3),
    )
    run = run_strategy_backtest({
        "name": "singular cost",
        "market": "multi",
        "universe": {"type": "list", "symbols": ["A"]},
        "signal": {"type": "factor", "plugin": "momentum", "lookback": 1},
        "portfolio": {"optimizer": "equal_weight", "max_position_pct": 1.0},
        "costs": {"cost_bps": 100.0},
    }, prices)

    assert run.ok is True
    assert run.metrics["turnover"] > 0.0
    assert run.metrics["cost_drag"] == pytest.approx(run.equity["turnover"].sum() * 0.01)


def test_allocation_result_to_dict_is_json_safe():
    idx = pd.date_range("2026-01-01", periods=1)
    result = allocate_targets(
        SignalPanel.from_score("factor", pd.DataFrame({"A": [1.0]}, index=idx), confidence=1.0),
        pd.DataFrame({"A": [0.01]}, index=idx),
        {},
        {},
    )

    payload = result.to_dict()
    encoded = json.dumps(payload)

    assert isinstance(encoded, str)
    assert payload["weights"]["columns"] == ["A"]


def test_legacy_strategy_does_not_enter_allocation_path():
    prices = pd.DataFrame(
        {"QQQ": [100, 98, 96, 94, 92, 95, 98, 101]},
        index=pd.date_range("2026-01-01", periods=8),
    )
    spec = {
        "name": "legacy RSI",
        "base_symbol": "QQQ",
        "data_profile": "generic",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "rsi", "kind": "rsi", "period": 2}],
        "rules": {"entry": [{"field": "rsi", "op": "<", "value": 40}], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 0.5},
    }

    run = run_strategy_backtest(spec, prices)

    assert run.ok is True
    assert run.allocation_diagnostics == []
    assert "allocation_diagnostics" not in run.signals


def test_explicit_signal_uses_signal_to_allocation_path():
    prices = pd.DataFrame(
        {"A": [100, 101, 102, 103], "B": [100, 99, 98, 97]},
        index=pd.date_range("2026-01-01", periods=4),
    )
    spec = StrategySpec.from_dict({
        "name": "signal allocation",
        "market": "multi",
        "universe": {"type": "list", "symbols": ["A", "B"]},
        "signal": {"type": "factor", "plugin": "momentum", "lookback": 1},
        "portfolio": {"optimizer": "equal_weight", "max_position_pct": 0.5},
    })

    run = run_strategy_backtest(spec, prices)

    assert run.ok is True
    assert not run.weights.empty
    assert "panel" in run.signals
    assert "allocation_diagnostics" in run.signals
    assert run.allocation_diagnostics == run.signals["allocation_diagnostics"]


def test_portfolio_rejects_unknown_optimizer():
    with pytest.raises(ValueError, match="unsupported portfolio optimizer"):
        StrategySpec.from_dict({
            "name": "bad optimizer",
            "portfolio": {"optimizer": "python"},
        })
