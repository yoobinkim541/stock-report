from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lib import rank_shadow
from reports import investment_report as report


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
