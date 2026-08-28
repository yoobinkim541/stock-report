from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ml.strategy_studio import StrategyRun, build_strategy_report, run_strategy_backtest
from ml.strategy_studio.validation import (
    ValidationReport,
    check_data_model_provenance,
    check_split_leakage,
    evaluate_validation_folds,
    make_cpcv_splits,
    make_purged_walk_forward_splits,
    promotion_gate,
)
from ml.walk_forward import leakage_guard_split, purged_walk_forward_splits


def _run(
    returns: list[float],
    *,
    gross_returns: list[float] | None = None,
    cost_drag: list[float] | None = None,
    trade_pnls: list[float] | None = None,
    start: str = "2024-01-01",
    freq: str = "D",
) -> StrategyRun:
    index = pd.date_range(start, periods=len(returns), freq=freq)
    net = pd.Series(returns, index=index, dtype="float64")
    gross = pd.Series(gross_returns if gross_returns is not None else returns, index=index, dtype="float64")
    drag = pd.Series(cost_drag if cost_drag is not None else (gross - net), index=index, dtype="float64")
    equity = pd.DataFrame({
        "nav": 100.0 * (1.0 + net).cumprod(),
        "gross_nav": 100.0 * (1.0 + gross).cumprod(),
        "net_return": net,
        "gross_return": gross,
        "cost_drag": drag,
        "turnover": pd.Series(0.02, index=index),
    })
    pnls = trade_pnls if trade_pnls is not None else []
    trades = [{"pnl": pnl, "date": str(index[min(i, len(index) - 1)].date())} for i, pnl in enumerate(pnls)]
    return StrategyRun(
        ok=True,
        spec={"name": "validation fixture", "validation": {"mode": "purged_walk_forward"}},
        metrics={"trade_count": len(trades)},
        trades=trades,
        equity=equity,
        benchmark={"symbol": "SPY", "available": False},
    )


def test_purged_walk_forward_is_deterministic_and_embargoed():
    index = pd.date_range("2020-01-01", periods=30, freq="D")

    first = make_purged_walk_forward_splits(
        index, train_bars=10, test_bars=5, step_bars=5, embargo_bars=2, label_horizon=0,
    )
    second = make_purged_walk_forward_splits(
        index, train_bars=10, test_bars=5, step_bars=5, embargo_bars=2, label_horizon=0,
    )

    assert [(s.path_id, s.train.tolist(), s.test.tolist()) for s in first] == [
        (s.path_id, s.train.tolist(), s.test.tolist()) for s in second
    ]
    assert first
    for split in first:
        assert split.train[-1] < split.test[0]
        assert (split.test[0] - split.train[-1]).days >= 3
        assert check_split_leakage(split) == []


def test_purged_walk_forward_requires_explicit_label_horizon_metadata():
    index = pd.date_range("2020-01-01", periods=20, freq="D")

    with pytest.warns(UserWarning, match="label horizon"):
        splits = make_purged_walk_forward_splits(
            index, train_bars=8, test_bars=3, step_bars=3, embargo_bars=1,
        )

    assert splits == []


def test_integer_walk_forward_helper_requires_horizon_metadata_too():
    with pytest.warns(UserWarning, match="label horizon"):
        assert list(purged_walk_forward_splits(20, 8, 3, 3, embargo=1)) == []

    issues = leakage_guard_split(np.arange(8), np.arange(9, 12), raise_on_error=False)
    assert "label_horizon_missing" in issues


def test_purged_walk_forward_blocks_post_test_embargo_and_overlapping_labels():
    index = pd.date_range("2020-01-01", periods=20, freq="D")
    label_end = pd.Series(index + pd.to_timedelta(5, unit="D"), index=index)

    splits = make_purged_walk_forward_splits(
        index, train_bars=8, test_bars=3, step_bars=3, embargo_bars=2, label_end=label_end,
    )

    assert splits
    first = splits[0]
    assert index[7] not in first.train
    assert index[6] not in first.train
    assert index[13] in first.blocked
    assert index[14] in first.blocked
    assert index[13] not in first.train
    assert index[14] not in first.train


def test_declared_label_horizon_extends_post_test_blackout():
    index = pd.date_range("2020-01-01", periods=20, freq="D")
    splits = make_purged_walk_forward_splits(
        index, train_bars=8, test_bars=3, step_bars=3, embargo_bars=1, label_horizon=2,
    )

    assert splits
    first = splits[0]
    assert index[12] in first.blocked
    assert index[13] in first.blocked
    assert index[14] in first.blocked


