from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ml.models import predict_with_metadata
from ml.strategy_studio import StrategyRun, build_strategy_report, run_strategy_backtest
from ml.strategy_studio.validation import (
    ValidationReport,
    ValidationSplit,
    check_data_model_provenance,
    check_split_leakage,
    evaluate_validation_folds,
    make_cpcv_splits,
    make_purged_walk_forward_splits,
    promotion_gate,
)
from ml.walk_forward import purged_walk_forward_splits


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


def _activation_provenance_evidence(count: int = 4) -> dict[str, object]:
    check = {
        "ok": True,
        "provenance_ok": True,
        "age_limits_configured": True,
        "evaluation_at": "2024-02-10T00:00:00Z",
        "data": {
            "source": "prices",
            "version": "v1",
            "as_of": "2024-02-09T00:00:00Z",
            "status": "fresh",
        },
        "model": {
            "model_id": "model-v1",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "feature_names": ["close"],
            "train_start": "2024-01-01T00:00:00Z",
            "train_end": "2024-02-08T00:00:00Z",
            "code_commit": "abc123",
            "seed": 42,
            "as_of": "2024-02-09T00:00:00Z",
            "status": "fresh",
            "provenance_status": "complete",
        },
    }
    return {"ok": True, "checks": [dict(check) for _ in range(count)]}


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

    with pytest.warns(UserWarning, match="label horizon"):
        cpcv_splits = make_cpcv_splits(index, groups=4, test_groups=1, embargo_bars=1)
    assert cpcv_splits == []


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
    assert all(split.to_dict()["chronology_evidence"]["valid"] for split in strict)


def test_cpcv_marks_training_between_test_groups_as_future_training():
    index = pd.date_range("2020-01-01", periods=16, freq="D")

    splits = make_cpcv_splits(index, groups=4, test_groups=2, embargo_bars=0, label_horizon=0)

    separated = next(split for split in splits if split.path_id == "cpcv-0-2")
    assert separated.future_training is True


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


