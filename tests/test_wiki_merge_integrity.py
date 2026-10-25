"""Merges keep trust, structured knowledge, provenance and split documents intact (B-02~B-04)."""
from pathlib import Path

import pytest

from agent_console import shared_memory, wiki, wiki_publication


@pytest.fixture(autouse=True)
def local_wiki(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def events() -> bytes:
    return (Path(shared_memory.shared_memory_dir()) / "events.jsonl").read_bytes()


def save(identity, **fields):
    return wiki.upsert_page({"id": identity, "title": identity, "body": "Body of " + identity,
                             "surface": "market", "kind": "concept", "status": "draft",
                             "source_refs": ["https://example.org/" + identity], **fields})


def claim(claim_id, **fields):
    return {"claim_id": claim_id, "source_revision_id": "rev-" + claim_id, "text": "Claim " + claim_id,
            "as_of": "2026-10-01", "scope": "synthetic", "anchor": None, "competing_claim_ids": [],
            "knowledge_state": "draft", **fields}


def assert_publishable():
    files = wiki_publication.render_candidate(wiki_publication.capture_snapshot())
    result = wiki_publication.validate_candidate(files)
    assert result["ok"], result["errors"]


def assert_no_active_child_of_archived_parent():
    wiki._CACHE.clear()
    pages = {page["id"]: page for page in wiki._all_wiki_pages()}
    for page in pages.values():
        if page["status"] != "archived" and page["parent_page_id"]:
            assert pages[page["parent_page_id"]]["status"] != "archived", page["id"]


# B-02 ------------------------------------------------------------------------------------------

def test_merge_refuses_unverified_text_into_source_backed_page():
    save("backed", status="stable", body="Official forecast")
    save("unverified", source_refs=[], body="UNVERIFIED_GUARANTEE")
    before = events()

    assert wiki._merge_pages(["unverified"], "backed", "") is None
    assert wiki._merge_pages(["backed"], "unverified", "") is None

    assert events() == before
    assert wiki.get_page("unverified")["status"] == "draft"


def test_merge_status_is_least_reviewed_input_and_synthesis_resets_review():
    save("stable-a", status="stable")
    save("draft-b")
    assert wiki._merge_pages(["draft-b"], "stable-a", "")
    assert wiki.get_page("stable-a")["status"] == "draft"
    assert "stable" not in wiki.get_page("stable-a")["tags"]

    save("stable-c", status="stable")
    save("stable-d", status="stable")
    assert wiki._merge_pages(["stable-d"], "stable-c", "")
    assert wiki.get_page("stable-c")["status"] == "stable"

    save("stable-e", status="stable")
    save("stable-f", status="stable")
    assert wiki._merge_pages(["stable-f"], "stable-e", "Unreviewed LLM synthesis")
    assert wiki.get_page("stable-e")["status"] == "draft"


def _mixed_trust_pair(kind):
    shared = "금리 상승 할인율 성장주 밸류에이션 위험 판단 기준"
    save("backed", kind=kind, status="stable", title="금리 성장주 위험 판단", summary="금리 성장주 위험 판단",
         body=shared + " Official forecast")
    save("unverified", kind=kind, source_refs=[], title="금리 성장주 위험 판단 메모",
         summary="금리 성장주 위험 판단", body=shared + " UNVERIFIED_GUARANTEE")


def _assert_mixed_pair_untouched():
    wiki._CACHE.clear()
    backed, unverified = wiki.get_page("backed"), wiki.get_page("unverified")
    assert "UNVERIFIED_GUARANTEE" not in backed["body"]
    assert backed["status"] == "stable" and backed["verification_status"] == "source-backed"
    assert "Official forecast" not in unverified["body"]
    assert unverified["status"] == "draft"


def test_curator_merge_decision_cannot_store_mixed_trust_pages():
    from reports import wiki_autonomous_curator as curator

    _mixed_trust_pair("concept")

    def approve(prompt):
        return ('{"action":"merge","target_page_id":"backed","source_page_ids":["unverified"],'
                '"body":"","reason":"same subject"}')

    result = curator.run(dry_run=False, llm_fn=approve, limit=1)

    assert result["merged"] == []
    _assert_mixed_pair_untouched()


def test_dedup_merge_judgment_cannot_store_mixed_trust_pages():
    from reports import wiki_dedup_batch

    _mixed_trust_pair("playbook")

    result = wiki_dedup_batch.run(dry_run=False, llm_fn=lambda prompt: '{"merge": true, "reason": "same"}')

    assert result["merged"] == []
    _assert_mixed_pair_untouched()


# B-03 ------------------------------------------------------------------------------------------

def test_merge_unions_structured_knowledge_and_keeps_overflow_provenance():
    target_refs = [f"https://example.org/target/{index}" for index in range(15)]
    source_refs = [f"https://example.org/source/{index}" for index in range(15)]
    save("target", source_refs=target_refs, evidence_ids=["e-target"], source_knowledge={
        "claims": [claim("t1"), claim("shared", competing_claim_ids=["t1"])],
        "limitations": ["TARGET_LIMITATION"],
        "relations": [{"type": "support", "from_claim_id": "t1", "to_claim_id": "shared"}],
        "integrations": [{"revision_id": "rev-t1", "role": "support", "evidence_ids": ["e-target"]}],
    })
    save("source", source_refs=source_refs, evidence_ids=["e-source"], source_knowledge={
        "claims": [claim("s1"), claim("shared", competing_claim_ids=["s1"])],
        "limitations": ["SOURCE_LIMITATION"],
        "relations": [{"type": "compete", "from_claim_id": "s1", "to_claim_id": "shared"}],
        "integrations": [{"revision_id": "rev-s1", "role": "conflict", "evidence_ids": ["e-source"]}],
    })

    assert wiki._merge_pages(["source"], "target", "")

    snapshot = wiki.semantic_snapshot("target")
    claims = {item["claim_id"]: item for item in snapshot["claims"]}
    assert set(claims) == {"t1", "s1", "shared"}
    assert claims["shared"]["competing_claim_ids"] == ["t1", "s1"]
    knowledge = snapshot["source_knowledge"]
    assert knowledge["limitations"] == ["TARGET_LIMITATION", "SOURCE_LIMITATION"]
    assert [relation["type"] for relation in knowledge["relations"]] == ["support", "compete"]
    assert {item["revision_id"] for item in knowledge["integrations"]} == {"rev-t1", "rev-s1"}
    assert set(target_refs + source_refs) <= set(snapshot["source_refs"])
    assert {"e-target", "e-source"} <= set(snapshot["evidence_ids"])
    assert any(child["raw"].get("provenance_only") for child in snapshot["children"])
    assert wiki.get_page("source")["status"] == "archived"
    assert_publishable()


def test_merge_refuses_conflicting_claim_identity_before_archiving():
    save("target", source_knowledge={"claims": [claim("same", text="Capacity is a forecast")]})
    save("source", source_knowledge={"claims": [claim("same", text="Revenue is guaranteed")]})
    before = events()

    assert wiki._merge_pages(["source"], "target", "") is None

    assert events() == before
    assert wiki.get_page("source")["status"] == "draft"


# B-04 ------------------------------------------------------------------------------------------

def _long_body(label):
    paragraphs = [f"{label} paragraph {index} " + "evidence " * 25 for index in range(10)]
    return "\n\n".join([*paragraphs, f"{label}_TAIL_EXCEPTION"])


def test_merge_reads_whole_split_documents_and_retires_their_children():
    save("target", body=_long_body("TARGET"))
    save("source", body=_long_body("SOURCE"))
    assert wiki.split_for_readability("target", max_chars=1000)
    assert wiki.split_for_readability("source", max_chars=1000)
    source_children = [child["id"] for child in wiki.semantic_snapshot("source")["children"]]
    assert len(source_children) > 1
    assert_publishable()

    assert wiki._merge_pages(["source"], "target", "")

    snapshot = wiki.semantic_snapshot("target")
    assert "TARGET_TAIL_EXCEPTION" in snapshot["body"]
    assert "SOURCE_TAIL_EXCEPTION" in snapshot["body"]
    for child_id in source_children:
        child = wiki.get_page(child_id)
        assert child["status"] == "archived" and child["merged_into"] == "target"
    assert_no_active_child_of_archived_parent()
    assert_publishable()


def test_merge_store_conflict_writes_nothing(monkeypatch):
    save("target")
    save("source")
    real = shared_memory.guarded_batch_upsert

    def concurrent_edit_then_write(**kwargs):
        save("source", body="CONCURRENT_EDIT")
        return real(**kwargs)

    monkeypatch.setattr(shared_memory, "guarded_batch_upsert", concurrent_edit_then_write)

    assert wiki._merge_pages(["source"], "target", "") is None

    wiki._CACHE.clear()
    assert wiki.get_page("source")["status"] == "draft"
    assert wiki.get_page("source")["body"] == "CONCURRENT_EDIT"
    assert "CONCURRENT_EDIT" not in wiki.get_page("target")["body"]
