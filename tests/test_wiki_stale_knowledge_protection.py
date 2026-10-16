from datetime import datetime, timedelta, timezone

import pytest

from agent_console import wiki


@pytest.fixture(autouse=True)
def isolated_wiki(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def _old_page(page_id, **changes):
    return wiki.upsert_page({
        "id": page_id, "title": page_id, "body": "Existing content",
        "summary": "Existing summary", "kind": "note", "status": "archived",
        "surface": "market", "source_refs": [],
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=120)).isoformat(),
        **changes,
    })


@pytest.mark.parametrize("changes", [
    {"kind": "concept"}, {"kind": "risk"}, {"kind": "playbook"},
    {"source_refs": ["https://example.com/original"]}, {"evidence_ids": ["raw-evidence"]},
    {"parent_page_id": "parent"}, {"tags": ["split_from:parent"]},
    {"distillation_state": {"status": "created", "last_result_id": "result"}},
])
def test_automatic_expiry_preserves_accumulated_knowledge_and_provenance(changes):
    saved = _old_page("protected-old", **changes)
    result = wiki.archive_stale_pages(max_age_days=14)
    assert result["deleted"] == 0
    assert wiki.get_page(saved["id"]) is not None


def test_automatic_expiry_preserves_original_with_active_inbound_link():
    original = _old_page("referenced-original")
    wiki.upsert_page({"id": "active-page", "title": "Current knowledge",
                      "body": "Current explanation", "kind": "concept", "status": "draft",
                      "surface": "market", "links": [original["id"]]})
    result = wiki.archive_stale_pages(max_age_days=14)
    assert result["deleted"] == 0
    assert wiki.get_page(original["id"]) is not None


def test_stale_source_backed_draft_is_flagged_but_not_hidden_by_auto_archive():
    saved = _old_page("stale-cited-draft", status="draft",
                      source_refs=["https://example.com/original"])
    assert saved["id"] in {p["id"] for p in wiki.list_stale_pages(max_age_days=14)}
    result = wiki.archive_stale_pages(max_age_days=14)
    assert result["archived"] == 0
    assert wiki.get_page(saved["id"])["status"] == "draft"


def test_unreferenced_expired_note_retains_existing_cleanup_behavior():
    saved = _old_page("unreferenced-note")
    result = wiki.archive_stale_pages(max_age_days=14)
    assert result["deleted"] == 1
    assert wiki.get_page(saved["id"]) is None
