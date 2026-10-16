"""Behavioral acceptance of the text-first LLM wiki pattern, not fact certification."""
import hashlib
import json
from pathlib import Path

import pytest

from agent_console import wiki, wiki_contract
from reports import wiki_distillation


@pytest.fixture(autouse=True)
def isolated_wiki(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_DOCS_DIR", str(tmp_path / "markdown"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def test_raw_body_is_compiled_once_into_persistent_cited_knowledge(tmp_path):
    path = tmp_path / "raw.txt"
    path.write_text("Capacity expanded in September. Demand remained uncertain.")
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    document = wiki_contract.read_raw_document(path, roots=[tmp_path])
    page = {"id": "source-test", "title": "Capacity", "surface": "market",
            "kind": "source_digest", "status": "draft", "_require_articles": True,
            "_source_articles": [{"evidence_id": "e1", "url": "https://example.com/source",
                "text": document["text"], "content_kind": "article"}]}

    def llm(prompt):
        assert document["text"] in prompt
        assert "append-only" in prompt
        return json.dumps({"action": "create", "kind": "risk", "title": "Capacity demand mismatch",
            "summary": "Capacity is not realized demand.",
            "body": "September capacity expanded [S1]. Demand remains uncertain; verify orders before attributing revenue growth [S1]."})

    payload, status, _ = wiki_distillation._distill_one_with_status(page, llm)
    assert status == "created"
    saved = wiki.upsert_page(payload)
    wiki._CACHE.clear()
    loaded = wiki.get_page(saved["id"])
    assert "verify orders" in loaded["body"]
    assert "https://example.com/source" in loaded["body"]
    assert loaded["evidence_ids"] == ["e1"]
    assert loaded["status"] == "draft"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
    assert wiki.list_pages(query="Capacity demand mismatch", surface="market")[0]["id"] == saved["id"]


def test_link_only_digest_does_not_become_claimed_fulltext_knowledge():
    page = {"id": "source-links", "body": "https://example.com/source",
            "source_refs": ["https://example.com/source"], "_require_articles": True}
    payload, status, reason = wiki_distillation._distill_one_with_status(
        page, lambda _: pytest.fail("link-only input invoked LLM"))
    assert payload is None
    assert status == "failed"
    assert "unavailable" in reason


def test_unsupported_citations_cannot_be_saved_as_knowledge():
    page = {"id": "source-invalid", "_source_articles": [{"evidence_id": "e1",
        "url": "https://example.com/a", "text": "Original text", "content_kind": "article"}]}
    payload, status, _ = wiki_distillation._distill_one_with_status(page, lambda _: json.dumps(
        {"action": "create", "kind": "concept", "title": "Invalid", "body": "Invented [S9]."}))
    assert payload is None
    assert status == "failed"


def test_supported_answer_can_be_filed_and_retrieved_with_citations():
    saved = wiki.capture_from_chat("How to validate capacity?", "Check realized orders, not capacity alone.",
        surface="market", kind="playbook", source_refs=["https://example.com/source"])
    section = wiki.build_context_section(query="validate capacity", surface="market")
    assert "Check realized orders" in section
    assert "https://example.com/source" in section
    assert wiki.get_page(saved["id"])["status"] == "draft"


def test_markdown_index_schema_and_history_survive_rebuild():
    first = wiki.upsert_page({"id": "topic-one", "title": "First knowledge", "body": "A reusable claim.",
        "kind": "concept", "status": "draft", "surface": "market"})
    wiki.rebuild_artifacts()
    out = wiki.wiki_artifacts_dir()
    history = (out / "log.md").read_text()
    wiki.upsert_page({**first, "body": "A dated clarification, with old claim retained."})
    wiki.rebuild_artifacts()
    assert (out / "log.md").read_text().startswith(history)
    assert "First knowledge" in (out / "index.md").read_text()
    assert "raw" in (out / "schema.md").read_text()


def test_lint_retains_missing_source_and_cross_reference_signals():
    pages = [{"id": "a", "title": "Claim", "kind": "concept", "status": "stable",
        "body": "An unsupported promoted claim", "source_refs": [], "tags": []},
        {"id": "b", "title": "Other", "kind": "concept", "status": "draft",
         "body": "Different perspective", "source_refs": [], "tags": []}]
    codes = {issue["code"] for issue in wiki.lint_pages(pages)["issues"]}
    assert "source_missing_for_promoted" in codes
    assert "orphan_page" in codes
