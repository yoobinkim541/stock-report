from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lib import rank_shadow
from reports import investment_report as report


def test_accepted_calibration_is_scoped_cached_and_preserves_order(monkeypatch):
    from ml import recommendation_calibration as calibration
    from ml import recommendation_surfaces as surfaces

    calls = []
    artifact = {"version": "calibration-v1", "source_snapshot": {"digest": "a" * 64},
                "validation_end_date": "2026-09-26", "validation_metrics": {"brier": .2},
                "scope": {"surface": "entry_signals"},
                "calibrator": {"bins": [{"max_score": 1.0, "probability": .7}]}}

    def load(**kwargs):
        calls.append(kwargs)
        return artifact

    monkeypatch.setattr(calibration, "load_accepted_calibrator", load)
    monkeypatch.setattr(surfaces, "source_metadata", lambda *a, **kw: {})
    monkeypatch.setenv("RECOMMENDER_V2_MODE", "shadow")
    rows = [{"ticker": "A", "score": .2, "model_version": "entry-v1",
             "parameter_version": "default", "wins": 1, "sample_size": 10},
            {"ticker": "B", "score": .9, "metadata": {"model_version": "entry-v1",
                                                        "parameter_version": "default"}}]
    result = surfaces.snapshot_candidates("entry_signals", rows, as_of="2026-09-27T12:00:00Z")
    assert len(calls) == 1
    assert calls[0]["market"] == "US"
    assert calls[0]["horizon"] == "20d"
    assert calls[0]["model_version"] == "entry-v1"
    assert [row["ticker"] for row in result] == ["A", "B"]
    assert [row["raw_score"] for row in result] == [.2, .9]
    assert all(row["calibrated_probability"] == .7 for row in result)
    assert result[0]["calibration"]["source_digest"] == "a" * 64
    assert result[0]["uncertainty_low"] is None
    assert "calibrated_probability" not in result[1]["missing_fields"]


def test_calibration_failure_or_unknown_scale_retains_baseline(monkeypatch):
    from ml import recommendation_calibration as calibration
    from ml import recommendation_surfaces as surfaces

    calls = []

    def load(**kwargs):
        calls.append(kwargs)
        raise OSError("artifact unavailable")

    monkeypatch.setattr(calibration, "load_accepted_calibrator", load)
    monkeypatch.setattr(surfaces, "source_metadata", lambda *a, **kw: {})
    row = {"ticker": "A", "score": .8, "model_version": "entry-v1"}
    result = surfaces.snapshot_candidates("entry_signals", [row])[0]
    assert result["raw_score"] == .8
    assert result["calibrated_probability"] is None
    assert result["calibration"]["status"] == "unavailable"
    assert len(calls) == 1
    calls.clear()
    report_row = surfaces.snapshot_candidates("investment_report", [row])[0]
    assert calls == []
    assert report_row["calibration"]["status"] == "unknown_scale"


def test_calibration_does_not_activate_unscoped_ranker(monkeypatch):
    from ml import recommendation_calibration as calibration
    from ml import recommendation_surfaces as surfaces
    from ml import ranker

    monkeypatch.setattr(calibration, "load_accepted_calibrator", lambda **kw: None)
    monkeypatch.setattr(surfaces, "source_metadata", lambda *a, **kw: {})
    monkeypatch.setattr(ranker, "load_ranker", lambda: SimpleNamespace(meta={"gate": {"passed": True}}))
    monkeypatch.setenv("RECOMMENDER_V2_MODE", "active")
    result = surfaces.snapshot_candidates("entry_signals", [{"ticker": "A", "score": .1},
                                                              {"ticker": "B", "score": .9}], allow_active=True)
    assert [r["ticker"] for r in result] == ["A", "B"]
    assert all(r["ranking_mode"] == "shadow" for r in result)


def test_calibration_lookup_is_at_decision_time_not_later_batch_time(monkeypatch):
    from ml import recommendation_calibration as calibration
    from ml import recommendation_surfaces as surfaces

    calls = []
    monkeypatch.setattr(calibration, "load_accepted_calibrator", lambda **kw: calls.append(kw))
    monkeypatch.setattr(surfaces, "source_metadata", lambda *a, **kw: {})
    rows = [{"ticker": "A", "score": .5, "snapshot_ts": "2026-08-01T12:00:00Z"},
            {"ticker": "B", "score": .5, "decision_ts": "2026-08-02T12:00:00Z"}]
    surfaces.snapshot_candidates("entry_signals", rows, as_of="2026-09-27T12:00:00Z")
    assert [kw["as_of"] for kw in calls] == ["2026-08-01T12:00:00+00:00", "2026-08-02T12:00:00+00:00"]


