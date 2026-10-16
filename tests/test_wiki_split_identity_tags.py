import pytest

from agent_console import shared_memory, wiki, wiki_publication


@pytest.fixture
def local_wiki(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def test_long_parent_split_roundtrip_and_publication(local_wiki):
    parent_id = "source-topic-" + "a" * 64
    claim = {"claim_id": "claim-one", "source_revision_id": "revision-one",
             "text": "Attributed source claim.", "scope": "Synthetic evidence only."}
    body = "Detailed attributed paragraph.\n\n" * 500 + "TAIL_IDENTITY_EVIDENCE"
    wiki.upsert_page({"id": parent_id, "title": "Long identity topic", "body": body,
                      "source_refs": ["https://example.org/evidence"],
                      "source_knowledge": {"claims": [claim], "limitations": ["Synthetic only."]}})
    semantic = wiki.semantic_snapshot(parent_id)
    assert semantic["children"]
    assert "TAIL_IDENTITY_EVIDENCE" in semantic["body"]
    assert semantic["claims"] == [claim]
    for child in semantic["children"]:
        assert child["parent_page_id"] == parent_id
        assert f"split_from:{parent_id}" in child["tags"]
        saved = wiki.upsert_page(child)
        assert f"split_from:{parent_id}" in saved["tags"]
        assert f"split_from:{parent_id}" in wiki.get_page(child["id"])["tags"]
    files = wiki_publication.render_candidate(wiki_publication.capture_snapshot())
    result = wiki_publication.validate_candidate(files)
    assert result["ok"], result
    rendered = b"\n".join(files.values()).decode()
    assert "TAIL_IDENTITY_EVIDENCE" in rendered
    assert claim["claim_id"] in rendered
    assert claim["source_revision_id"] in rendered


@pytest.mark.parametrize("prefix", ["split_from:", "split_into:"])
def test_reserved_identity_tags_are_bounded_and_other_tags_stay_limited(prefix):
    valid = prefix + "source-topic-" + "b" * 64
    untrusted = ["x" * 200, prefix + "x" * 81, prefix + "bad/path" + "x" * 70]
    record = wiki._build_wiki_record({"id": "bounded", "title": "Bounded",
                                      "tags": [valid, valid, *untrusted]}, existing={})
    for tags in (record["tags"], wiki._record_to_page(record)["tags"]):
        assert tags.count(valid) == 1
        assert all(len(tag) <= 60 for tag in tags if tag != valid)
        assert len(tags) <= 20


@pytest.mark.parametrize("prefix", ["split_from:", "split_into:"])
def test_shared_memory_preserves_only_bounded_split_identity_tags(prefix):
    valid = prefix + "Ab_9-" * 16
    malformed = [prefix + "x" * 81, prefix + "bad/path" + "x" * 70,
                 prefix + "x" * 70 + "\n", "ordinary:" + "X" * 100]
    tags = [valid, *malformed, *[f"tag-{index}" for index in range(30)]]
    normalized = shared_memory.normalize_record({"id": "bounded-tags", "tags": tags})
    assert normalized["tags"][0] == valid
    assert all(len(tag) <= 60 for tag in normalized["tags"][1:])
    assert normalized["tags"][4] == shared_memory._clean_text(malformed[3], 60).lower()
    assert len(normalized["tags"]) == 20
