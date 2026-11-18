from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _sample_pages() -> list[dict]:
    return [
        {
            "id": "digest-1",
            "title": "SaveTicker digest",
            "kind": "source_digest",
            "status": "reviewed",
            "source_refs": ["source:saveticker:123"],
            "links": [],
            "backlinks": [],
            "openQuestions": ["무엇을 우선 추적할까?"],
            "summary": "요약",
            "body": "본문",
            "tags": ["wiki"],
            "updated_at": "2026-07-31T00:00:00+00:00",
        },
        {
            "id": "risk-1",
            "title": "리스크 판단",
            "kind": "risk",
            "status": "stable",
            "source_refs": ["conversation:abc"],
            "links": [],
            "backlinks": [],
            "summary": "리스크 요약",
            "body": "리스크 본문",
            "tags": ["wiki"],
            "updated_at": "2026-07-31T00:00:00+00:00",
        },
    ]


def _patch_empty_pipeline(monkeypatch, module):
    monkeypatch.setattr(module.source_collector, "load_source_health", lambda: {})
    monkeypatch.setattr(module.source_collector, "stale_sources", lambda *args, **kwargs: [])
    monkeypatch.setattr(module.source_collector, "load_recent_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(module.wiki, "list_pages", lambda **kwargs: [])
    monkeypatch.setattr(module.wiki, "stats", lambda: {"total": 0, "status_counts": {}})
    monkeypatch.setattr(module.wiki, "lint_pages", lambda *args, **kwargs: {"issues": []})
    monkeypatch.setattr(module.wiki, "list_stale_pages", lambda *args, **kwargs: [])
    monkeypatch.setattr(module.wiki, "list_unused_pages", lambda *args, **kwargs: [])
    monkeypatch.setattr(module.evidence_usage, "usage_summary", lambda *args, **kwargs: {})


def test_pipeline_health_report_merges_source_wiki_and_curation(monkeypatch):
    from reports import wiki_pipeline_health

    monkeypatch.setattr(wiki_pipeline_health.news_labels, "load_labels", lambda: [])
    source_health = {
        "saveticker": {
            "first_run": "2026-07-31T00:00:00+00:00",
            "last_run": "2026-07-31T00:30:00+00:00",
            "last_count": 8,
            "last_success": "2026-07-31T00:30:00+00:00",
            "last_success_count": 8,
        },
        "telegram:insidertracking": {
            "first_run": "2026-07-30T00:00:00+00:00",
            "last_run": "2026-07-31T00:30:00+00:00",
            "last_count": 0,
            "last_error": "timeout",
        },
    }

    monkeypatch.setattr(wiki_pipeline_health.source_collector, "load_source_health", lambda: source_health)
    monkeypatch.setattr(
        wiki_pipeline_health.source_collector,
        "stale_sources",
        lambda health=None, now=None, thresholds=None, cache_dir=None: [
            {"source": "telegram:insidertracking", "hours": 18.0, "threshold": 12, "error": "timeout"}
        ],
    )
    monkeypatch.setattr(
        wiki_pipeline_health.source_collector,
        "load_recent_events",
        lambda cache_dir=None, now=None, hours=24: [{"source": "saveticker"}, {"source": "saveticker"}],
    )
    monkeypatch.setattr(wiki_pipeline_health.wiki, "stats", lambda: {
        "total": 2,
        "status_counts": {"reviewed": 1, "stable": 1, "archived": 0},
        "kind_counts": {"source_digest": 1, "risk": 1},
        "surface_counts": {"wiki": 2},
        "latest": {"title": "리스크 판단"},
    })
    monkeypatch.setattr(
        wiki_pipeline_health.wiki,
        "lint_pages",
        lambda pages=None: {
            "ok": False,
            "issue_count": 2,
            "issues": [
                {"code": "source_missing_for_promoted", "page_id": "risk-1", "title": "리스크 판단"},
                {"code": "open_questions_present", "page_id": "digest-1", "title": "SaveTicker digest"},
            ],
        },
    )
    monkeypatch.setattr(
        wiki_pipeline_health.wiki,
        "list_stale_pages",
        lambda max_age_days=14: [{"id": "digest-1", "title": "SaveTicker digest"}],
    )
    monkeypatch.setattr(
        wiki_pipeline_health.wiki,
        "list_unused_pages",
        lambda days=30: [{"id": "digest-1", "title": "SaveTicker digest"}, {"id": "risk-1", "title": "리스크 판단"}],
    )
    monkeypatch.setattr(wiki_pipeline_health.wiki, "list_pages", lambda **kwargs: _sample_pages())

    report = wiki_pipeline_health.build_pipeline_health_report(dry_run=True)

    assert set(report) >= {"generated_at", "dry_run", "source_health", "wiki_health", "curation_health", "recommendations"}
    assert report["source_health"]["overall"]["tracked_sources"] == 2
    assert report["source_health"]["overall"]["stale_sources"] == 1
    assert report["wiki_health"]["source_backed_count"] == 1
    assert report["wiki_health"]["unverified_count"] == 1
    assert report["curation_health"]["source_digest_unlinked_count"] == 1
    assert report["recommendations"][0]["category"] == "collection"


def test_dry_run_pipeline_health_uses_bounded_recent_event_sample(monkeypatch):
    from reports import wiki_pipeline_health

    _patch_empty_pipeline(monkeypatch, wiki_pipeline_health)
    seen = {}

    def load_recent_events(*, hours=24, limit=None, **kwargs):
        seen["hours"] = hours
        seen["limit"] = limit
        return []

    monkeypatch.setattr(wiki_pipeline_health.source_collector, "load_recent_events", load_recent_events)

    wiki_pipeline_health.build_pipeline_health_report(dry_run=True)

    assert seen == {"hours": wiki_pipeline_health.RECENT_EVENT_HOURS, "limit": 120}


def test_pipeline_health_treats_empty_news_labels_as_no_data(monkeypatch):
    from reports import wiki_pipeline_health

    _patch_empty_pipeline(monkeypatch, wiki_pipeline_health)
    monkeypatch.setattr(wiki_pipeline_health.news_labels, "load_labels", lambda: [])

    report = wiki_pipeline_health.build_pipeline_health_report(dry_run=True)

    labels = report["news_label_health"]
    assert labels["total"] == 0
    assert labels["status"] == "no_data"
    assert labels["attention"] is False
    assert report["overall"]["news_labels"]["status"] == "no_data"
    assert not any(rec["category"] == "news_labels" for rec in report["recommendations"])


def test_wiki_health_separates_provenance_pages_from_actionable_unused_pages():
    from reports.wiki_pipeline_health import _summarize_wiki_health

    summary = _summarize_wiki_health(
        [
            {"id": "digest", "kind": "source_digest", "status": "reviewed"},
            {"id": "note", "kind": "note", "status": "draft"},
        ],
        {"status_counts": {}},
        {"issues": []},
        [],
        [
            {"id": "digest", "kind": "source_digest"},
            {"id": "note", "kind": "note"},
        ],
    )

    assert summary["unused_count"] == 2
    assert summary["unused_provenance_count"] == 1
    assert summary["unused_actionable_count"] == 1


def test_recommendations_do_not_flag_provenance_only_unused_pages():
    from reports.wiki_pipeline_health import _recommendations

    recommendations = _recommendations(
        {"stale_sources": [], "sources": []},
        {
            "stale_count": 0,
            "unused_count": 31,
            "unused_actionable_count": 0,
            "source_missing_for_promoted_count": 0,
        },
        {"source_digest_unlinked_count": 0},
    )

    assert not any(rec["category"] == "hygiene" for rec in recommendations)


def test_curation_health_exposes_distillation_queue_states():
    from reports.wiki_pipeline_health import _summarize_curation_health

    result = _summarize_curation_health(
        [
            {"id": "pending", "kind": "source_digest", "status": "draft", "links": [], "backlinks": [], "distillation_state": {"status": ""}},
            {"id": "created", "kind": "source_digest", "status": "draft", "links": [], "backlinks": [], "distillation_state": {"status": "created"}},
            {"id": "skipped", "kind": "source_digest", "status": "draft", "links": [], "backlinks": [], "distillation_state": {"status": "skipped"}},
            {"id": "linked-pending", "kind": "source_digest", "status": "draft", "links": ["judgment"], "backlinks": [], "distillation_state": {"status": ""}},
            {"id": "judgment", "kind": "risk", "status": "draft", "links": [], "backlinks": []},
        ]
    )

    assert result["distillation_pending_count"] == 1
    assert result["distillation_created_count"] == 1
    assert result["distillation_skipped_count"] == 1


def test_pipeline_health_flags_recent_news_labels_without_llm(monkeypatch):
    from reports import wiki_pipeline_health

    _patch_empty_pipeline(monkeypatch, wiki_pipeline_health)
    now = wiki_pipeline_health.datetime.now(wiki_pipeline_health.timezone.utc).isoformat()
    rows = [
        {"label_method": "heuristic", "label_error": "exit 1",
         "label_error_category": "exit", "labeled_at": now}
        for _ in range(4)
    ]
    monkeypatch.setattr(wiki_pipeline_health.news_labels, "load_labels", lambda: rows)

    report = wiki_pipeline_health.build_pipeline_health_report(dry_run=True)

    labels = report["news_label_health"]
    assert labels["total"] == 4
    assert labels["llm_count"] == 0
    assert labels["fallback_ratio"] == 1.0
    assert labels["status"] == "attention"
    assert labels["attention"] is True
    assert report["overall"]["news_labels"]["llm_count"] == 0
    assert report["overall"]["news_labels"]["fallback_ratio"] == 1.0
    assert report["overall"]["status"] == "attention"
    rec = next(rec for rec in report["recommendations"] if rec["category"] == "news_labels")
    assert rec["priority"] == 1
    assert "llm 0건" in rec["detail"]
    assert "fallback 100.0%" in rec["detail"]


def test_source_health_summary_exposes_blocked_and_zero_persistence():
    from reports.wiki_pipeline_health import _summarize_source_health

    section = _summarize_source_health({
        "polymarket": {
            "last_run": "2026-08-21T00:00:00+00:00",
            "last_count": 0,
            "availability": "blocked",
            "availability_reason": "HTTP 451",
            "last_fetched_count": 0,
            "last_persisted_count": 0,
            "last_duration_ms": 31,
        },
        "saveticker": {
            "last_run": "2026-08-21T00:00:00+00:00",
            "last_count": 114,
            "last_success": "2026-08-21T00:00:00+00:00",
            "last_fetched_count": 114,
            "last_persisted_count": 0,
            "zero_persist_streak": 3,
            "last_duration_ms": 1200,
        },
    }, [], [])

    by_source = {row["source"]: row for row in section["sources"]}
    assert by_source["polymarket"]["availability"] == "blocked"
    assert by_source["saveticker"]["zero_persist_streak"] == 3
    assert section["overall"]["blocked_sources"] == 1
    assert section["overall"]["zero_persist_sources"] == 1


def test_source_health_summary_exposes_explicit_integrity_metrics():
    from reports.wiki_pipeline_health import _summarize_source_health

    section = _summarize_source_health({
        "kalshi": {
            "last_run": "2026-08-21T00:00:00+00:00",
            "last_count": 4,
            "last_fetch_success": "2026-08-21T00:00:00+00:00",
            "last_persist_success": "2026-08-21T00:00:00+00:00",
            "last_fetched_count": 4,
            "last_persisted_count": 0,
            "last_deduped_count": 4,
            "last_collision_count": 0,
            "zero_persist_streak": 0,
            "cardinality_drop_streak": 0,
            "persist_success": True,
        },
    }, [], [])

    row = next(row for row in section["sources"] if row["source"] == "kalshi")
    assert row["fetch_success"] is True
    assert row["persist_success"] is True
    assert row["deduped_count"] == 4
    assert row["collision_count"] == 0
    assert row["duplicate_only"] is True
    assert section["overall"]["collision_sources"] == 0
