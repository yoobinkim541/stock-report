import pytest

from reports import wiki_distillation, wiki_pipeline_health


@pytest.mark.parametrize("supporting", [["support"], []])
def test_unchanged_counterevidence_is_not_reported_as_pending_refresh(supporting):
    page = {"id": "conflict-health", "kind": "source_digest", "status": "draft",
            "evidence_ids": supporting, "conflicting_evidence_ids": ["counter"],
            "links": [], "backlinks": []}
    fingerprint = wiki_distillation._evidence_fingerprint(page)
    page["distillation_state"] = {"status": "skipped", "evidence_fingerprint": fingerprint}

    result = wiki_pipeline_health._summarize_curation_health([page])

    assert result["source_digest_unlinked_pages"][0]["current_evidence_fingerprint"] == fingerprint
    assert result["intentional_skip_count"] == 1
    assert result["actionable_unlinked_count"] == 0
    assert result["refresh_due_count"] == 0


@pytest.mark.parametrize("supporting", [["support"], []])
def test_changed_counterevidence_is_not_counted_as_an_intentional_skip(supporting):
    page = {"id": "changed-conflict-health", "kind": "source_digest", "status": "draft",
            "evidence_ids": supporting, "conflicting_evidence_ids": ["old-counter"],
            "links": [], "backlinks": []}
    page["distillation_state"] = {
        "status": "skipped", "evidence_fingerprint": wiki_distillation._evidence_fingerprint(page),
    }
    page["conflicting_evidence_ids"] = ["new-counter"]

    result = wiki_pipeline_health._summarize_curation_health([page])

    assert result["intentional_skip_count"] == 0
    assert result["actionable_unlinked_count"] == 1
    assert result["refresh_due_count"] == 1
    assert result["distillation_queue_ids"] == [page["id"]]
