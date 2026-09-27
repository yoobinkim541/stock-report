import json


def test_evidence_fingerprint_survives_storage_and_page_refresh(monkeypatch, tmp_path):
    from agent_console import shared_memory, wiki

    root = tmp_path / "memory"
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(root))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    fingerprint = "a" * 64
    payload = {
        "id": "fingerprint-roundtrip",
        "title": "Evidence version test",
        "body": "Original evidence",
        "surface": "market",
        "kind": "source_digest",
        "status": "draft",
        "distillation_state": {
            "status": "created", "attempts": 1,
            "last_result_id": "judgment-test",
            "evidence_fingerprint": fingerprint,
        },
    }
    wiki.upsert_page(payload)
    stored = json.loads((root / "events.jsonl").read_text().strip())
    assert stored["distillation_state"]["evidence_fingerprint"] == fingerprint
    assert wiki.get_page(payload["id"])["distillation_state"]["evidence_fingerprint"] == fingerprint

    payload.pop("distillation_state")
    payload["body"] = "New evidence"
    wiki.upsert_page(payload)
    assert wiki.get_page(payload["id"])["distillation_state"]["evidence_fingerprint"] == fingerprint
