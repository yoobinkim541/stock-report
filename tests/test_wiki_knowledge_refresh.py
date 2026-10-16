import json
from datetime import datetime, timezone

from reports import wiki_distillation as wd
from reports.evidence_cards import event_to_evidence_card


def test_changed_evidence_reopens_linked_completed_digest():
    page = {"id": "s", "kind": "source_digest", "evidence_ids": ["old"],
            "links": ["k"], "distillation_state": {"status": "created", "last_result_id": "k"}}
    page["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(page)
    knowledge = {"id": "k", "kind": "concept"}
    assert wd.select_distillation_candidates([page, knowledge]) == []
    page["evidence_ids"] = ["old", "new"]
    assert wd.select_distillation_candidates([page, knowledge]) == [page]


def test_skipped_evidence_retries_only_after_change():
    page = {"evidence_ids": ["e"], "distillation_state": {"status": "skipped"}}
    page["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(page)
    assert not wd._distillation_is_eligible(page)
    page["evidence_ids"].append("new")
    assert wd._distillation_is_eligible(page)


def test_archived_body_reaches_prompt_without_whole_file_read(tmp_path, monkeypatch):
    from reports import source_collector as collector
    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    event = {"id": "event", "source": "official", "title": "Capacity",
             "url": "https://example.org/report", "collected_at": now.isoformat(),
             "body_raw": "a" * 800 + " capacity constraint evidence beyond preview", "tickers": ["ABC"]}
    path = collector._event_file(tmp_path, now)
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    page = {"evidence_ids": [event_to_evidence_card(event, now=now).id]}
    from pathlib import Path
    original_read_text = Path.read_text
    def reject_event_file_read(path, *args, **kwargs):
        if path == path_to_event_file:
            raise AssertionError("whole event file read")
        return original_read_text(path, *args, **kwargs)
    path_to_event_file = path
    monkeypatch.setattr(Path, "read_text", reject_event_file_read)
    wd._attach_article_context([page], cache_dir=tmp_path, now=now)
    page["_previous_knowledge"] = {"kind": "concept", "body": "existing causal explanation"}
    prompt = wd._build_distillation_prompt(page)
    assert "capacity constraint evidence beyond preview" in prompt
    assert "existing causal explanation" in prompt
    assert "https://example.org/report" in prompt


def test_invalid_article_citation_rejected():
    page = {"_source_articles": [{"url": "https://example.org", "text": "evidence"}]}
    plan = {"action": "create", "kind": "concept", "title": "T", "body": "unsupported [S9]"}
    payload, status, reason = wd._distill_one_with_status(page, lambda _: json.dumps(plan))
    assert payload is None and status == "failed"
    assert "citation" in reason


def test_missing_article_does_not_generate_from_links_alone():
    def unexpected_model_call(_prompt):
        raise AssertionError("missing article must not invoke model")
    payload, status, reason = wd._distill_one_with_status(
        {"_require_articles": True, "_source_articles": []}, unexpected_model_call)
    assert payload is None and status == "failed"
    assert "unavailable" in reason