def test_purged_walk_forward_removes_rows_with_overlapping_label_end_times():
    index = pd.date_range("2020-01-01", periods=14, freq="D")
    label_end = pd.Series(index + pd.to_timedelta(3, unit="D"), index=index)

    splits = make_purged_walk_forward_splits(
        index, train_bars=8, test_bars=3, step_bars=3, embargo_bars=0, label_end=label_end,
    )

    assert splits
    first = splits[0]
    assert index[7] not in first.train
    assert index[6] not in first.train


def test_split_generators_reject_duplicate_or_non_monotonic_indexes():
    with pytest.raises(ValueError, match="monotonic"):
        make_purged_walk_forward_splits(pd.Index([2, 1, 3]), 1, 1, 1, 0)
    with pytest.raises(ValueError, match="duplicate"):
        make_cpcv_splits(pd.Index([1, 1, 2, 3]), groups=2, test_groups=1, embargo_bars=0)


def test_cpcv_paths_are_chronological_and_have_post_test_embargo():
    index = pd.date_range("2020-01-01", periods=12, freq="D")
    splits = make_cpcv_splits(index, groups=3, test_groups=1, embargo_bars=1, label_horizon=0)

    assert len(splits) == 3
    assert [split.path_id for split in splits] == ["cpcv-0", "cpcv-1", "cpcv-2"]
    for split in splits:
        assert split.test.is_monotonic_increasing
        assert split.train.is_monotonic_increasing
        assert not set(split.train).intersection(split.test)
        assert check_split_leakage(split) == []
        for test_value in split.test:
            assert test_value not in split.train
        last_test = split.test[-1]
        following = index[index > last_test]
        if len(following):
            assert following[0] not in split.train


def test_cpcv_is_diagnostic_by_default_and_strict_mode_has_no_future_training():
    index = pd.date_range("2020-01-01", periods=12, freq="D")
    diagnostic = make_cpcv_splits(index, groups=3, test_groups=1, embargo_bars=1, label_horizon=0)
    strict = make_cpcv_splits(
        index, groups=3, test_groups=1, embargo_bars=1, label_horizon=0,
        strictly_chronological=True,
    )

    assert any(split.future_training for split in diagnostic)
    assert strict
    assert all(not split.future_training for split in strict)
    assert all(len(split.train) == 0 or split.train[-1] < split.test[0] for split in strict)


