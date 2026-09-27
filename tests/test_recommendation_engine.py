import math

import pytest


def _engine():
    from ml import recommendation_engine

    return recommendation_engine


def test_build_snapshot_normalizes_candidate_without_inventing_missing_evidence():
    engine = _engine()

    snapshot = engine.build_snapshot(
        "entry",
        {
            "id": "entry-NVDA-1",
            "ticker": "NVDA",
            "horizon": 20,
            "raw_score": 0.8,
            "wins": 8,
            "samples": 10,
            "timestamp": "2026-09-27T09:00:00+00:00",
            "reasons": ["momentum"],
        },
        source_features={"news_axis": 0.9, "freshness_seconds": 172_801},
        as_of="2026-09-27T09:00:00+00:00",
        model_version="entry-v2",
    )

    required = {
        "id", "decision_ts", "asof_date", "surface", "market", "ticker", "horizon",
        "raw_score", "calibrated_probability", "expected_excess", "signal", "rank",
        "selection_status", "sample_size", "sample_quality", "uncertainty_low",
        "uncertainty_high", "freshness_seconds", "source_coverage", "data_quality",
        "missing_fields", "risk_score", "concentration_penalty", "total_rank_score",
        "model_version", "parameter_version", "feature_version", "reasons", "evidence_refs",
    }
    assert required <= snapshot.keys()
    assert snapshot["surface"] == "entry"
    assert snapshot["horizon"] == "20d"
    assert snapshot["calibrated_probability"] == pytest.approx(10 / 14)
    assert snapshot["uncertainty_low"] < snapshot["calibrated_probability"] < snapshot["uncertainty_high"]
    assert snapshot["selection_status"] == "shadow"
    assert snapshot["news_contribution"] == 0.0
    assert snapshot["freshness_seconds"] == 172_801
    assert {"market", "evidence_refs"} <= set(snapshot["missing_fields"])
    assert "stale" in snapshot["data_quality"]
    assert "missing_fields:market" in snapshot["data_quality"]


def test_shrink_probability_uses_beta_two_two_prior():
    engine = _engine()

    assert engine.shrink_probability(0, 0) == pytest.approx(0.5)
    assert engine.shrink_probability(8, 10) == pytest.approx(10 / 14)


def test_fit_score_calibrator_requires_one_hundred_finite_rows():
    engine = _engine()
    rows = [{"raw_score": 0.5, "positive_excess": 1, "split": "train"} for _ in range(99)]
    rows.extend([
        {"raw_score": float("nan"), "positive_excess": 1, "split": "holdout"},
        {"raw_score": 0.5, "positive_excess": float("inf"), "split": "holdout"},
    ])

    assert engine.fit_score_calibrator(rows) is None


def test_score_calibration_is_monotonic_and_reports_held_out_metrics():
    engine = _engine()
    rows = []
    for index, score in enumerate((0.1, 0.3, 0.5, 0.7, 0.9)):
        rows.extend(
            {
                "raw_score": score,
                "positive_excess": int(offset < index * 5),
                "split": "train",
                "decision_ts": f"2026-01-{index + 1:02d}T00:00:00+00:00",
            }
            for offset in range(20)
        )
    rows.extend([
        {"raw_score": 0.1, "positive_excess": 0, "split": "holdout"},
        {"raw_score": 0.3, "positive_excess": 0, "split": "holdout"},
        {"raw_score": 0.5, "positive_excess": 1, "split": "holdout"},
        {"raw_score": 0.7, "positive_excess": 1, "split": "holdout"},
    ])

    calibrator = engine.fit_score_calibrator(rows)

    assert calibrator is not None
    assert calibrator["training_samples"] == 100
    assert calibrator["held_out_samples"] == 4
    assert len(calibrator["bins"]) == 5
    probabilities = [bin_["probability"] for bin_ in calibrator["bins"]]
    assert probabilities == sorted(probabilities)
    assert engine.calibrate_score(0.9, calibrator) == pytest.approx(1.0)
    assert calibrator["brier"] == pytest.approx(0.09375)
    assert calibrator["ece"] == pytest.approx(0.25)


def test_calibrator_marks_metrics_unavailable_without_finite_held_out_rows():
    engine = _engine()
    rows = [
        {"raw_score": index / 100, "positive_excess": index % 2, "split": "train"}
        for index in range(100)
    ]
    rows.append({"raw_score": float("nan"), "positive_excess": 1, "split": "holdout"})

    calibrator = engine.fit_score_calibrator(rows)

    assert calibrator["metrics"] == {
        "status": "unavailable",
        "sample_size": 0,
        "brier": None,
        "ece": None,
    }


def test_calibrator_uses_the_latest_chronological_rows_as_held_out_data():
    engine = _engine()
    rows = [
        {
            "raw_score": index / 100,
            "positive_excess": index % 2,
            "decision_ts": f"2026-03-{index + 1:03d}",
        }
        for index in range(100)
    ]

    calibrator = engine.fit_score_calibrator(rows)

    assert calibrator["training_samples"] == 80
    assert calibrator["held_out_samples"] == 20
    assert [bin_["count"] for bin_ in calibrator["bins"]] == [20, 20, 20, 20]


def test_rank_snapshots_is_shadow_first_and_only_reorders_after_a_passed_gate():
    engine = _engine()
    snapshots = [
        {"ticker": "A", "calibrated_probability": 0.9, "risk_score": 1.0, "source_features": {"news_axis": 0.9}},
        {"ticker": "B", "calibrated_probability": 0.7, "risk_score": 0.0},
        {"ticker": "C", "calibrated_probability": 0.8, "risk_score": 0.0, "concentration_penalty": 0.25},
    ]

    shadow = engine.rank_snapshots(snapshots, top_n=3, holdings=("C",), active=False, gate={"passed": True})
    blocked = engine.rank_snapshots(snapshots, top_n=3, holdings=("C",), active=True, gate={"passed": False})
    active = engine.rank_snapshots(snapshots, top_n=3, holdings=("C",), active=True, gate={"passed": True})

    assert [row["ticker"] for row in shadow] == ["A", "B", "C"]
    assert [row["ticker"] for row in blocked] == ["A", "B", "C"]
    assert all(row["ranking_mode"] == "shadow" for row in shadow + blocked)
    assert shadow[0]["news_contribution"] == 0.0
    assert [row["ticker"] for row in active] == ["B", "C", "A"]
    assert active[0]["ranking_mode"] == "active"
    assert active[0]["selection_status"] == "selected"
    assert active[0]["risk_penalty"] == 0.0
    assert 0.0 <= active[-1]["risk_penalty"] <= 0.5
    assert 0.0 <= active[1]["concentration_penalty"] <= 0.35
    assert active[-1]["total_rank_score"] == pytest.approx(0.4)
    assert active[-1]["news_contribution"] == pytest.approx(0.9)


def test_calibrate_score_rejects_non_finite_inputs():
    engine = _engine()

    assert engine.calibrate_score(math.nan, {"bins": []}) is None
    assert engine.calibrate_score(0.5, None) is None
