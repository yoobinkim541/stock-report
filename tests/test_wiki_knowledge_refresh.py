import json

from reports import wiki_distillation as wd


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


def test_archived_judgment_link_reselects_completed_digest():
    page = {
        "id": "source-archived",
        "kind": "source_digest",
        "evidence_ids": ["e1"],
        "links": ["old-judgment"],
        "distillation_state": {"status": "created", "last_result_id": "old-judgment"},
    }
    page["distillation_state"]["evidence_fingerprint"] = wd._evidence_fingerprint(page)
    archived = {"id": "old-judgment", "kind": "concept", "status": "archived"}

    assert wd.select_distillation_candidates([page, archived]) == [page]


def test_article_context_loads_only_selected_ready_body(monkeypatch):
    selected_url = "https://example.org/selected"
    other_url = "https://example.org/other"
    index = {
        selected_url: {
            "status": "ready", "content_hash": "a" * 64, "evidence_ids": ["selected"],
            "title": "Selected", "source": "official", "published_at": "2026-09-09",
        },
        other_url: {
            "status": "ready", "content_hash": "b" * 64, "evidence_ids": ["other"],
            "title": "Other", "source": "official", "published_at": "2026-09-09",
        },
    }
    page = {"evidence_ids": ["selected"], "source_refs": [selected_url], "tags": ["source:official"]}
    wd._attach_article_metadata([page], index)
    calls = []

    def get_article(url, **kwargs):
        calls.append(url)
        return {
            "url": url, "title": "Selected", "source": "official",
            "published_at": "2026-09-09", "text": "selected body", "content_hash": "a" * 64,
        }

    monkeypatch.setattr(wd.article_queue, "get_article", get_article)
    wd._attach_article_context([page], index=index)

    assert calls == [selected_url]
    assert page["_source_articles"][0]["text"] == "selected body"


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