def test_cpcv_future_training_cannot_be_activation_safe():
    report = ValidationReport.from_dict({
        "validation_mode": "cpcv",
        "aggregate": {
            "net_cagr": 0.12,
            "benchmark_excess_cagr": 0.04,
            "max_drawdown": -0.10,
            "trade_count": 200,
            "test_periods": 4,
            "n_observations": 120,
            "turnover": 0.30,
            "dsr": 0.99,
            "pbo": 0.10,
            "regime_concentration": 0.20,
            "provenance_ok": True,
            "cpcv_future_training": True,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
        "folds": [{"net_cagr": 0.1, "trade_count": 50}] * 4,
    })

    decision = promotion_gate(report, {"environment": "pilot", "strictly_chronological": True})

    assert decision.accepted is False
    assert "cpcv_future_training" in decision.failed_checks


def test_missing_provenance_and_age_limits_fail_closed_but_remain_diagnostic():
    incomplete = check_data_model_provenance(
        {"data": {"source": "prices"}, "model": {"model_id": "m1"}},
        evaluation_at="2024-01-03T00:00:00Z",
    )

    assert incomplete["ok"] is False
    assert any("incomplete" in warning for warning in incomplete["warnings"])
    assert any("age limits" in warning for warning in incomplete["warnings"])

    report = ValidationReport.from_dict({
        "validation_mode": "purged_walk_forward",
        "aggregate": {
            "net_cagr": 0.12, "benchmark_excess_cagr": 0.04, "max_drawdown": -0.1,
            "trade_count": 200, "test_periods": 4, "n_observations": 120,
            "turnover": 0.3, "dsr": 0.99, "pbo": 0.1, "regime_concentration": 0.2,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
        "folds": [{"net_cagr": 0.1, "trade_count": 50}] * 4,
    })
    decision = promotion_gate(report, {"environment": "pilot"})

    assert decision.accepted is False
    assert "provenance" in decision.failed_checks


def test_complete_provenance_without_evaluation_timestamp_is_not_freshness_safe():
    result = check_data_model_provenance(
        {
            "data": {"source": "prices", "version": "v1", "as_of": "2024-01-03T00:00:00Z"},
            "model": {"model_id": "m1", "model_version": "v1", "trained_until": "2024-01-03T00:00:00Z"},
        },
        max_data_age_seconds=3600,
        max_model_age_seconds=3600,
    )

    assert result["ok"] is False
    assert any("evaluation timestamp" in warning for warning in result["warnings"])


def test_evaluator_enforces_configured_data_and_model_age_limits():
    run = _run([0.001, -0.001] * 20)
    run.spec["validation"].update({"max_data_age_seconds": 86400, "max_model_age_seconds": 86400})
    run.signals["provenance"] = {
        "data": {"source": "prices", "version": "v1", "as_of": "2024-02-09T00:00:00Z"},
        "model": {"model_id": "m1", "model_version": "v1", "trained_until": "2024-02-09T00:00:00Z"},
    }

    report = evaluate_validation_folds([run], {})

    assert report.aggregate["provenance_ok"] is True
    assert report.provenance["checks"][0]["age_limits_configured"] is True


def test_numeric_only_dsr_pbo_report_is_rejected_and_complete_evidence_passes():
    base = {
        "validation_mode": "purged_walk_forward",
        "aggregate": {
            "net_cagr": 0.12, "benchmark_excess_cagr": 0.04, "max_drawdown": -0.1,
            "trade_count": 200, "test_periods": 4, "n_observations": 120,
            "turnover": 0.3, "dsr": 0.99, "pbo": 0.1, "regime_concentration": 0.2,
            "provenance_ok": True,
        },
        "folds": [{"net_cagr": 0.1, "trade_count": 50}] * 4,
    }
    numeric_only = promotion_gate(ValidationReport.from_dict(base), {"environment": "pilot"})
    complete = dict(base)
    complete["aggregate"] = {
        **base["aggregate"],
        "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
        "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4], "method": "cscv"},
    }
    evidenced = promotion_gate(ValidationReport.from_dict(complete), {"environment": "pilot"})

    assert numeric_only.accepted is False
    assert "dsr_evidence" in numeric_only.failed_checks
    assert "pbo_evidence" in numeric_only.failed_checks
    assert "dsr_evidence" not in evidenced.failed_checks
    assert "pbo_evidence" not in evidenced.failed_checks
    assert evidenced.accepted is True


def test_annualization_distinguishes_five_minute_and_weekly_returns():
    returns = [0.01, -0.01] * 20
    five_minute = evaluate_validation_folds([_run(returns, freq="5min")], {})
    weekly = evaluate_validation_folds([_run(returns, freq="W")], {})

    assert five_minute.aggregate["periods_per_year"] > 100_000
    assert weekly.aggregate["periods_per_year"] == pytest.approx(52.18, rel=0.02)
    assert five_minute.aggregate["volatility"] > weekly.aggregate["volatility"] * 20


def test_benchmark_run_and_metric_shapes_produce_cost_adjusted_excess():
    strategy = _run([0.01, 0.0, 0.01] * 20)
    benchmark = _run([0.005, 0.0, 0.005] * 20)

    report = evaluate_validation_folds([strategy], {"buy_and_hold": benchmark})
    metric_report = evaluate_validation_folds(
        [strategy], {"buy_and_hold": {"equity": benchmark.equity["nav"], "cagr": 0.01}},
    )

    assert report.aggregate["benchmark_excess_cagr"] is not None
    assert metric_report.aggregate["benchmark_excess_cagr"] is not None
    assert report.aggregate["benchmark_excess_cagr"] > 0
    assert metric_report.aggregate["benchmark_excess_cagr"] == pytest.approx(
        metric_report.aggregate["net_cagr"] - 0.01,
    )


def test_missing_trade_pnls_do_not_use_bar_returns_for_trade_metrics():
    report = evaluate_validation_folds([_run([0.01, -0.01] * 20)], {})

    assert report.aggregate["hit_rate"] is None
    assert report.aggregate["profit_factor"] is None
    assert any("trade PnL" in warning for warning in report.warnings)


def test_duplicate_timestamps_and_overlapping_fold_timestamps_warn():
    duplicate = _run([0.01, -0.01] * 20)
    duplicate.equity.index = duplicate.equity.index.where(
        np.arange(len(duplicate.equity)) != 1, duplicate.equity.index[0],
    )
    overlapping = _run([0.01, -0.01] * 20, start="2024-01-10")

    report = evaluate_validation_folds([duplicate, overlapping], {})

    assert any("duplicate" in warning for warning in report.warnings)
    assert any("overlap" in warning for warning in report.warnings)


def test_metrics_are_net_of_cost_and_include_risk_trade_and_cost_fields():
    net = [0.001, -0.0005, 0.002, -0.001] * 80
    gross = [value + 0.0002 for value in net]
    folds = [_run(net, gross_returns=gross, cost_drag=[0.0002] * len(net), trade_pnls=[1.0, -0.5, 2.0])]

    report = evaluate_validation_folds(folds, {})

    for field in (
        "gross_cagr", "net_cagr", "volatility", "sharpe", "sortino", "max_drawdown",
        "calmar", "turnover", "cost_drag", "hit_rate", "profit_factor", "trade_count",
    ):
        assert field in report.aggregate
    assert report.aggregate["gross_cagr"] > report.aggregate["net_cagr"]
    assert report.aggregate["cost_drag"] == pytest.approx(0.0002 * len(net))
    assert report.aggregate["hit_rate"] == pytest.approx(2 / 3)
    assert report.aggregate["profit_factor"] == pytest.approx(3.0 / 0.5)


def test_tiny_samples_do_not_fabricate_dsr_or_pbo():
    report = evaluate_validation_folds([_run([0.01, -0.01, 0.005], trade_pnls=[1.0])], {})

    assert report.aggregate["dsr"] is None
    assert report.aggregate["pbo"] is None
    assert any("DSR" in warning for warning in report.warnings)
    assert any("PBO" in warning for warning in report.warnings)
    json.dumps(report.to_dict())


def test_data_and_model_provenance_reject_stale_and_future_timestamps():
    result = check_data_model_provenance(
        {
            "data": {"source": "prices", "version": "v1", "as_of": "2024-01-05T00:00:00Z"},
            "model": {"model_id": "m1", "model_version": "m1-v1", "trained_until": "2024-01-04T00:00:00Z"},
        },
        evaluation_at="2024-01-03T00:00:00Z",
        max_data_age_seconds=60,
        max_model_age_seconds=60,
    )

    assert result["ok"] is False
    assert any("later" in error for error in result["errors"])
    assert any("stale" in warning for warning in result["warnings"])


def test_promotion_gate_separates_preview_pilot_and_explicit_live_activation():
    report = ValidationReport.from_dict({
        "validation_mode": "purged_walk_forward",
        "aggregate": {
            "net_cagr": 0.12,
            "benchmark_excess_cagr": 0.04,
            "max_drawdown": -0.10,
            "trade_count": 200,
            "test_periods": 4,
            "turnover": 0.30,
            "dsr": 0.99,
            "pbo": 0.10,
            "regime_concentration": 0.20,
            "provenance_ok": True,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
        "folds": [{"net_cagr": 0.1, "trade_count": 50}] * 4,
    })
    config = {
        "min_trades": 100,
        "min_test_periods": 4,
        "max_pbo": 0.5,
        "min_dsr": 0.95,
        "max_drawdown": 0.25,
        "max_turnover": 1.0,
        "max_regime_concentration": 0.75,
        "require_cost_adjusted_positive_excess": True,
    }

    preview = promotion_gate(report, {**config, "mode": "single_pass", "environment": "shadow"})
    pilot = promotion_gate(report, {**config, "environment": "pilot"})
    live = promotion_gate(report, {**config, "environment": "live"})
    activated = promotion_gate(report, {**config, "environment": "live", "explicit_live_activation": True})

    assert preview.accepted is False
    assert preview.preview is True
    assert "preview_only" in preview.failed_checks
    assert pilot.accepted is True
    assert live.accepted is False
    assert "live_activation_explicit" in live.failed_checks
    assert activated.accepted is True
    assert activated.activation_safe is True


def test_promotion_gate_rejects_negative_cost_adjusted_excess():
    report = ValidationReport.from_dict({
        "aggregate": {
            "net_cagr": -0.02,
            "benchmark_excess_cagr": -0.03,
            "max_drawdown": -0.25,
            "trade_count": 200,
            "turnover": 0.8,
            "dsr": 0.99,
            "pbo": 0.1,
        },
        "folds": [{"net_cagr": -0.01, "trade_count": 100}],
    })
    decision = promotion_gate(report, {"min_trades": 100, "max_pbo": 0.5, "min_dsr": 0.95,
                                       "require_cost_adjusted_positive_excess": True})

    assert decision.accepted is False
    assert "net_excess" in decision.failed_checks


def test_legacy_single_pass_run_exposes_preview_validation_without_behavior_change():
    index = pd.date_range("2026-01-01", periods=20, freq="D")
    prices = pd.DataFrame({"QQQ": np.linspace(100.0, 110.0, len(index))}, index=index)
    spec = {
        "name": "legacy validation preview",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "rules": {"entry": [], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "validation": {"mode": "single_pass"},
    }

    run = run_strategy_backtest(spec, prices, benchmark="QQQ")
    report = build_strategy_report(run)

    assert run.ok is True
    assert run.validation["promotion_eligible"] is False
    assert run.promotion["preview"] is True
    assert report["validation"]["promotion_eligible"] is False
    json.dumps(report)
