from __future__ import annotations

import os
import sys

import pytest

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


def test_recommendations_flag_blocked_sources_even_when_not_stale():
    from reports.wiki_pipeline_health import _recommendations

    recommendations = _recommendations(
        {
            "stale_sources": [],
            "sources": [
                {"source": "saveticker", "availability": "blocked", "availability_reason": "HTTP 403"},
            ],
        },
        {"stale_count": 0, "unused_count": 0, "source_missing_for_promoted_count": 0},
        {"source_digest_unlinked_count": 0},
    )

    rec = next(rec for rec in recommendations if rec["category"] == "collection")
    assert "saveticker" in rec["detail"]
    assert "HTTP 403" in rec["detail"]


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

    # Created without new evidence or an archived result is not selector-eligible.
    assert result["distillation_pending_count"] == 1
    assert result["distillation_queue_ids"] == ["pending"]
    assert result["distillation_created_count"] == 1
    assert result["distillation_skipped_count"] == 1


def test_curation_health_separates_intentional_skips_and_orphaned_results():
    from reports.wiki_pipeline_health import _summarize_curation_health

    result = _summarize_curation_health(
        [
            {"id": "skipped", "kind": "source_digest", "status": "draft", "links": [], "backlinks": [],
             "distillation_state": {"status": "skipped", "reason": "단순 사실 나열"}},
            {"id": "orphaned", "kind": "source_digest", "status": "draft", "links": [], "backlinks": [],
             "distillation_state": {"status": "created", "last_result_id": "archived-judgment"}},
            {"id": "failed", "kind": "source_digest", "status": "draft", "links": [], "backlinks": [],
             "distillation_state": {"status": "failed", "attempts": 1}},
            {"id": "archived-judgment", "kind": "risk", "status": "archived", "links": [], "backlinks": []},
        ]
    )

    assert result["source_digest_unlinked_count"] == 3
    assert result["intentional_skip_count"] == 1
    assert result["orphaned_judgment_count"] == 1
    assert result["actionable_unlinked_count"] == 2
    assert result["distillation_pending_count"] == 2


def test_curation_health_counts_evidence_changes_as_refresh_due():
    from reports.wiki_pipeline_health import _summarize_curation_health

    result = _summarize_curation_health([
        {
            "id": "changed",
            "kind": "source_digest",
            "status": "draft",
            "links": [],
            "backlinks": [],
            "evidence_ids": ["new-evidence"],
            "distillation_state": {"status": "skipped", "evidence_fingerprint": "old"},
        },
    ])

    assert result["refresh_due_count"] == 1
    assert result["intentional_skip_count"] == 0
    assert result["actionable_unlinked_count"] == 1


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


def test_gap_count_includes_blocked_sources_that_stale_check_skips():
    """차단(403) 소스는 stale 계산에서 빠지므로 '소스 공백 0'으로 숨겨지면 안 된다."""
    from reports.wiki_pipeline_health import _summarize_source_health

    section = _summarize_source_health({
        "saveticker": {"last_run": "2026-10-23T00:00:00+00:00", "last_count": 0,
                       "availability": "blocked", "availability_reason": "HTTP 403"},
        "economic_calendar": {"last_run": "2026-10-23T00:00:00+00:00", "last_count": 0,
                              "availability": "error", "last_error": "timeout"},
        "fred": {"last_run": "2026-10-23T00:00:00+00:00", "last_count": 0,
                 "availability": "disabled"},
        "kalshi": {"last_run": "2026-10-23T00:00:00+00:00", "last_count": 60,
                   "last_success": "2026-10-23T00:00:00+00:00"},
    }, [], [])

    overall = section["overall"]
    assert overall["stale_sources"] == 0
    assert overall["gap_sources"] == 2
    assert sorted(overall["gap_source_names"]) == ["economic_calendar", "saveticker"]



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


# B-fix-ground-08: new source/topic pipeline state reaches health with concrete causes.
def _write_json(path, value):
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _health_with_reports_root(monkeypatch, root):
    from reports import wiki_pipeline_health

    _patch_empty_pipeline(monkeypatch, wiki_pipeline_health)
    monkeypatch.setattr(wiki_pipeline_health.news_labels, "load_labels", lambda: [])
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(root))
    return wiki_pipeline_health.build_pipeline_health_report(dry_run=True)


def _public(root):
    return root / "public-wiki-pilot-20261013"


def _attempt(**counts):
    result = {"compile_failed": 0, "routing_pending": 0, "effects_pending": 0, "stale_base": 0,
              "reason_counts": {}, **counts}
    return {"revision_id": "a" * 64, "result": result}


def test_source_pipeline_without_journals_is_no_data_not_attention(monkeypatch, tmp_path):
    report = _health_with_reports_root(monkeypatch, tmp_path)
    assert report["source_pipeline_health"]["status"] == "no_data"
    assert report["overall"]["status"] == "ok"
    assert report["overall"]["source_pipeline"] == "no_data"


def test_failed_automatic_run_without_source_digest_is_attention(monkeypatch, tmp_path):
    from reports.wiki_pipeline_health import format_pipeline_health_report

    _write_json(_public(tmp_path) / "automatic-latest.json", {
        "attempted": 5, "results": [_attempt(compile_failed=1, effects_pending=1,
                                             reason_counts={"anchor_missing": 1}) for _ in range(5)]})
    report = _health_with_reports_root(monkeypatch, tmp_path)
    section = report["source_pipeline_health"]
    latest = section["journals"]["public"]["automatic_latest"]
    assert latest["state"] == "ok" and latest["compile_failed"] == 5 and latest["effects_pending"] == 5
    assert report["overall"]["status"] == "attention"
    assert any("compile_failed 5" in reason and "effects_pending 5" in reason and "anchor_missing 5" in reason
               for reason in section["reasons"])
    rec = next(rec for rec in report["recommendations"] if rec["category"] == "source_pipeline")
    assert rec["priority"] == 1 and "compile_failed 5" in rec["detail"]
    assert "healthy" not in {rec["category"] for rec in report["recommendations"]}
    assert "원문 파이프라인: attention" in format_pipeline_health_report(report)


