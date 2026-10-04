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


def test_build_snapshot_merges_inherited_and_comprehensive_missing_metadata():
    engine = _engine()

    snapshot = engine.build_snapshot(
        "report",
        {
            "id": "report-AAPL-1",
            "ticker": "AAPL",
            "horizon": "20d",
            "raw_score": 0.6,
            "missing_fields": ["body_available", "macro_feature"],
        },
        source_features={"missing_fields": ["macro_feature", "blocked_source"]},
    )

    assert snapshot["missing_fields"][:3] == ["body_available", "macro_feature", "blocked_source"]
    assert {
        "decision_ts", "asof_date", "market", "calibrated_probability", "expected_excess",
        "signal", "rank", "sample_size", "uncertainty_low", "uncertainty_high",
        "freshness_seconds", "source_coverage", "risk_score", "model_version",
        "parameter_version", "feature_version", "evidence_refs",
    } <= set(snapshot["missing_fields"])
    assert "missing_fields:body_available" in snapshot["data_quality"]
    assert "missing_fields:blocked_source" in snapshot["data_quality"]
    assert "missing_fields:expected_excess" in snapshot["data_quality"]
    assert len(snapshot["missing_fields"]) == len(set(snapshot["missing_fields"]))
    assert "sample_quality" in snapshot["missing_fields"]


def test_empty_snapshot_marks_every_unavailable_output_missing():
    snapshot = _engine().build_snapshot("report", {})

    unavailable = {name for name, value in snapshot.items() if value is None}
    assert "total_rank_score" in unavailable
    assert unavailable <= set(snapshot["missing_fields"])
    assert {"sample_quality", "evidence_refs", "source_features"} <= set(snapshot["missing_fields"])
    for name in snapshot["missing_fields"]:
        assert f"missing_fields:{name}" in snapshot["data_quality"].split("|")


def test_snapshot_preserves_evidence_quality_and_zero_values_without_mutation():
    from copy import deepcopy

    candidate = {
        "id": "report-AAPL-1", "ticker": "AAPL", "market": "US", "horizon": 20,
        "raw_score": 0, "wins": 0, "samples": 0, "expected_excess": 0,
        "signal": "hold", "rank": 0, "risk_score": 0, "source_coverage": 0,
        "freshness_seconds": 0, "parameter_version": "params-v1",
        "evidence_references": {"url": "https://example.com/evidence", "status": "blocked"},
        "missing_fields": [" body_available ", "missing_fields:body_available"],
        "data_quality": "blocked",
    }
    source = {
        "coverage": 1, "feature_version": "features-v1",
        "missing_fields": ["body_available", "macro_feature"], "data_quality": ["partial"],
    }
    original_candidate, original_source = deepcopy(candidate), deepcopy(source)
    snapshot = _engine().build_snapshot(
        "report", candidate, source_features=source,
        as_of="2026-09-27T09:00:00+00:00", model_version="model-v1",
    )

    assert snapshot["missing_fields"] == ["body_available", "macro_feature"]
    assert snapshot["data_quality"] == "blocked|partial|missing_fields:body_available|missing_fields:macro_feature"
    assert snapshot["evidence_refs"] == [candidate["evidence_references"]]
    assert snapshot["source_features"] == source
    assert snapshot["source_coverage"] == 0
    assert snapshot["sample_size"] == 0
    assert snapshot["raw_score"] == 0
    assert snapshot["asof_date"] == "2026-09-27"
    assert candidate == original_candidate
    assert source == original_source


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


def test_score_calibration_ece_groups_held_out_rows_by_calibrated_probability():
    engine = _engine()
    rows = []
    for index, score in enumerate((10.0, 20.0, 30.0, 40.0, 50.0)):
        rows.extend(
            {"raw_score": score, "positive_excess": int(offset < index * 5), "split": "train"}
            for offset in range(20)
        )
    rows.extend([
        {"raw_score": 10.0, "positive_excess": 0, "split": "holdout"},
        {"raw_score": 20.0, "positive_excess": 1, "split": "holdout"},
        {"raw_score": 30.0, "positive_excess": 0, "split": "holdout"},
        {"raw_score": 40.0, "positive_excess": 1, "split": "holdout"},
        {"raw_score": 50.0, "positive_excess": 0, "split": "holdout"},
    ])

    calibrator = engine.fit_score_calibrator(rows)

    assert calibrator["ece"] == pytest.approx(0.5)
    assert calibrator["brier"] == pytest.approx(0.375)


