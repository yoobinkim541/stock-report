"""Point-in-time artifact production and fail-closed consumption."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from ml import recommendation_calibration as calibration


NOW = datetime.now(timezone.utc) + timedelta(minutes=1)


@pytest.fixture(autouse=True)
def fixed_training_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = NOW - timedelta(seconds=1)
            return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)

    monkeypatch.setattr(calibration, "datetime", Clock)


def records(n=10, *, model="m1", parameter="p1"):
    start = NOW - timedelta(days=40)
    decisions, outcomes = [], []
    for i in range(n):
        stamp = start + timedelta(days=i)
        decisions.append({"id": f"d{i}", "decision_ts": stamp.isoformat(),
                          "market": "US", "raw_score": i / n,
                          "metadata": {"model_version": model, "parameter_version": parameter}})
        outcomes.append({"decision_id": f"d{i}:h20", "horizon": 20,
                         "status": "matured", "fwd_excess": .1 if i % 2 else -.1,
                         "matured_at": (stamp + timedelta(hours=2)).isoformat()})
    return decisions, outcomes


@pytest.fixture
def fitted(monkeypatch):
    calls = []

    def fit(rows, **kwargs):
        calls.append((copy.deepcopy(rows), kwargs))
        held_out = sum(r["split"] == "holdout" for r in rows)
        return {"version": "test-fit", "score_range": list(kwargs["score_range"]), "bins": [
            {"min_score": 0., "max_score": 1., "probability": .5, "count": 5}],
            "acceptance": {"accepted": True, "reasons": []},
            "baseline_metrics": {"valid": True, "n_samples": held_out, "brier": .3, "ece": .3, "reasons": []},
            "validation_metrics": {"valid": True, "n_samples": held_out, "brier": .2, "ece": .2, "reasons": []}}

    monkeypatch.setattr(calibration, "fit_score_calibrator", fit)
    return calls


def train(tmp_path, decisions, outcomes, **kwargs):
    return calibration.train_artifacts(
        decisions, outcomes, as_of=NOW, base_dir=tmp_path,
        min_samples=4, min_bin_size=2, min_holdout_samples=2, **kwargs)


def load(tmp_path, **kwargs):
    return calibration.load_accepted_calibrator(
        "entry_signals", "20d", "US", kwargs.pop("model_version", "m1"),
        kwargs.pop("parameter_version", "p1"), kwargs.pop("as_of", NOW),
        base_dir=tmp_path, **kwargs)


def test_pit_availability_purge_and_whole_day_holdout(tmp_path, fitted):
    decisions, outcomes = records()
    decisions[9]["decision_ts"] = decisions[8]["decision_ts"]
    outcomes[0]["matured_at"] = decisions[8]["decision_ts"]  # equal boundary is purged
    outcomes[1]["exit_date"] = (NOW + timedelta(days=1)).isoformat()
    outcomes[2].pop("matured_at")
    outcomes[3]["matured_at"] = (NOW + timedelta(days=1)).isoformat()
    artifacts = train(tmp_path, decisions, outcomes)
    artifact = artifacts[0]
    rows, kwargs = fitted[0]
    assert artifact["counts"]["purged"] == 1
    assert artifact["rejections"]["future_outcome_availability"] == 2
    assert artifact["rejections"]["missing_outcome_timestamp"] == 1
    assert {r["decision_id"] for r in rows} == {"d4", "d5", "d6", "d7", "d8", "d9"}
    assert {r["decision_id"] for r in rows if r["split"] == "holdout"} == {"d8", "d9"}
    assert kwargs["score_range"] == (0., 1.)
    assert kwargs["min_holdout_samples"] == 2
    assert all(datetime.fromisoformat(r["decision_ts"]).tzinfo for r in rows)
    assert artifact["scope"]["model_version"] == "m1"
    assert load(tmp_path) == artifact


def test_canonical_horizons_dedupe_without_cross_horizon_labels(tmp_path, fitted):
    decisions, outcomes = records()
    outcomes += [{**o, "decision_id": o["decision_id"] + "d", "horizon": "20d"}
                 for o in list(outcomes)]
    outcomes += [{**o, "decision_id": o["decision_id"].replace("h20", "h60"), "horizon": 60}
                 for o in outcomes[:10]]
    artifacts = train(tmp_path, decisions, outcomes)
    assert {a["scope"]["horizon"] for a in artifacts} == {"20d", "60d"}
    assert all(a["counts"]["joined"] == 10 for a in artifacts)
    assert sum(a["counts"]["duplicate_outcomes"] for a in artifacts) == 10
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_versions_separate_scopes_and_reader_matches_scale(tmp_path, fitted):
    d, o = records()
    d2, o2 = records(model="m2", parameter="p2")
    for row in d2:
        row["id"] = "other-" + row["id"]
    for row in o2:
        row["decision_id"] = "other-" + row["decision_id"]
    artifacts = train(tmp_path, d + d2, o + o2)
    assert len(artifacts) == 2
    assert load(tmp_path, model_version="m2", parameter_version="p2") is not None
    assert load(tmp_path, model_version="m2") is None
    assert load(tmp_path, score_range=(0, 100)) is None
    assert calibration.load_accepted_calibrator("other", "20d", "US", "m1", "p1", NOW,
                                                base_dir=tmp_path) is None


def test_unknown_versions_reject_without_fake_metadata(tmp_path, fitted):
    d, o = records(model=None, parameter=None)
    artifact = train(tmp_path, d, o)[0]
    assert artifact["scope"]["model_version"] is None
    assert artifact["source_score_versions"] == []
    assert "unknown_source_score_version" in artifact["acceptance"]["reasons"]
    assert calibration.load_accepted_calibrator("entry_signals", 20, "US", None, None, NOW,
                                                base_dir=tmp_path) is None


def test_score_version_alone_cannot_authorize_lookup(tmp_path, fitted):
    d, o = records(model=None, parameter=None)
    for row in d:
        row["score_version"] = "sv1"
    artifact = train(tmp_path, d, o)[0]
    assert artifact["source_score_versions"] == ["sv1"]
    assert artifact["acceptance"]["accepted"] is False
    assert "unknown_lookup_score_version" in artifact["acceptance"]["reasons"]
    # Even an old/tampered acceptance flag cannot authorize a null-version scope.
    artifact["acceptance"] = {"accepted": True, "reasons": []}
    next(tmp_path.glob("*.json")).write_text(json.dumps(artifact))
    assert load(tmp_path, model_version=None, parameter_version=None) is None


@pytest.mark.parametrize("model,parameter", [("m1", None), (None, "p1")])
def test_one_known_lookup_version_remains_sufficient(tmp_path, fitted, model, parameter):
    d, o = records(model=model, parameter=parameter)
    for outcome in o:
        outcome.update(model_version=model, parameter_version=parameter,
                       metadata={"model_version": model, "parameter_version": parameter})
    artifact = train(tmp_path, d, o)[0]
    assert artifact["acceptance"]["accepted"] is True
    assert load(tmp_path, model_version=model, parameter_version=parameter) == artifact


@pytest.mark.parametrize("payload,reason", [
    ({"model_version": "other"}, "outcome_version_mismatch:model_version"),
    ({"metadata": {"parameter_version": "other"}}, "outcome_version_mismatch:parameter_version"),
    ({"model_version": "m1", "metadata": {"model_version": "other"}}, "conflicting_outcome_version:model_version"),
    ({"parameter_version": "p1", "metadata": {"parameter_version": None}}, "conflicting_outcome_version:parameter_version"),
    ({"model_version": ["m1"]}, "invalid_outcome_version:model_version"),
    ({"metadata": {"parameter_version": 7}}, "invalid_outcome_version:parameter_version"),
    ({"model_version": None}, "outcome_version_mismatch:model_version"),
    ({"metadata": []}, "invalid_outcome_metadata"),
])
def test_outcome_version_provenance_is_validated(tmp_path, fitted, payload, reason):
    d, o = records()
    o[0].update(payload)
    artifact = train(tmp_path, d, o)[0]
    assert artifact["counts"]["joined"] == 9
    assert artifact["rejections"][reason] == 1
    assert "d0" not in {r["decision_id"] for r in fitted[0][0]}


@pytest.mark.parametrize("payload,reason", [
    ({"base_decision_id": "d0", "decision_id": "foreign:h20"}, "conflicting_outcome_identity"),
    ({"base_decision_id": "foreign"}, "conflicting_outcome_identity"),
    ({"base_decision_id": ["d0"]}, "invalid_outcome_base_id"),
    ({"base_decision_id": "d0", "decision_id": None}, "invalid_outcome_decision_id"),
])
def test_explicit_base_identity_must_agree_with_encoded_id(tmp_path, fitted, payload, reason):
    d, o = records()
    o[0].update(payload)
    artifact = train(tmp_path, d, o)[0]
    assert artifact["counts"]["joined"] == 9
    assert artifact["source_rejections"][reason] == 1
    assert "d0" not in {r["decision_id"] for r in fitted[0][0]}


def test_explicit_base_identity_accepts_canonical_horizon_aliases(tmp_path, fitted):
    d, o = records()
    for i, outcome in enumerate(o):
        outcome.update(base_decision_id=f"d{i}", decision_id=f"d{i}:h20d")
    artifact = train(tmp_path, d, o)[0]
    assert artifact["counts"]["joined"] == 10
    assert artifact["acceptance"]["accepted"] is True


@pytest.mark.parametrize("metric_key", ["baseline_metrics", "validation_metrics"])
@pytest.mark.parametrize("damage", ["invalid_flag", "missing_flag", "bad_count", "missing_count",
                                    "brier_regression", "ece_boundary"])
def test_loader_rechecks_validation_evidence_and_baseline(tmp_path, fitted, metric_key, damage):
    artifact = train(tmp_path, *records())[0]
    evidence = artifact[metric_key]
    if damage == "invalid_flag":
        evidence["valid"] = False
    elif damage == "missing_flag":
        evidence.pop("valid")
    elif damage == "bad_count":
        evidence["n_samples"] = 0
    elif damage == "missing_count":
        evidence.pop("n_samples")
    elif damage == "brier_regression":
        evidence["brier"] = .9 if metric_key == "validation_metrics" else .1
    else:
        if metric_key == "validation_metrics":
            evidence["ece"] = artifact["baseline_metrics"]["ece"] + .02
        else:
            evidence["ece"] = 0.
            artifact["validation_metrics"]["ece"] = .02
            artifact["calibrator"]["validation_metrics"] = copy.deepcopy(artifact["validation_metrics"])
    artifact["calibrator"][metric_key] = copy.deepcopy(evidence)
    next(tmp_path.glob("*.json")).write_text(json.dumps(artifact))
    assert load(tmp_path) is None


def test_loader_accepts_brier_equality_and_ece_below_tolerance(tmp_path, fitted):
    artifact = train(tmp_path, *records())[0]
    artifact["validation_metrics"].update(brier=.3, ece=.319)
    artifact["calibrator"]["validation_metrics"] = copy.deepcopy(artifact["validation_metrics"])
    next(tmp_path.glob("*.json")).write_text(json.dumps(artifact))
    assert load(tmp_path) == artifact


@pytest.mark.parametrize("damage", ["invalid_flag", "bad_count", "brier_regression", "ece_boundary"])
def test_producer_does_not_trust_fit_acceptance_without_evidence(tmp_path, fitted, monkeypatch, damage):
    original_fit = calibration.fit_score_calibrator

    def bad_fit(rows, **kwargs):
        result = original_fit(rows, **kwargs)
        metric = result["validation_metrics"]
        metric.update({"invalid_flag": {"valid": False}, "bad_count": {"n_samples": 0},
                       "brier_regression": {"brier": .9}, "ece_boundary": {"ece": .32}}[damage])
        return result

    monkeypatch.setattr(calibration, "fit_score_calibrator", bad_fit)
    artifact = train(tmp_path, *records())[0]
    assert artifact["acceptance"]["accepted"] is False
    assert "invalid_fit_evidence" in artifact["acceptance"]["reasons"]
    assert load(tmp_path) is None


def test_fit_rejection_is_persisted_and_never_loaded(tmp_path, monkeypatch):
    def rejected(rows, **kwargs):
        return {"bins": [{"min_score": 0., "max_score": 1., "probability": .5, "count": 5}],
                "acceptance": {"accepted": False, "reasons": ["brier_baseline_regression"]},
                "baseline_metrics": {"brier": .1}, "validation_metrics": {"brier": .2}}
    monkeypatch.setattr(calibration, "fit_score_calibrator", rejected)
    artifact = train(tmp_path, *records())[0]
    assert artifact["acceptance"] == {"accepted": False, "reasons": ["brier_baseline_regression"]}
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["baseline_metrics"] == {"brier": .1}
    assert load(tmp_path) is None


@pytest.mark.parametrize("damage", ["corrupt", "stale", "future", "validation_future", "bad_bins",
                                    "missing_snapshot", "invalid_acceptance", "missing_versions",
                                    "fit_scale", "bad_metrics", "bad_counts"])
def test_reader_rejects_corrupt_stale_and_future(tmp_path, fitted, damage):
    artifact = train(tmp_path, *records())[0]
    path = next(tmp_path.glob("*.json"))
    if damage == "corrupt":
        path.write_text("{broken")
    else:
        if damage == "stale":
            artifact["validation_end_date"] = (NOW - timedelta(days=91)).isoformat()
        elif damage == "future":
            artifact["trained_at"] = (NOW + timedelta(days=1)).isoformat()
        elif damage == "validation_future":
            artifact["validation_end_date"] = (NOW + timedelta(days=1)).isoformat()
        elif damage == "bad_bins":
            artifact["calibrator"]["bins"][0]["probability"] = 2
        elif damage == "missing_snapshot":
            artifact.pop("source_snapshot")
        elif damage == "missing_versions":
            artifact["source_score_versions"] = []
        elif damage == "fit_scale":
            artifact["calibrator"]["score_range"] = [0, 100]
        elif damage == "bad_metrics":
            artifact["validation_metrics"]["brier"] = None
        elif damage == "bad_counts":
            artifact["counts"]["holdout"] = 0
        else:
            artifact["acceptance"]["accepted"] = "true"
        path.write_text(json.dumps(artifact))
    assert load(tmp_path) is None


def test_insufficient_samples_and_missing_acceptance_are_diagnostic(tmp_path, fitted, monkeypatch):
    artifact = train(tmp_path, *records(2))[0]
    assert artifact["calibrator"] is None
    assert "insufficient_samples" in artifact["acceptance"]["reasons"]
    assert not fitted
    monkeypatch.setattr(calibration, "fit_score_calibrator", lambda rows, **kwargs: {"bins": []})
    artifact = train(tmp_path, *records())[0]
    assert "missing_fit_acceptance" in artifact["acceptance"]["reasons"]
    assert load(tmp_path) is None


def test_nonfinite_metrics_duplicates_and_ambiguous_decisions_excluded(tmp_path, fitted):
    d, o = records()
    o[0]["fwd_excess"] = float("nan")
    o[1]["benchmark_ret"] = float("inf")
    d += [{**d[2], "raw_score": .99}, dict(d[3])]
    artifact = train(tmp_path, d, o)[0]
    assert artifact["counts"]["joined"] == 7
    assert artifact["rejections"]["invalid_outcome_metric"] == 2
    assert artifact["rejections"]["conflicting_decision"] == 1


def test_atomic_persistence_preserves_previous_artifact_on_failure(tmp_path, fitted, monkeypatch):
    train(tmp_path, *records())
    path = next(tmp_path.glob("*.json"))
    before = path.read_bytes()
    monkeypatch.setattr("safe_io.os.replace", lambda *args: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        train(tmp_path, *records())
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_nonentry_requires_explicit_scale_and_input_is_bounded(tmp_path, fitted):
    with pytest.raises(ValueError, match="explicit score_range"):
        train(tmp_path, *records(), surface="investment_report")
    with pytest.raises(ValueError, match="max_rows"):
        train(tmp_path, *records(), max_rows=5)
    artifacts = train(tmp_path, *records(), surface="investment_report", score_range=(0, 100))
    assert artifacts[0]["score_range"] == [0., 100.]


def test_raw_training_and_holdout_scores_both_require_declared_scale(tmp_path, fitted):
    d, o = records()
    d[0]["raw_score"] = -.01
    d[9]["raw_score"] = 1.01
    artifact = train(tmp_path, d, o)[0]
    assert artifact["rejections"]["invalid_or_out_of_range_score"] == 2
    assert artifact["counts"]["joined"] == 8
    assert {r["decision_id"] for r in fitted[0][0]} == {f"d{i}" for i in range(1, 9)}
    assert all(0 <= r["raw_score"] <= 1 for r in fitted[0][0])


def test_scope_limit_rejects_before_any_artifact_write(tmp_path, fitted):
    d, o = records()
    o += [{**r, "decision_id": r["decision_id"].replace("h20", "h60"), "horizon": 60}
          for r in list(o)]
    with pytest.raises(ValueError, match="max_scopes"):
        train(tmp_path, d, o, max_scopes=1)
    assert not list(tmp_path.glob("*.json"))


def test_missing_and_naive_timestamps_are_never_imputed(tmp_path, fitted):
    d, o = records()
    d[0]["decision_ts"] = "2026-08-29T12:00:00"
    o[1]["matured_at"] = "2026-08-30T12:00:00"
    o[2]["matured_at"] = "2026-08-31"
    d[2]["decision_ts"] = "2026-08-31T09:00:00+09:00"
    artifact = train(tmp_path, d, o)[0]
    assert artifact["rejections"]["invalid_or_future_decision_timestamp"] == 1
    assert artifact["rejections"]["invalid_outcome_timestamp"] == 1
    row = next(r for r in fitted[0][0] if r["decision_id"] == "d2")
    assert row["decision_ts"] == "2026-08-31T00:00:00+00:00"
    assert row["outcome_available_at"] == "2026-08-31T23:59:59.999999+00:00"


def test_conflicting_horizon_and_metadata_rejected(tmp_path, fitted):
    d, o = records()
    o[0]["horizon"] = 60  # encoded outcome ID disagrees
    d[1]["model_version"] = "different"
    artifact = next(a for a in train(tmp_path, d, o) if a["scope"]["horizon"] == "20d")
    assert artifact["counts"]["joined"] == 8
    assert artifact["source_rejections"]["conflicting_metadata:model_version"] == 1
    assert sum(a["rejections"].get("conflicting_outcome_horizon", 0)
               for a in train(tmp_path, d, o)) == 1


def test_horizon_specific_decisions_join_only_their_own_labels(tmp_path, fitted):
    d, o = records()
    decisions = [{**r, "id": r["id"] + ":h20", "raw_score": .2} for r in d]
    decisions += [{**r, "id": r["id"] + ":h60", "raw_score": .8} for r in d]
    outcomes = o + [{**r, "decision_id": r["decision_id"].replace(":h20", ":h60"), "horizon": 60}
                    for r in o]
    artifacts = train(tmp_path, decisions, outcomes)
    assert all(a["counts"]["joined"] == 10 for a in artifacts)
    assert {r["raw_score"] for r in fitted[0][0]} == {.2}
    assert {r["raw_score"] for r in fitted[1][0]} == {.8}


def test_malformed_accepted_fit_still_writes_rejected_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "fit_score_calibrator", lambda rows, **kwargs: {
        "bins": [{"min_score": 0, "max_score": 1, "probability": .5}],
        "acceptance": {"accepted": True, "reasons": []}})
    artifact = train(tmp_path, *records())[0]
    assert artifact["acceptance"]["accepted"] is False
    assert "invalid_fit_evidence" in artifact["acceptance"]["reasons"]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_real_fit_baseline_rejection_and_accepted_roundtrip(tmp_path):
    d, o = records(100)
    # Compress dates while keeping each label strictly before the next decision.
    start = NOW - timedelta(days=40)
    for i, (decision, outcome) in enumerate(zip(d, o)):
        day = start + timedelta(days=i // 4)
        decision["decision_ts"] = day.isoformat()
        outcome["matured_at"] = (day + timedelta(hours=2)).isoformat()
        decision["raw_score"] = .1 if i % 2 else .9
        outcome["fwd_excess"] = -.1  # raw baseline is better than constant 0 on holdout
    for i in range(80, 100):
        decision = d[i]
        decision["raw_score"] = .99
        o[i]["fwd_excess"] = .1
    rejected = calibration.train_artifacts(d, o, base_dir=tmp_path, as_of=NOW)[0]
    assert rejected["acceptance"]["accepted"] is False
    assert "brier_deterioration" in rejected["acceptance"]["reasons"]
    assert load(tmp_path) is None
    for i in range(100):
        d[i]["raw_score"] = .2 if i % 2 else .8
        o[i]["fwd_excess"] = -.1 if i % 2 else .1
    accepted = calibration.train_artifacts(d, o, base_dir=tmp_path, as_of=NOW)[0]
    assert accepted["acceptance"]["accepted"] is True
    assert load(tmp_path) == accepted


def test_cli_defaults_to_entry_ledger_and_prints_rejection(tmp_path, capsys):
    from crons.recommendation_calibration_refresh import main
    data = tmp_path / "ledger"
    data.mkdir()
    d, o = records(2)
    for name, rows in (("decisions", d), ("outcomes", o)):
        (data / f"entry_signals_{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    assert main(["--ledger-dir", str(data), "--artifact-dir", str(tmp_path / "artifacts"),
                 "--as-of", NOW.isoformat()]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scopes"][0]["scope"]["surface"] == "entry_signals"
    assert "insufficient_samples" in result["scopes"][0]["acceptance"]["reasons"]
    assert len(list((tmp_path / "artifacts").glob("*.json"))) == 1
    assert main(["--ledger-dir", str(data), "--max-rows", "1"]) == 1
    assert "max_rows" in json.loads(capsys.readouterr().out)["error"]
    assert main(["--ledger-dir", str(data), "--max-bytes", "1"]) == 1
    assert "max_bytes" in json.loads(capsys.readouterr().out)["error"]