@pytest.mark.parametrize("row", [
    {"model_version": ["bad"]},
    {"model_version": "a", "metadata": {"model_version": "b"}},
    {"parameter_version": {}, "model_version": "entry-v1"},
])
def test_calibration_rejects_invalid_or_conflicting_version_metadata(monkeypatch, row):
    from ml import recommendation_calibration as calibration
    from ml import recommendation_surfaces as surfaces

    monkeypatch.setattr(calibration, "load_accepted_calibrator", lambda **kw: pytest.fail("invalid lookup"))
    monkeypatch.setattr(surfaces, "source_metadata", lambda *a, **kw: {})
    result = surfaces.snapshot_candidates("entry_signals", [{"ticker": "A", "score": .5, **row}])[0]
    assert result["calibration"]["status"] == "invalid_metadata"


class Ledger:
    def __init__(self):
        self.rows = []

    def log_decision(self, row):
        self.rows.append(row)

    def append_journal(self, *args):
        pass


def test_batch_sources_are_bounded_and_never_load_labels(monkeypatch):
    from ml import recommendation_surfaces as surfaces
    from providers import news_labels

    monkeypatch.setattr(news_labels, "load_labels", lambda: pytest.fail("raw labels loaded"))
    calls = []

    class Index:
        def source_health(self):
            return {"saveticker": {"availability": "blocked"}}

        def query_events(self, **kwargs):
            calls.append(kwargs)
            return [{"ticker": "A", "event_at": "2026-09-27T10:00:00+00:00",
                     "published_at": "2026-09-27T10:00:00+00:00",
                     "collected_at": "2026-09-27T10:01:00+00:00",
                     "source_family": "news", "source": "saveticker", "event_id": "e1",
                     "content_hash": "h", "raw_path": "events.jsonl", "raw_line": 1}]

    rows = surfaces.snapshot_candidates(
        "investment_report", [{"ticker": "A", "total_score": 70, "expected_ret": .3},
                              {"ticker": "B", "total_score": 60}],
        as_of="2026-09-27T12:00:00+00:00", index=Index(), selected_tickers={"A"})
    assert len(calls) == 1
    assert set(calls[0]["tickers"]) == {"A", "B"}
    assert 0 < calls[0]["limit"] <= 10000
    assert rows[0]["evidence_refs"][0]["raw_line"] == 1
    assert "blocked" in rows[0]["data_quality"]
    assert rows[0]["expected_excess"] is None
    assert rows[0]["selection_status"] == "selected"
    assert rows[1]["selection_status"] == "shadow"


def test_report_shadow_preserves_legacy_picks_and_full_candidate_set(monkeypatch):
    monkeypatch.setenv("RECOMMENDER_V2_MODE", "shadow")
    rows = [{"ticker": "A", "total_score": 90, "signal": "Neutral"},
            {"ticker": "B", "total_score": 70, "signal": "Positive"},
            {"ticker": "C", "total_score": 0, "signal": "Negative"}]
    picks = report._select_top_buy_candidates(rows, limit=1)
    assert picks == [rows[1]]
    assert all("recommendation_v2_shadow" in row for row in rows)
    data = report._build_json_data("2026-09-27", {}, rows, picks, [], [], [], [], [], str, [])
    assert len(data["recommendation_v2_shadow"]) == 3
    assert rows[1]["selection_status"] == "selected"
    assert rows[2]["selection_status"] == "filtered"


def test_active_recomputes_combined_gate_not_cached_flag(monkeypatch):
    from ml import recommendation_surfaces as surfaces
    from ml import ranker

    monkeypatch.setenv("RECOMMENDER_V2_MODE", "active")
    monkeypatch.setattr(ranker, "load_ranker", lambda: SimpleNamespace(
        meta={"gate": {"passed": True}}, train_end_date="2026-09-27",
        oos_ic=.2, oos_top_decile_ret=.1))
    rows = [{"ticker": "A", "score": .2}, {"ticker": "B", "score": .9}]
    snapshots = surfaces.snapshot_candidates("investment_report", rows, allow_active=True)
    assert [r["ticker"] for r in snapshots] == ["A", "B"]
    assert all(r["ranking_mode"] == "shadow" for r in snapshots)


