from __future__ import annotations

from copy import deepcopy
import hashlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from reports import wiki_distillation, wiki_pipeline_health


CURRENT_FINGERPRINT = hashlib.sha256(b'["evidence-1"]').hexdigest()


def _runtime_contract(monkeypatch, pages, candidates, refresh_ids):
    def select(supplied_pages, *, limit):
        assert supplied_pages is pages
        assert limit == len(pages)
        return candidates

    monkeypatch.setattr(wiki_distillation, "select_distillation_candidates", select)
    monkeypatch.setattr(
        wiki_distillation, "_refresh_due",
        lambda page: page["id"] in refresh_ids, raising=False,
    )


def _pages(*, state=None, linked=True, status="draft", judgment_status="draft"):
    return [
        {
            "id": "digest",
            "kind": "source_digest",
            "status": status,
            "source_refs": ["source:news:1"],
            "evidence_ids": ["evidence-1"],
            "links": ["judgment"] if linked else [],
            "backlinks": [],
            "distillation_state": state or {},
        },
        {
            "id": "judgment",
            "kind": "risk",
            "status": judgment_status,
            "links": [],
            "backlinks": [],
        },
    ]


def test_curation_health_counts_linked_created_refresh(monkeypatch):
    pages = _pages(state={"status": "created", "evidence_fingerprint": "old"})
    _runtime_contract(monkeypatch, pages, [pages[0]], {"digest"})

    result = wiki_pipeline_health._summarize_curation_health(pages)

    assert result["source_digest_linked_count"] == 1
    assert result["source_digest_unlinked_count"] == 0
    assert result["distillation_pending_count"] == 1
    assert result["refresh_due_count"] == 1
    assert result["distillation_queue_ids"] == ["digest"]
    assert result["refresh_due_ids"] == ["digest"]


@pytest.mark.parametrize(
    "state,linked,status,judgment_status,pending,refresh",
    [
        ({}, False, "draft", "draft", 1, 0),
        ({}, True, "draft", "draft", 1, 0),
        ({"status": "created", "evidence_fingerprint": "old"}, True, "draft", "draft", 1, 1),
        ({"status": "created", "evidence_fingerprint": CURRENT_FINGERPRINT}, True, "draft", "draft", 0, 0),
        ({"status": "created"}, True, "draft", "draft", 1, 1),
        ({"status": "failed", "attempts": 1, "last_result_id": "judgment", "evidence_fingerprint": CURRENT_FINGERPRINT}, True, "draft", "draft", 1, 0),
        ({"status": "skipped", "evidence_fingerprint": CURRENT_FINGERPRINT}, False, "draft", "draft", 0, 0),
        ({"status": "skipped", "evidence_fingerprint": "old"}, True, "draft", "draft", 1, 1),
        ({"status": "failed", "attempts": 3, "last_result_id": "judgment", "evidence_fingerprint": CURRENT_FINGERPRINT}, True, "draft", "draft", 0, 0),
        ({"status": "failed", "attempts": 3, "evidence_fingerprint": CURRENT_FINGERPRINT}, False, "draft", "draft", 0, 0),
        ({"status": "failed", "attempts": 8, "last_result_id": "judgment", "evidence_fingerprint": "old"}, True, "draft", "draft", 1, 1),
        ({"status": "created", "evidence_fingerprint": "old"}, True, "archived", "draft", 0, 0),
        ({"status": "created", "last_result_id": "judgment", "evidence_fingerprint": CURRENT_FINGERPRINT}, True, "draft", "archived", 1, 0),
        ({"status": "skipped", "last_result_id": "judgment", "evidence_fingerprint": CURRENT_FINGERPRINT}, True, "draft", "archived", 0, 0),
    ],
    ids=[
        "unlinked-pending", "linked-unattempted-evidence", "linked-created-changed",
        "linked-created-unchanged", "legacy-created-no-fingerprint", "linked-failed-refresh-retry",
        "skipped-unchanged", "linked-skipped-changed", "linked-exhausted-unchanged",
        "unlinked-exhausted-unchanged", "linked-exhausted-changed", "archived-digest",
        "archived-result-created", "archived-result-skipped",
    ],
)
def test_queue_health_obeys_runtime_selector_and_refresh_contract(
    monkeypatch, state, linked, status, judgment_status, pending, refresh
):
    pages = _pages(state=state, linked=linked, status=status, judgment_status=judgment_status)
    _runtime_contract(
        monkeypatch, pages, [pages[0]] if pending else [], {"digest"} if refresh else set()
    )

    result = wiki_pipeline_health.distillation_queue_health(pages)

    assert result == {
        "distillation_pending_count": pending,
        "distillation_queue_ids": ["digest"] if pending else [],
        "refresh_due_count": refresh,
        "refresh_due_ids": ["digest"] if refresh else [],
    }


