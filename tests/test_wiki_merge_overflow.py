import pytest


@pytest.fixture
def store(monkeypatch, tmp_path):
    from agent_console import shared_memory, wiki

    root = tmp_path / "memory"
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(root))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    return root


def _save_pair(wiki, **target_fields):
    for page_id, character in [("target", "A"), ("source", "B")]:
        wiki.upsert_page({
            "id": page_id, "title": page_id, "body": character * 7000,
            "surface": "market", "kind": "concept", "status": "draft",
            "source_refs": ["source:" + page_id],
            "evidence_ids": [page_id + "-evidence"],
            **(target_fields if page_id == "target" else {}),
        })


def test_oversized_merge_preserves_both_documents_through_lossless_split(store):
    from agent_console import wiki

    _save_pair(wiki)

    assert wiki._merge_pages(["source"], "target", "Synthesis")

    snapshot = wiki.semantic_snapshot("target")
    assert "A" * 7000 in snapshot["body"] and "B" * 7000 in snapshot["body"]
    assert "Synthesis" in snapshot["body"]
    assert len(snapshot["children"]) >= 2
    assert all(len(child["body"]) <= wiki.WIKI_BODY_LIMIT for child in snapshot["children"])
    assert {"target-evidence", "source-evidence"} <= set(snapshot["evidence_ids"])
    source = wiki.get_page("source")
    assert source["status"] == "archived" and source["merged_into"] == "target"
    assert source["body"] == "B" * 7000


def test_oversized_merge_into_split_child_is_refused_without_writing(store):
    from agent_console import wiki

    wiki.upsert_page({"id": "root", "title": "root", "body": "Root", "surface": "market",
                      "kind": "concept", "status": "draft", "source_refs": ["source:root"],
                      "tags": ["split_into:target"]})
    _save_pair(wiki, parent_page_id="root", tags=["split_from:root"])
    before = (store / "events.jsonl").read_bytes()
    target_before = wiki.get_page("target")
    source_before = wiki.get_page("source")

    assert wiki._merge_pages(["source"], "target", "Synthesis") is None

    assert (store / "events.jsonl").read_bytes() == before
    assert wiki.get_page("target") == target_before
    assert wiki.get_page("source") == source_before
    assert not wiki.get_page("source")["merged_into"]