def test_active_gate_is_scoped_to_surface_horizon_and_model_version(monkeypatch):
    from ml import recommendation_surfaces as surfaces
    from ml import ranker

    monkeypatch.setenv("RECOMMENDER_V2_MODE", "active")
    model = SimpleNamespace(meta={"surface": "investment_report", "horizon": "20d",
                                  "model_version": "report-v2"})
    monkeypatch.setattr(ranker, "load_ranker", lambda: model)
    monkeypatch.setattr(ranker, "ranker_gate", lambda _: {"passed": True})
    rows = [{"ticker": "A", "score": .2, "model_version": "report-v2"},
            {"ticker": "B", "score": .9, "model_version": "report-v2"}]

    ranked = surfaces.snapshot_candidates("investment_report", rows, top_n=1, allow_active=True)
    assert [r["ticker"] for r in ranked] == ["B", "A"]
    assert [r["selection_status"] for r in ranked] == ["selected", "shadow"]
    model.meta["horizon"] = "60d"
    blocked = surfaces.snapshot_candidates("investment_report", rows, top_n=1, allow_active=True)
    assert [r["ticker"] for r in blocked] == ["A", "B"]
    assert all(r["ranking_mode"] == "shadow" for r in blocked)


def test_index_query_failure_is_counted_as_degraded_fallback(tmp_path, monkeypatch):
    from ml import recommendation_surfaces as surfaces
    from reports.recommendation_index import RecommendationIndex

    index = RecommendationIndex(tmp_path / "index.db", tmp_path)
    index.ingest()

    def fail_query(**kwargs):
        raise RuntimeError("database busy")

    monkeypatch.setattr(index, "query_events", fail_query)
    result = surfaces.source_metadata(["AAPL"], "2026-09-27T12:00:00+00:00", index=index)
    assert "index_unavailable" in result["AAPL"]["data_quality"]
    assert index.status()["fallback_count"] == 1


def test_active_report_keeps_snapshot_bound_to_original_candidate(monkeypatch):
    from ml import ranker

    monkeypatch.setenv("RECOMMENDER_V2_MODE", "active")
    monkeypatch.setattr(ranker, "load_ranker", lambda: SimpleNamespace(meta={
        "surface": "investment_report", "horizon": "20d", "model_version": "report-v2",
    }))
    monkeypatch.setattr(ranker, "ranker_gate", lambda _: {"passed": True})
    rows = [
        {"ticker": "LOW", "total_score": 90, "raw_score": .9, "risk_score": 1.0,
         "signal": "Positive", "model_version": "report-v2"},
        {"ticker": "HIGH", "total_score": 80, "raw_score": .8, "risk_score": 0.0,
         "signal": "Positive", "model_version": "report-v2"},
        {"ticker": "FILTER", "total_score": 0, "signal": "Negative", "model_version": "report-v2"},
    ]
    picks = report._select_top_buy_candidates(rows, limit=1)
    assert [row["ticker"] for row in picks] == ["HIGH"]
    assert all(row["recommendation_v2_shadow"]["ticker"] == row["ticker"] for row in rows)
    assert rows[2]["selection_status"] == "filtered"


@pytest.mark.parametrize("market,module,symbol", [
    ("US", "crons.us_mock_track", "A"), ("KR", "crons.kiwoom_mock_track", "A"),
])
def test_mock_shadow_and_selected_decisions_share_metadata(market, module, symbol):
    import importlib
    tracker = importlib.import_module(module)
    sigs = [{"ticker": "A", "code": "A", "policy_score": .9},
            {"ticker": "B", "code": "B", "policy_score": .2}]
    shadow, selected = Ledger(), Ledger()
    assert rank_shadow.log_ranked_candidates(shadow, sigs, today="2026-09-27", market=market) == 2
    tracker._log_decision(selected, sigs[0], symbol, "buy", "buy", 1, True, "2026-09-27")
    for key in ("selection_rank", "data_quality", "source_coverage", "evidence_refs"):
        assert key in selected.rows[0]
        assert selected.rows[0][key] == shadow.rows[0][key]
    assert selected.rows[0]["selection_status"] == "selected"
    assert shadow.rows[1]["selection_status"] == "shadow"