def test_ece_pools_equal_calibrated_probabilities_and_weights_by_sample_count():
    rows = []
    for score, positives in ((10, 0), (20, 15), (30, 5), (40, 15), (50, 20)):
        rows.extend(
            {"raw_score": score, "positive_excess": int(i < positives), "split": "train"}
            for i in range(20)
        )
    rows.extend(
        {"raw_score": score, "positive_excess": target, "split": "holdout"}
        for score, target in ((20, 0), (30, 1), (50, 0))
    )

    calibrator = _engine().fit_score_calibrator(rows)

    assert [bin_["probability"] for bin_ in calibrator["bins"]] == [0, 0.5, 0.5, 0.75, 1]
    assert calibrator["metrics"]["sample_size"] == 3
    assert calibrator["ece"] == pytest.approx(1 / 3)
    assert calibrator["brier"] == pytest.approx(0.5)


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
    assert active[-1]["news_contribution"] == 0.0


def test_active_rank_never_promotes_invalid_filtered_or_unscored_rows():
    rows = [
        {"ticker": "INVALID", "raw_score": 1.0, "selection_status": "invalid"},
        {"ticker": "FILTERED", "raw_score": 0.9, "selection_status": "filtered"},
        {"ticker": "MISSING", "raw_score": None},
        {"ticker": "VALID", "raw_score": 0.7},
    ]
    ranked = _engine().rank_snapshots(rows, top_n=4, active=True, gate={"passed": True})
    assert [row["ticker"] for row in ranked] == ["VALID"]


def test_calibrator_requires_minimum_training_bin_even_with_large_holdout():
    rows = [{"raw_score": 0.1, "positive_excess": 1, "split": "train"}]
    rows += [{"raw_score": 0.2, "positive_excess": 0, "split": "holdout"}] * 100
    assert _engine().fit_score_calibrator(rows) is None


def test_snapshot_does_not_relabel_absolute_return_as_excess():
    snapshot = _engine().build_snapshot("report", {"raw_score": 0.5, "expected_ret_20d": 0.1})
    assert snapshot["expected_excess"] is None
    assert "expected_excess" in snapshot["missing_fields"]


@pytest.mark.parametrize("inherited_rank", [False, True])
def test_active_ranking_clears_only_resolved_rank_missingness(inherited_rank):
    from copy import deepcopy

    engine = _engine()
    candidate = {
        "raw_score": 0.6,
        "missing_fields": ["raw_score", "body_available"],
        "data_quality": "blocked|missing_fields:rank_context",
    }
    source = {"missing_fields": ["macro_feature"], "data_quality": "partial"}
    if inherited_rank:
        candidate["missing_fields"].append("rank")
        source["missing_fields"].append("rank")
        source["data_quality"] += "|missing_fields:rank"
    snapshot = engine.build_snapshot("report", candidate, source_features=source)
    original = deepcopy(snapshot)

    ranked = engine.rank_snapshots([snapshot], active=True, gate={"passed": True})[0]

    assert ranked["rank"] == ranked["selection_rank"] == 1
    assert ranked["missing_fields"] == [name for name in original["missing_fields"] if name != "rank"]
    assert ranked["data_quality"].split("|") == [
        issue for issue in original["data_quality"].split("|") if issue != "missing_fields:rank"
    ]
    assert snapshot == original


@pytest.mark.parametrize("active,gate", [(False, {"passed": True}), (True, {"passed": False}), (True, None)])
def test_shadow_ranking_preserves_unresolved_rank_missingness(active, gate):
    engine = _engine()
    snapshot = engine.build_snapshot("report", {"raw_score": 0.6})

    ranked = engine.rank_snapshots([snapshot], active=active, gate=gate)[0]

    assert ranked["rank"] is None
    assert "rank" in ranked["missing_fields"]
    assert "missing_fields:rank" in ranked["data_quality"].split("|")
    assert ranked["missing_fields"] == snapshot["missing_fields"]
    assert ranked["data_quality"] == snapshot["data_quality"]


def test_active_ranking_marks_quality_complete_when_only_rank_was_missing():
    snapshot = {"raw_score": 0.6, "missing_fields": ["rank"], "data_quality": "missing_fields:rank"}

    ranked = _engine().rank_snapshots([snapshot], active=True, gate={"passed": True})[0]

    assert ranked["rank"] == 1
    assert ranked["missing_fields"] == []
    assert ranked["data_quality"] == "complete"


def test_calibrate_score_rejects_non_finite_inputs():
    engine = _engine()

    assert engine.calibrate_score(math.nan, {"bins": []}) is None
    assert engine.calibrate_score(0.5, None) is None
