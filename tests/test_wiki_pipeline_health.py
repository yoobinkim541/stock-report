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


def test_pipeline_health_report_merges_source_wiki_and_curation(monkeypatch):
    from reports import wiki_pipeline_health

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