def test_cpcv_activation_requires_affirmative_per_fold_chronology_proof():
    folds = [
        {
            "path_id": f"cpcv-{number}",
            "validation_mode": "cpcv",
            "train_max": f"2024-01-0{number + 1}T00:00:00Z",
            "test_start": f"2024-01-1{number + 1}T00:00:00Z",
            "net_cagr": 0.1,
            "trade_count": 50,
        }
        for number in range(4)
    ]
    base = {
        "validation_mode": "cpcv",
        "provenance": _activation_provenance_evidence(),
        "aggregate": {
            "net_cagr": 0.12, "benchmark_excess_cagr": 0.04, "max_drawdown": -0.1,
            "trade_count": 200, "test_periods": 120, "fold_count": 4, "cpcv_fold_count": 4,
            "cpcv_fold_ids": [f"cpcv-{number}" for number in range(4)], "n_observations": 120,
            "turnover": 0.3, "dsr": 0.99, "pbo": 0.1, "regime_concentration": 0.2,
            "provenance_ok": True,
            "cpcv_future_training": False,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
        "folds": folds,
    }
    incomplete = {
        **base,
        "aggregate": {
            **base["aggregate"],
            "cpcv_chronology_evidence": [
                {"fold_id": "cpcv-0", "valid": True, "future_training": False,
                 "train_max": "2024-01-01T00:00:00Z", "test_min": "2024-01-02T00:00:00Z",
                 "train_before_test": True},
            ],
        },
    }
    complete = {
        **base,
        "aggregate": {
            **base["aggregate"],
            "cpcv_chronology_evidence": [
                {"fold_id": f"cpcv-{number}", "valid": True, "future_training": False,
                 "train_max": f"2024-01-0{number + 1}T00:00:00Z",
                 "test_min": f"2024-01-1{number + 1}T00:00:00Z",
                 "train_before_test": True}
                for number in range(4)
            ],
        },
    }

    rejected = promotion_gate(
        ValidationReport.from_dict(incomplete),
        {"environment": "pilot", "strictly_chronological": True},
    )
    accepted = promotion_gate(
        ValidationReport.from_dict(complete),
        {"environment": "pilot", "strictly_chronological": True},
    )
    diagnostic_only = promotion_gate(
        ValidationReport.from_dict(complete),
        {"environment": "pilot"},
    )

    assert "cpcv_chronology_evidence" in rejected.failed_checks
    assert "cpcv_chronology_evidence" not in accepted.failed_checks
    assert accepted.accepted is True
    assert "cpcv_not_activation_safe" in diagnostic_only.failed_checks


def test_mixed_validation_modes_do_not_clear_cpcv_future_training_failure():
    cpcv = _run([0.001, -0.001] * 20)
    cpcv.spec["validation"]["mode"] = "cpcv"
    cpcv.metrics["future_training"] = True
    walk_forward = _run([0.001, -0.001] * 20, start="2025-01-01")
    walk_forward.spec["validation"]["mode"] = "purged_walk_forward"

    report = evaluate_validation_folds([cpcv, walk_forward], {})
    decision = promotion_gate(report, {"mode": "purged_walk_forward", "environment": "pilot"})

    assert report.aggregate["cpcv_future_training"] is True
    assert "cpcv_future_training" in decision.failed_checks
    assert "cpcv_chronology_evidence" in decision.failed_checks


def test_cpcv_chronology_evidence_is_bound_to_fold_ids_timestamps_and_flags():
    folds = [
        {
            "path_id": "cpcv-0",
            "validation_mode": "cpcv",
            "train_max": "2024-01-01T00:00:00Z",
            "test_start": "2024-01-11T00:00:00Z",
            "trade_count": 50,
        },
        {
            "path_id": "cpcv-1",
            "validation_mode": "cpcv",
            "train_max": "2024-01-02T00:00:00Z",
            "test_start": "2024-01-12T00:00:00Z",
            "trade_count": 50,
        },
    ]
    evidence = [
        {
            "fold_id": "cpcv-0", "valid": True, "future_training": False,
            "train_max": "2024-01-01T00:00:00Z", "test_min": "2024-01-11T00:00:00Z",
            "train_before_test": True,
        },
        {
            "fold_id": "cpcv-1", "valid": True, "future_training": False,
            "train_max": "2024-01-02T00:00:00Z", "test_min": "2024-01-12T00:00:00Z",
            "train_before_test": True,
        },
    ]
    base = {
        "validation_mode": "cpcv",
        "aggregate": {
            "net_cagr": 0.12, "benchmark_excess_cagr": 0.04, "max_drawdown": -0.1,
            "trade_count": 100, "test_periods": 120, "cpcv_fold_count": 2,
            "cpcv_fold_ids": ["cpcv-0", "cpcv-1"], "n_observations": 120,
            "turnover": 0.3, "dsr": 0.99, "pbo": 0.1, "regime_concentration": 0.2,
            "provenance_ok": True,
            "cpcv_future_training": False,
            "cpcv_chronology_evidence": evidence,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
        "folds": folds,
    }

    variants = [
        ([{**evidence[0], "fold_id": "cpcv-missing"}, evidence[1]], "mismatched fold ID"),
        ([evidence[0], {**evidence[1], "fold_id": "cpcv-0"}], "duplicate fold ID"),
        ([{key: value for key, value in evidence[0].items() if key != "valid"}, evidence[1]], "missing proof"),
        ([{**evidence[0], "future_training": True, "no_future_training": True}, evidence[1]], "contradictory future proof"),
        ([{**evidence[0], "train_end": "2024-01-02T00:00:00Z"}, evidence[1]], "conflicting train-end aliases"),
        ([{**evidence[0], "test_start": "2024-01-12T00:00:00Z"}, evidence[1]], "conflicting test-start aliases"),
        ([{**evidence[0], "test_min": "2024-01-10T00:00:00Z"}, evidence[1]], "mismatched test timestamp"),
    ]

    for invalid_evidence, label in variants:
        report = ValidationReport.from_dict({
            **base,
            "aggregate": {**base["aggregate"], "cpcv_chronology_evidence": invalid_evidence},
        })
        decision = promotion_gate(
            report, {"environment": "pilot", "strictly_chronological": True}
        )
        assert "cpcv_chronology_evidence" in decision.failed_checks, label


def test_evaluator_separates_cpcv_fold_count_from_return_observations():
    folds = []
    for number, start in enumerate(("2024-01-01", "2024-03-01")):
        run = _run([0.001, -0.001] * 20, start=start)
        run.spec["validation"].update({
            "mode": "cpcv",
            "chronology_evidence": {
                "fold_id": f"cpcv-{number}",
                "valid": True,
                "future_training": False,
                "train_max": f"2023-12-{31 - number:02d}T00:00:00Z",
                "test_min": f"{start}T00:00:00Z",
                "train_before_test": True,
            },
            "train_max": f"2023-12-{31 - number:02d}T00:00:00Z",
        })
        run.metrics.update({"path_id": f"cpcv-{number}", "trade_count": 50})
        folds.append(run)

    report = evaluate_validation_folds(folds, {})

    assert report.aggregate["test_periods"] == 80
    assert report.aggregate["fold_count"] == 2
    assert report.aggregate["cpcv_fold_count"] == 2
    assert report.aggregate["cpcv_fold_ids"] == ["cpcv-0", "cpcv-1"]
    assert report.aggregate["cpcv_chronology_ok"] is True


def test_cpcv_explicit_zero_fold_count_does_not_fallback_to_observations():
    fold = {
        "path_id": "cpcv-0",
        "validation_mode": "cpcv",
        "train_max": "2024-01-01T00:00:00Z",
        "test_start": "2024-01-11T00:00:00Z",
        "trade_count": 100,
    }
    evidence = [{
        "fold_id": "cpcv-0", "valid": True, "future_training": False,
        "train_max": "2024-01-01T00:00:00Z", "test_min": "2024-01-11T00:00:00Z",
        "train_before_test": True,
    }]
    aggregate = {
        "net_cagr": 0.12, "benchmark_excess_cagr": 0.04, "max_drawdown": -0.1,
        "trade_count": 100, "test_periods": 120, "n_observations": 120,
        "turnover": 0.3, "dsr": 0.99, "pbo": 0.1, "regime_concentration": 0.2,
        "provenance_ok": True, "cpcv_future_training": False,
        "cpcv_fold_ids": ["cpcv-0"], "cpcv_chronology_evidence": evidence,
        "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
        "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
    }

    for count_key in ("fold_count", "cpcv_fold_count"):
        report = ValidationReport.from_dict({
            "validation_mode": "cpcv",
            "aggregate": {**aggregate, count_key: 0},
            "folds": [fold],
        })
        decision = promotion_gate(
            report, {"environment": "pilot", "strictly_chronological": True}
        )

        assert "cpcv_chronology_evidence" in decision.failed_checks
        assert "min_test_periods" in decision.failed_checks


def test_cpcv_rejects_proof_only_or_conflicting_actual_timestamps():
    evidence = [{
        "fold_id": "cpcv-0", "valid": True, "future_training": False,
        "train_max": "2024-01-01T00:00:00Z", "test_min": "2024-01-11T00:00:00Z",
        "train_before_test": True,
    }]
    base = {
        "validation_mode": "cpcv",
        "aggregate": {
            "net_cagr": 0.12, "benchmark_excess_cagr": 0.04, "max_drawdown": -0.1,
            "trade_count": 100, "test_periods": 120, "cpcv_fold_count": 1,
            "cpcv_fold_ids": ["cpcv-0"], "n_observations": 120,
            "turnover": 0.3, "dsr": 0.99, "pbo": 0.1, "regime_concentration": 0.2,
            "provenance_ok": True, "cpcv_future_training": False,
            "cpcv_chronology_evidence": evidence,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
    }
    actual_variants = [
        {
            "path_id": "cpcv-0", "validation_mode": "cpcv",
            "chronology_evidence": evidence[0],
        },
        {
            "path_id": "cpcv-0", "validation_mode": "cpcv",
            "train_max": "2024-01-01T00:00:00Z",
            "test_min": "2024-01-11T00:00:00Z",
            "test_start": "2024-01-12T00:00:00Z",
        },
    ]

    for actual in actual_variants:
        report = ValidationReport.from_dict({**base, "folds": [actual]})
        decision = promotion_gate(
            report, {"environment": "pilot", "strictly_chronological": True}
        )

        assert "cpcv_chronology_evidence" in decision.failed_checks


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


def test_promotion_rejects_explicit_incomplete_model_provenance_even_when_prediction_compatibility_is_opt_in():
    run = _run([0.001, -0.001] * 20)
    run.spec["validation"].update({"max_data_age_seconds": 172800, "max_model_age_seconds": 172800})
    run.signals["provenance"] = {
        "data": {
            "source": "prices",
            "version": "v1",
            "as_of": "2024-02-08T00:00:00Z",
            "freshness": "fresh",
        },
        "model": {
            "model_id": "legacy-model",
            "model_version": "model-v1",
            "as_of": "2024-02-08T00:00:00Z",
            "freshness": "fresh",
            "provenance_status": "incomplete",
            "require_provenance": False,
        },
    }

    report = evaluate_validation_folds([run], {})
    decision = promotion_gate(
        report,
        {"mode": "purged_walk_forward", "environment": "pilot"},
    )

    assert report.aggregate["provenance_ok"] is False
    assert "provenance" in decision.failed_checks


def test_activation_rejects_aggregate_only_provenance_claim_without_fold_checks():
    report = ValidationReport.from_dict({
        "validation_mode": "purged_walk_forward",
        "aggregate": {
            "net_cagr": 0.12,
            "benchmark_excess_cagr": 0.04,
            "max_drawdown": -0.1,
            "trade_count": 200,
            "test_periods": 120,
            "n_observations": 120,
            "fold_count": 4,
            "turnover": 0.3,
            "dsr": 0.99,
            "pbo": 0.1,
            "regime_concentration": 0.2,
            "provenance_ok": True,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
        "folds": [{"net_cagr": 0.1, "trade_count": 50}] * 4,
    })

    decision = promotion_gate(report, {"environment": "pilot"})

    assert decision.accepted is False
    assert "provenance" in decision.failed_checks


def test_default_compatible_prediction_provenance_cannot_activate_task5():
    class FixedModel:
        feature_names_ = ["close"]

        def predict(self, features):
            return [0.25] * len(features)

    prediction = predict_with_metadata(
        FixedModel(),
        pd.DataFrame({"close": [100.0]}),
        {
            "model_id": "legacy-promotion-model",
            "feature_version": "features-v1",
            "model_version": "model-v1",
            "as_of": "2024-02-08T00:00:00Z",
            "confidence": [0.9],
            "feature_names": ["close"],
            "require_provenance": False,
        },
    )
    run = _run([0.001, -0.001] * 20)
    run.spec["validation"].update({"max_data_age_seconds": 172800, "max_model_age_seconds": 172800})
    run.signals["provenance"] = {
        "data": {
            "source": "prices",
            "version": "v1",
            "as_of": "2024-02-08T00:00:00Z",
            "freshness": "fresh",
        },
        **prediction["provenance"],
    }

    report = evaluate_validation_folds([run], {})
    decision = promotion_gate(report, {"mode": "purged_walk_forward", "environment": "pilot"})

    assert prediction["provenance_status"] == "incomplete"
    assert report.aggregate["provenance_ok"] is False
    assert "provenance" in decision.failed_checks


def test_mixed_fold_provenance_is_invalid_when_one_fold_is_missing():
    valid = _run([0.001, -0.001] * 20)
    valid.spec["validation"].update({"max_data_age_seconds": 86400, "max_model_age_seconds": 86400})
    valid.signals["provenance"] = {
        "data": {"source": "prices", "version": "v1", "as_of": "2024-02-09T00:00:00Z", "freshness": "fresh"},
        "model": {"model_id": "m1", "model_version": "v1", "trained_until": "2024-02-09T00:00:00Z", "freshness": "fresh"},
    }
    missing = _run([0.001, -0.001] * 20, start="2025-01-01")
    missing.spec["validation"].update({"max_data_age_seconds": 86400, "max_model_age_seconds": 86400})

    report = evaluate_validation_folds([valid, missing], {})
    decision = promotion_gate(report, {"mode": "purged_walk_forward", "environment": "pilot"})

    assert report.aggregate["provenance_ok"] is False
    assert any("provenance is unavailable" in warning for warning in report.warnings)
    assert "provenance" in decision.failed_checks


def test_split_leakage_requires_declared_horizon_and_rejects_nat_label_end():
    split = ValidationSplit(train=pd.Index([0, 1]), test=pd.Index([3, 4]))
    assert "label_horizon_missing" in check_split_leakage(split)

    with_nat = ValidationSplit(train=pd.date_range("2024-01-01", periods=2), test=pd.date_range("2024-01-04", periods=2))
    issues = check_split_leakage(with_nat, label_end=[pd.NaT, pd.Timestamp("2024-01-02")])

    assert "label_end_invalid" in issues


def test_explicit_label_end_extends_post_test_blackout_beyond_numeric_horizon():
    index = pd.date_range("2020-01-01", periods=20, freq="D")
    label_end = pd.Series(index + pd.to_timedelta(5, unit="D"), index=index)

    splits = make_purged_walk_forward_splits(
        index, train_bars=8, test_bars=3, step_bars=3, embargo_bars=1,
        label_horizon=1, label_end=label_end,
    )

    assert splits
    first = splits[0]
    assert index[15] in first.blocked
    assert index[16] in first.blocked


def test_legacy_integer_walk_forward_default_remains_available():
    splits = list(purged_walk_forward_splits(20, 8, 3, 3, embargo=1))

    assert splits
    assert splits[0][0].tolist() == list(range(8))
    assert splits[0][1].tolist() == list(range(9, 12))


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


def test_nat_provenance_timestamp_is_missing_and_not_freshness_safe():
    result = check_data_model_provenance(
        {
            "data": {"source": "prices", "version": "v1", "as_of": pd.NaT, "freshness": "fresh"},
            "model": {"model_id": "m1", "model_version": "v1", "trained_until": "2024-01-03T00:00:00Z", "freshness": "fresh"},
        },
        evaluation_at="2024-01-03T00:00:00Z",
        max_data_age_seconds=3600,
        max_model_age_seconds=3600,
    )

    assert result["ok"] is False
    assert any("data provenance timestamp is missing" in warning for warning in result["warnings"])


def test_strict_provenance_rejects_blank_model_provenance_status():
    result = check_data_model_provenance(
        {
            "data": {"source": "prices", "version": "v1", "as_of": "2024-02-09T00:00:00Z", "status": "fresh"},
            "model": {
                "model_id": "m1",
                "feature_version": "features-v1",
                "model_version": "model-v1",
                "feature_names": ["close"],
                "train_start": "2024-01-01T00:00:00Z",
                "train_end": "2024-02-08T00:00:00Z",
                "code_commit": "abc123",
                "seed": 42,
                "as_of": "2024-02-09T00:00:00Z",
                "status": "fresh",
                "provenance_status": " ",
            },
        },
        evaluation_at="2024-02-10T00:00:00Z",
        max_data_age_seconds=86400,
        max_model_age_seconds=86400,
    )

    assert result["ok"] is False
    assert any("provenance status" in warning for warning in result["warnings"])


def test_evaluator_enforces_configured_data_and_model_age_limits():
    run = _run([0.001, -0.001] * 20)
    run.spec["validation"].update({"max_data_age_seconds": 86400, "max_model_age_seconds": 86400})
    run.signals["provenance"] = {
        "data": {"source": "prices", "version": "v1", "as_of": "2024-02-09T00:00:00Z", "freshness": "fresh"},
        "model": {
            "model_id": "m1",
            "feature_version": "features-v1",
            "model_version": "v1",
            "feature_names": ["close"],
            "train_start": "2024-01-01T00:00:00Z",
            "train_end": "2024-02-09T00:00:00Z",
            "code_commit": "abc123",
            "seed": 42,
            "as_of": "2024-02-09T00:00:00Z",
            "freshness": "fresh",
            "provenance_status": "complete",
        },
    }

    report = evaluate_validation_folds([run], {})

    assert report.aggregate["provenance_ok"] is True
    assert report.provenance["checks"][0]["age_limits_configured"] is True


def test_activation_rejects_falsely_complete_payload_missing_required_model_fields():
    evidence = _activation_provenance_evidence(count=1)
    del evidence["checks"][0]["model"]["code_commit"]
    report = ValidationReport.from_dict({
        "validation_mode": "purged_walk_forward",
        "provenance": evidence,
        "aggregate": {
            "net_cagr": 0.12,
            "benchmark_excess_cagr": 0.04,
            "max_drawdown": -0.1,
            "trade_count": 200,
            "test_periods": 120,
            "fold_count": 1,
            "n_observations": 120,
            "turnover": 0.3,
            "dsr": 0.99,
            "pbo": 0.1,
            "regime_concentration": 0.2,
            "provenance_ok": True,
            "dsr_evidence": {"tested_configurations": 4, "method": "dsr"},
            "pbo_evidence": {"tested_configurations": 4, "matrix_shape": [120, 4]},
        },
        "folds": [{"net_cagr": 0.1, "trade_count": 50}],
    })

    decision = promotion_gate(report, {"environment": "pilot"})

    assert decision.accepted is False
    assert "provenance" in decision.failed_checks


def test_numeric_only_dsr_pbo_report_is_rejected_and_complete_evidence_passes():
    base = {
        "validation_mode": "purged_walk_forward",
        "provenance": _activation_provenance_evidence(),
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
        "provenance": _activation_provenance_evidence(),
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