def test_journal_compile_failure_and_recovery_wait_are_reported(monkeypatch, tmp_path):
    root = tmp_path / "wiki-source-v1"
    _write_json(root / "sources" / ("a" * 64 + ".json"), {"compile_state": "failed", "error_code": "claim_number_unsupported"})
    _write_json(root / "sources" / ("b" * 64 + ".json"), {"compile_state": "compiled", "error_code": ""})
    _write_json(root / "operations" / ("c" * 64 + ".json"),
                {"stage": "prepared", "artifacts": "pending", "search": "pending", "log": "pending"})
    _write_json(root / "topic-operations-v3" / ("d" * 64 + ".json"),
                {"stage": "committed", "artifacts": "done", "search": "failed", "log": "done"})
    report = _health_with_reports_root(monkeypatch, tmp_path)
    section = report["source_pipeline_health"]
    journal = section["journals"]["unrestricted"]
    assert journal["compile_failed"] == 1 and journal["error_codes"] == {"claim_number_unsupported": 1}
    assert journal["operation_stages"] == {"prepared": 1, "effects_pending": 1}
    assert "unrestricted: compile 실패 1건 (claim_number_unsupported 1)" in section["reasons"]
    assert any("복구·효과 대기 2건" in reason for reason in section["reasons"])
    assert report["overall"]["status"] == "attention"


def test_missing_and_corrupt_automatic_summary_are_distinguished(monkeypatch, tmp_path):
    (_public(tmp_path) / "sources").mkdir(parents=True)
    missing = _health_with_reports_root(monkeypatch, tmp_path)["source_pipeline_health"]
    assert missing["journals"]["public"]["automatic_latest"] == {"state": "missing"}
    assert missing["status"] == "ok" and not missing["attention"]
    _write_json(_public(tmp_path) / "automatic-latest.json", "{not json")
    _write_json(_public(tmp_path) / "sources" / ("e" * 64 + ".json"), "[]")
    corrupt = _health_with_reports_root(monkeypatch, tmp_path)["source_pipeline_health"]
    assert corrupt["journals"]["public"]["automatic_latest"] == {"state": "corrupt"}
    assert "public: automatic-latest.json 손상" in corrupt["reasons"]
    assert "public: journal 기록 손상 1건" in corrupt["reasons"]
    assert corrupt["status"] == "attention"


def test_stale_unattended_run_is_attention_and_clean_recovery_clears_it(monkeypatch, tmp_path):
    import os
    import time

    latest = _public(tmp_path) / "automatic-latest.json"
    _write_json(latest, {"attempted": 1, "results": [_attempt()], "topic_recovery": [
        {"stage": "applied", "effects": {"artifacts": "pending"}}]})
    old = time.time() - 10 * 3600
    os.utime(latest, (old, old))
    stale = _health_with_reports_root(monkeypatch, tmp_path)["source_pipeline_health"]
    assert stale["journals"]["public"]["automatic_latest"]["stale"] is True
    assert any("자동 실행 정체" in reason for reason in stale["reasons"])
    assert "public: 토픽 복구 대기 1건" in stale["reasons"]
    _write_json(_public(tmp_path) / "sources" / ("f" * 64 + ".json"), {"compile_state": "compiled"})
    _write_json(_public(tmp_path) / "topic-operations-v3" / ("1" * 64 + ".json"),
                {"stage": "committed", "artifacts": "done", "search": "done", "log": "done"})
    _write_json(latest, {"attempted": 1, "results": [_attempt()], "topic_recovery": [
        {"stage": "committed", "effects": {"artifacts": "done", "search": "done", "log": "done"}}]})
    report = _health_with_reports_root(monkeypatch, tmp_path)
    assert report["source_pipeline_health"]["status"] == "ok"
    assert report["source_pipeline_health"]["reasons"] == []
    assert report["overall"]["status"] == "ok"


# B-fix2-06: a damaged automatic summary or receipt field is reported as corruption, never raised.
@pytest.mark.parametrize("attempted", ["damaged", [1], {"n": 1}, 1e999])
def test_damaged_attempted_count_is_corruption_attention_not_a_crash(monkeypatch, tmp_path, attempted):
    import json
    from datetime import datetime, timezone

    from reports.wiki_pipeline_health import _summarize_automatic_latest

    latest = _public(tmp_path) / "automatic-latest.json"
    _write_json(latest, json.dumps({"results": [], "attempted": attempted}))
    assert _summarize_automatic_latest(latest, datetime.now(timezone.utc)) == {"state": "corrupt"}
    report = _health_with_reports_root(monkeypatch, tmp_path)
    section = report["source_pipeline_health"]
    assert "public: automatic-latest.json 손상" in section["reasons"]
    assert section["status"] == "attention" and report["overall"]["status"] == "attention"


def test_unhashable_compile_state_is_counted_as_corrupt_record(monkeypatch, tmp_path):
    _write_json(_public(tmp_path) / "sources" / ("a" * 64 + ".json"), {"compile_state": ["failed"]})
    section = _health_with_reports_root(monkeypatch, tmp_path)["source_pipeline_health"]
    assert "public: journal 기록 손상 1건" in section["reasons"]
    assert section["status"] == "attention"
