from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ml import ranker


def evidence():
    return dict(n_folds=3, purged=True, fold_ics=[.05, .06, .07],
                mean_ic=.06, top_decile_excess=.03, transaction_cost=.01,
                mdd=.12, brier=.19, ece=.03, clean_mean_ic=.02,
                clean_top_decile_excess=.02)


def baseline():
    return dict(mean_ic=.04, mdd=.10, brier=.20, ece=.02)


def candidate(**changes):
    now = datetime.now(timezone.utc).isoformat()
    values = dict(model=None, feature_names=[], train_end_date=now,
                  oos_ic=.06, oos_icir=1., oos_top_decile_ret=.03,
                  oos_hit_rate=.6, feature_importance=None,
                  meta={"trained_at": now, "validation_metrics": evidence(),
                        "validation_baseline": baseline()})
    values.update(changes)
    return ranker.RankerResult(**values)


def test_gate_passes_complete_evidence():
    from ml.recommendation_validation import recommendation_gate
    assert recommendation_gate(evidence(), baseline())["passed"]


@pytest.mark.parametrize("key,value", [
    ("n_folds", 2), ("n_folds", 3.5), ("purged", False),
    ("fold_ics", [.05, float("nan"), .07]), ("mean_ic", .049),
    ("top_decile_excess", .01), ("mdd", .121), ("brier", .201),
    ("ece", .04), ("clean_mean_ic", 0), ("clean_top_decile_excess", -.01),
])
def test_gate_rejects_threshold_breaches(key, value):
    from ml.recommendation_validation import recommendation_gate
    metrics = evidence()
    metrics[key] = value
    decision = recommendation_gate(metrics, baseline())
    assert not decision["passed"]
    assert decision["reasons"]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf")])
def test_every_required_metric_fails_closed(value):
    from ml.recommendation_validation import recommendation_gate
    for key in evidence():
        metrics = evidence()
        metrics[key] = value
        assert not recommendation_gate(metrics, baseline())["passed"], key
    for key in baseline():
        ref = baseline()
        ref[key] = value
        assert not recommendation_gate(evidence(), ref)["passed"], key
    assert not recommendation_gate(evidence())["passed"]


def test_calibration_actual_probabilities_and_invalid_inputs():
    from ml.recommendation_validation import validate_calibration
    result = validate_calibration([0, 1], [.2, .8])
    assert result["valid"]
    assert result["brier"] == pytest.approx(.04)
    assert result["ece"] == pytest.approx(.2)
    for y, p in [([], []), ([1], []), ([1], [float("nan")]),
                 ([2], [.5]), ([1], [1.1])]:
        result = validate_calibration(y, p)
        assert not result["valid"]
        assert result["brier"] is None and result["ece"] is None


def test_freshness_uses_training_end_not_recent_file_creation():
    result = candidate(train_end_date="2025-04-14")
    status = ranker.ranker_freshness(result)
    assert status["shadow_only"] and status["stale"]
    assert status["train_end_date"] == "2025-04-14"
    assert ranker.ranker_freshness(candidate(oos_ic=-.01))["shadow_only"]
    assert not ranker.ranker_freshness(candidate())["shadow_only"]
    assert ranker.ranker_freshness(SimpleNamespace())["shadow_only"]


def test_direct_save_cannot_overwrite_cache_without_gate(tmp_path):
    path = tmp_path / "ranker.pkl"
    path.write_bytes(b"existing artifact")
    assert ranker.save_ranker(candidate(meta={}), path) is False
    assert path.read_bytes() == b"existing artifact"


@pytest.mark.parametrize("symlink", [False, True])
def test_passing_candidate_preserves_unreadable_cache(tmp_path, symlink):
    path = tmp_path / "ranker.pkl"
    if symlink:
        path.symlink_to(tmp_path / "missing-target.pkl")
    else:
        path.write_bytes(b"unreadable artifact")
    assert ranker.adopt_if_better(candidate(), path) == (False, None)
    if symlink:
        assert path.is_symlink()
        assert not path.exists()
    else:
        assert path.read_bytes() == b"unreadable artifact"


@pytest.mark.parametrize("changes", [
    {"train_end_date": "2025-04-14"}, {"oos_ic": -.01},
    {"oos_top_decile_ret": -.01},
])
def test_ineligible_cached_model_is_preserved_and_not_scored(monkeypatch, tmp_path, changes):
    import pickle
    from ml import data_pipeline

    path = tmp_path / "ranker.pkl"
    result = candidate(**changes)
    result.meta["gate"] = {"passed": True}
    artifact = pickle.dumps(result)
    path.write_bytes(artifact)

    def forbidden(*args, **kwargs):
        pytest.fail("ineligible cache triggered training or production scoring")

    monkeypatch.setattr(data_pipeline, "build_ml_dataset", forbidden)
    monkeypatch.setattr(data_pipeline, "fetch_universe", forbidden)
    assert ranker.rank_today(cache_path=path).empty
    assert path.read_bytes() == artifact


def test_gate_accepts_inclusive_ic_and_mdd_boundaries():
    from ml.recommendation_validation import recommendation_gate
    metrics = evidence()
    metrics.update(mean_ic=.05, fold_ics=[.05] * 3, mdd=.12)
    assert recommendation_gate(metrics, baseline())["passed"]


def test_adoption_requires_gate_even_without_champion(monkeypatch, tmp_path):
    path = tmp_path / "missing.pkl"
    monkeypatch.setattr(ranker, "load_ranker", lambda path: None)
    assert ranker.adopt_if_better(candidate(meta={}), path) == (False, None)
    assert not path.exists()


def test_adoption_passes_both_gates_and_saves(tmp_path):
    path = tmp_path / "ranker.pkl"
    assert ranker.adopt_if_better(candidate(), path) == (True, None)
    assert path.exists()


def test_cached_unvalidated_ranker_never_reaches_production_features(monkeypatch):
    from ml import data_pipeline
    def forbidden(*args, **kwargs):
        pytest.fail("unvalidated model reached production features")
    monkeypatch.setattr(data_pipeline, "fetch_universe", forbidden)
    monkeypatch.setattr(ranker, "load_ranker", lambda path: candidate(meta={}))
    assert ranker.rank_today().empty


def test_weekly_runs_wf_before_adoption_and_reports_missing_metrics(monkeypatch):
    from crons import weekly_ranker_retrain as weekly
    from ml import data_pipeline
    calls, messages = [], []
    result = candidate(meta={})
    monkeypatch.setattr(data_pipeline, "build_ml_dataset", lambda **kw: {})
    monkeypatch.setattr(ranker, "train_ranker", lambda ds: result)
    def wf(ds, **kw):
        calls.append("wf")
        return {"n_folds": 3, "purged": True, "fold_ics": [.05]*3,
                "mean_ic": .05, "icir": 1.}
    def adopt(r, **kw):
        calls.append("adopt")
        assert r.meta["validation_metrics"]["n_folds"] == 3
        return False, None
    monkeypatch.setattr(ranker, "walk_forward_backtest", wf)
    monkeypatch.setattr(ranker, "adopt_if_better", adopt)
    monkeypatch.setattr(weekly, "send_cron_telegram", messages.append)
    monkeypatch.setenv("RANKER_XGB_CHALLENGER_ENABLED", "0")
    assert weekly.main() == 0
    assert calls == ["wf", "adopt"]
    assert "Brier" in messages[0] and "coverage" in messages[0]
    assert "shadow" in messages[0]
