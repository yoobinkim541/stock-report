import json
from datetime import datetime, timezone

from reports import hermes_source_webhook, raw_archive, source_collector, wiki_distillation
from reports.evidence_cards import event_to_evidence_card


def test_fred_notes_survive_webhook_classification_and_reach_wiki_as_definitions(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    cache = tmp_path / "cache"
    body = "This series measures money supply. The notes describe its components and methodology."
    payload = {"schema": "stock-report.source-batch.v1", "delivery_id": "fred-notes",
               "collected_at": datetime.now(timezone.utc).isoformat(),
               "records": [{"url": "https://fred.stlouisfed.org/series/M2SL",
                            "title": "M2 series notes", "body": body, "body_coverage": "full",
                            "extraction_scope": "series_notes", "raw_content": body, "raw_format": "text"}]}
    result = hermes_source_webhook.ingest_batch(payload, cache_dir=cache, state_dir=tmp_path / "state")
    assert result["persisted"] == 1
    event = source_collector.load_recent_events(cache_dir=cache)[0]
    assert event["classification"]["kind"] == "series_notes"
    assert event["wiki_eligible"] is True
    assert raw_archive.read_raw_text(event["text_path"]) == body
    page = {"id": "source-notes", "_require_articles": True,
            "evidence_ids": [event_to_evidence_card(event).id], "source_refs": [event["text_path"]]}
    wiki_distillation._attach_article_context([page], cache_dir=cache)
    assert len(page["_source_articles"]) == 1
    assert page["_source_articles"][0]["source_text_scope"] == "series_notes"
    plan = {"action": "create", "kind": "concept", "title": "Definition of M2",
            "body": "The series notes define the measure [S1]."}
    saved, outcome, reason = wiki_distillation._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert outcome == "created", reason
    assert saved["evidence_ids"] == page["evidence_ids"]
    assert "https://fred.stlouisfed.org/series/M2SL" in saved["body"]


def test_numeric_fred_event_remains_a_structured_snapshot():
    event = source_collector._classify_event({"source": "fred", "title": "M2 value",
                                             "url": "https://fred.stlouisfed.org/series/M2SL",
                                             "body": "Latest value: 3.2", "fulltext_status": "full",
                                             "body_coverage": "full"})
    assert event["classification"]["kind"] == "macro_snapshot"
    assert event["wiki_eligible"] is False


def test_structured_snapshot_does_not_poison_complete_article_context():
    page = {"id": "mixed-context", "_require_articles": True, "_source_articles": []}
    snapshot = {"evidence_id": "snapshot", "url": "https://fred.stlouisfed.org/series/M2SL",
                "text": "Latest value: 3.2", "text_complete": False, "provenance": "fred",
                "content_kind": "macro_snapshot", "source_text_scope": "structured_snapshot"}
    article = {"evidence_id": "article", "url": "https://example.com/article",
               "text": "The full article describes the policy rationale.", "text_complete": True,
               "provenance": "official", "content_kind": "analysis", "source_text_scope": "article_fulltext"}
    wiki_distillation._append_source_article(page, snapshot)
    wiki_distillation._append_source_article(page, article)
    assert not page.get("_source_text_error")
    assert page["_source_articles"] == [article]
    assert page["_excluded_source_types"] == ["macro_snapshot"]
    plan = {"action": "create", "kind": "concept", "title": "Article rationale",
            "body": "The article describes the rationale [S1]."}
    result, outcome, reason = wiki_distillation._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert outcome == "created", reason
    assert result["evidence_ids"] == ["article"]


def test_excluded_snapshot_archive_cannot_reenter_article_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    cache = tmp_path / "cache"
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    artifact = raw_archive.save_raw_artifact("fred", "json", now, "Numeric snapshot",
                                             "https://fred.stlouisfed.org/series/M2SL", '{"value": 3.2}', ".json")
    raw_archive.save_extracted_text(artifact, "Latest value: 3.2")
    snapshot = {"source": "fred", "title": "Numeric snapshot", "url": artifact["url"],
                "type": "macro_snapshot", "body": "Latest value: 3.2", "text_path": artifact["text_path"]}
    article = {"source": "official", "title": "A full policy article", "url": "https://example.com/policy",
               "type": "article", "body": "The complete policy rationale.", "fulltext_status": "full"}
    source_collector.append_events([snapshot, article], cache_dir=cache, now=now)
    events = source_collector.load_recent_events(cache_dir=cache, now=now)
    page = {"evidence_ids": [event_to_evidence_card(e, now=now).id for e in events],
            "source_refs": [artifact["text_path"]]}
    wiki_distillation._attach_article_context([page], cache_dir=cache, now=now)
    assert not page.get("_source_text_error")
    assert len(page["_source_articles"]) == 1
    assert page["_source_articles"][0]["url"] == "https://example.com/policy"