def test_queue_health_counts_all_candidates_in_selector_order(monkeypatch):
    pages = [
        {
            "id": f"digest-{i}", "kind": "source_digest", "status": "draft",
            "evidence_ids": ["evidence-1"], "links": ["judgment"],
            "distillation_state": {
                "status": "created", "evidence_fingerprint": "old",
                "last_attempt_at": f"2026-10-07T00:00:{i:02d}+00:00",
            },
        }
        for i in reversed(range(12))
    ]
    pages.append({"id": "judgment", "kind": "risk", "status": "draft"})
    candidates = list(reversed(pages[:-1]))
    _runtime_contract(monkeypatch, pages, candidates, {page["id"] for page in candidates})

    result = wiki_pipeline_health.distillation_queue_health(pages)

    assert result["distillation_pending_count"] == 12
    assert result["refresh_due_count"] == 12
    assert result["distillation_queue_ids"] == [f"digest-{i}" for i in range(12)]
    assert result["refresh_due_ids"] == result["distillation_queue_ids"]


def test_queue_health_is_read_only_and_uses_only_supplied_pages(monkeypatch):
    pages = _pages(state={"status": "created", "evidence_fingerprint": "old"})
    original = deepcopy(pages)
    _runtime_contract(monkeypatch, pages, [pages[0]], {"digest"})

    def forbidden(*args, **kwargs):
        pytest.fail("queue diagnostics must not read/write pages or run distillation")

    for name in ("list_pages", "get_page", "upsert_page"):
        monkeypatch.setattr(wiki_distillation.wiki, name, forbidden)
    monkeypatch.setattr(wiki_distillation, "run", forbidden)
    monkeypatch.setattr(wiki_distillation, "_distill_one_with_status", forbidden)

    assert wiki_pipeline_health.distillation_queue_health(pages)["distillation_pending_count"] == 1
    assert wiki_pipeline_health._summarize_curation_health(pages)["refresh_due_count"] == 1
    assert pages == original


def test_queue_health_uses_real_legacy_selector_without_refresh_predicate(monkeypatch):
    if hasattr(wiki_distillation, "_refresh_due"):
        pytest.skip("real legacy-selector coverage applies only to the legacy baseline")
    pages = [
        {"id": "pending", "kind": "source_digest", "status": "draft", "evidence_ids": ["e1", "e2"]},
        {"id": "retry", "kind": "source_digest", "status": "draft", "distillation_state": {"status": "failed", "attempts": 1}},
        {"id": "skipped", "kind": "source_digest", "status": "draft", "distillation_state": {"status": "skipped"}},
        {"id": "exhausted", "kind": "source_digest", "status": "draft", "distillation_state": {"status": "failed", "attempts": 3}},
        {"id": "archived", "kind": "source_digest", "status": "archived"},
        {"id": "linked", "kind": "source_digest", "status": "draft", "links": ["judgment"]},
        {"id": "judgment", "kind": "risk", "status": "draft"},
    ]

    result = wiki_pipeline_health.distillation_queue_health(pages)

    assert result == {
        "distillation_pending_count": 2,
        "distillation_queue_ids": ["pending", "retry"],
        "refresh_due_count": 0,
        "refresh_due_ids": [],
    }


def test_queue_health_optional_refresh_predicate_fallback(monkeypatch):
    pages = _pages()
    _runtime_contract(monkeypatch, pages, [pages[0]], set())
    monkeypatch.delattr(wiki_distillation, "_refresh_due")

    result = wiki_pipeline_health.distillation_queue_health(pages)

    assert result["distillation_pending_count"] == 1
    assert result["distillation_queue_ids"] == ["digest"]
    assert result["refresh_due_count"] == 0
    assert result["refresh_due_ids"] == []


def test_queue_health_excludes_refreshes_rejected_by_selector(monkeypatch):
    pages = _pages(state={"status": "created", "evidence_fingerprint": "old"}, status="archived")
    _runtime_contract(monkeypatch, pages, [], {"digest"})

    result = wiki_pipeline_health.distillation_queue_health(pages)

    assert result["distillation_pending_count"] == 0
    assert result["refresh_due_count"] == 0
    assert result["refresh_due_ids"] == []


def test_queue_health_counts_linked_and_unlinked_refreshes(monkeypatch):
    pages = _pages(state={"status": "created", "evidence_fingerprint": "old"})
    unlinked = deepcopy(pages[0])
    unlinked.update(id="unlinked", links=[])
    pages.append(unlinked)
    _runtime_contract(monkeypatch, pages, [pages[0], unlinked], {"digest", "unlinked"})

    result = wiki_pipeline_health._summarize_curation_health(pages)

    assert result["source_digest_unlinked_count"] == 1
    assert result["source_digest_linked_count"] == 1
    assert result["distillation_pending_count"] == 2
    assert result["refresh_due_count"] == 2
    assert result["refresh_due_ids"] == ["digest", "unlinked"]


def test_queue_health_empty_corpus():
    assert wiki_pipeline_health.distillation_queue_health([]) == {
        "distillation_pending_count": 0,
        "distillation_queue_ids": [],
        "refresh_due_count": 0,
        "refresh_due_ids": [],
    }
