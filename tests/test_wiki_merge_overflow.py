def test_oversized_merge_preserves_both_documents(monkeypatch, tmp_path):
    from agent_console import shared_memory, wiki

    root = tmp_path / "memory"
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(root))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    for page_id, character in [("target", "A"), ("source", "B")]:
        wiki.upsert_page({
            "id": page_id, "title": page_id, "body": character * 7000,
            "surface": "market", "kind": "concept", "status": "draft",
            "source_refs": ["source:" + page_id],
            "evidence_ids": [page_id + "-evidence"],
        })
    before = (root / "events.jsonl").read_bytes()
    target_before = wiki.get_page("target")
    source_before = wiki.get_page("source")

    result = wiki._merge_pages(["source"], "target", "Synthesis")

    assert result is None
    assert (root / "events.jsonl").read_bytes() == before
    assert wiki.get_page("target") == target_before
    assert wiki.get_page("source") == source_before
    assert wiki.get_page("source")["status"] == "draft"
    assert not wiki.get_page("source")["merged_into"]
