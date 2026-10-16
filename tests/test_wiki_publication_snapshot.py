import copy
import json
from contextlib import contextmanager

import pytest

from agent_console import shared_memory, wiki


def row(identity="parent", **fields):
    return {"id": identity, "title": identity, "body": "captured body",
            "tags": ["wiki"], "source": {"surface": "wiki"}, **fields}


@pytest.fixture
def capture(monkeypatch):
    from agent_console import wiki_publication as publication
    rows = [row()]
    held = []

    @contextmanager
    def lock():
        held.append(True)
        yield
        held.pop()

    def checked(path):
        assert held
        return rows

    monkeypatch.setattr(shared_memory, "_events_lock", lock)
    monkeypatch.setattr(shared_memory, "_read_jsonl_checked", checked)
    monkeypatch.setattr(wiki, "_now", lambda: "2026-10-16T12:00:00+00:00")
    return publication, rows


def test_writer_after_capture_cannot_change_rendered_index_or_note(capture, monkeypatch):
    publication, rows = capture
    snapshot = publication.capture_snapshot()
    rows[0]["body"] = "writer replacement"
    def forbidden(*args, **kwargs):
        raise AssertionError("live access or side effect")
    for name in ("_all_wiki_pages", "_wiki_records", "_append_wiki_log", "_now"):
        monkeypatch.setattr(wiki, name, forbidden)
    monkeypatch.setattr(shared_memory, "all_records", forbidden)
    before = copy.deepcopy(snapshot)
    files = publication.render_candidate(snapshot)
    assert b"captured body" in files["notes/parent.md"]
    assert b"captured body" in files["index.md"]
    assert all(b"writer replacement" not in content for content in files.values())
    assert publication.validate_candidate(files) == {"ok": True, "errors": []}
    assert publication.render_candidate(snapshot) == files
    assert snapshot == before


@pytest.mark.parametrize("field,value", [
    ("body", "changed"), ("useCount", 9), ("source", {"extra": "metadata"}),
])
def test_hash_covers_full_rows(capture, field, value):
    publication, rows = capture
    first = publication.capture_snapshot()
    rows[0][field] = value
    assert first["snapshot_sha256"] != publication.capture_snapshot()["snapshot_sha256"]


def test_hash_covers_schema_and_capture_timestamp(capture, monkeypatch):
    publication, _ = capture
    first = publication.capture_snapshot()
    monkeypatch.setattr(wiki.wiki_contract, "maintenance_schema", lambda: "new schema")
    second = publication.capture_snapshot()
    assert first["schema_sha256"] != second["schema_sha256"]
    assert first["snapshot_sha256"] != second["snapshot_sha256"]
    monkeypatch.setattr(wiki, "_now", lambda: "2026-10-17T12:00:00+00:00")
    assert second["snapshot_sha256"] != publication.capture_snapshot()["snapshot_sha256"]


def test_archived_children_and_full_metadata_are_preserved(capture):
    publication, rows = capture
    tail = "paragraph\n\n" * 2000 + "EXACT TAIL"
    refs = [f"raw://original/{i}" for i in range(120)]
    rows[:] = [row(tags=["wiki", "split_into:child"], links=["child"]),
               row("child", tags=["wiki", "archived", "split_from:parent"],
                   parent_page_id="parent", body=tail, artifacts=refs,
                   source_knowledge={"unrecognized_metadata": {"full": refs}})]
    snapshot = publication.capture_snapshot()
    files = publication.render_candidate(snapshot)
    assert tail.encode() in files["notes/child.md"]
    assert all(ref.encode() in files["notes/child.md"] for ref in refs)
    assert json.loads(files["snapshot.json"])["records"] == rows
    assert publication.validate_candidate(files)["ok"]


@pytest.mark.parametrize("fields,target", [
    ({"links": ["missing-target"]}, "missing-target"),
    ({"tags": ["wiki", "split_into:missing-child"]}, "missing-child"),
    ({"parent_page_id": "missing-parent"}, "missing-parent"),
    ({"body": "[[missing-body|label]]"}, "missing-body"),
])
def test_missing_targets_block_without_dropping_records(capture, fields, target):
    publication, rows = capture
    rows[:] = [row(**fields)]
    files = publication.render_candidate(publication.capture_snapshot())
    result = publication.validate_candidate(files)
    assert not result["ok"]
    assert any(e.get("page_id") == "parent" and e.get("target_id") == target
               for e in result["errors"])
    assert json.loads(files["snapshot.json"])["records"] == rows
    assert "notes/parent.md" in files


@pytest.mark.parametrize("identities", [("a/b", "a?b"), ("same", "same")])
def test_collisions_never_overwrite(capture, identities):
    publication, rows = capture
    rows[:] = [row(identity) for identity in identities]
    with pytest.raises(ValueError, match="collision|duplicate"):
        publication.render_candidate(publication.capture_snapshot())


def test_unicode_identity_and_tampering(capture):
    publication, rows = capture
    rows[:] = [row("\uD55C\uAE00-page")]
    files = publication.render_candidate(publication.capture_snapshot())
    assert "notes/\uD55C\uAE00-page.md" in files
    assert publication.validate_candidate(files)["ok"]
    files["notes/\uD55C\uAE00-page.md"] += b"tampered"
    assert not publication.validate_candidate(files)["ok"]


def test_checked_capture_failure_propagates(capture, monkeypatch):
    publication, _ = capture
    def corrupt(path):
        raise ValueError("store_corrupt")
    monkeypatch.setattr(shared_memory, "_read_jsonl_checked", corrupt)
    with pytest.raises(ValueError, match="store_corrupt"):
        publication.capture_snapshot()


def test_pure_lint_has_explicit_clock_and_legacy_lint_still_logs(monkeypatch):
    logs = []
    monkeypatch.setattr(wiki, "_append_wiki_log", lambda *a, **k: logs.append(a))
    pages = [wiki._record_to_page(row())]
    wiki.collect_lint(pages, captured_at="2026-10-16T12:00:00+00:00")
    assert not logs
    wiki.lint_pages(pages)
    assert logs == [("lint",)]


@pytest.mark.parametrize("child_tags", [["wiki", "split_from:parent"], ["wiki"]])
def test_child_body_is_byte_exact_not_redacted_or_stripped(capture, child_tags):
    publication, rows = capture
    body = "  leading\n\npassword=original-reference\n\ntrailing  \n"
    rows[:] = [row(tags=["wiki", "split_into:child"]),
               row("child", tags=child_tags,
                   parent_page_id="parent", body=body)]
    files = publication.render_candidate(publication.capture_snapshot())
    assert body.encode() in files["notes/child.md"]


def test_split_relationship_mismatch_blocks(capture):
    publication, rows = capture
    rows[:] = [row(tags=["wiki", "split_into:child"]), row("other"),
               row("child", parent_page_id="other", tags=["wiki", "split_from:other"])]
    result = publication.validate_candidate(publication.render_candidate(publication.capture_snapshot()))
    assert not result["ok"]
    assert {"code": "child_parent_mismatch", "page_id": "parent", "target_id": "child"} in result["errors"]


def test_raw_links_beyond_view_limit_are_checked(capture):
    publication, rows = capture
    rows[:] = [row(links=[f"target-{i}" for i in range(13)])]
    rows.extend(row(f"target-{i}") for i in range(12))
    result = publication.validate_candidate(publication.render_candidate(publication.capture_snapshot()))
    assert {"code": "missing_link", "page_id": "parent", "target_id": "target-12"} in result["errors"]


@pytest.mark.parametrize("mutation", ["extra", "missing", "unsafe", "snapshot", "schema"])
def test_candidate_integrity_rejects_mutation(capture, mutation):
    publication, _ = capture
    files = publication.render_candidate(publication.capture_snapshot())
    if mutation == "extra":
        files["extra.md"] = b"extra"
    elif mutation == "missing":
        del files["notes/parent.md"]
    elif mutation == "unsafe":
        files["../escape.md"] = b"escape"
    else:
        snapshot = json.loads(files["snapshot.json"])
        if mutation == "snapshot":
            snapshot["records"][0]["body"] = "forged"
        else:
            snapshot["schema_text"] = "forged"
        files["snapshot.json"] = json.dumps(snapshot).encode()
    assert not publication.validate_candidate(files)["ok"]


@pytest.mark.parametrize("field", ["summary", "openQuestions"])
def test_links_in_other_rendered_sections_are_checked(capture, field):
    publication, rows = capture
    rows[0][field] = ["[[absent|label]]"] if field == "openQuestions" else "[[absent|label]]"
    result = publication.validate_candidate(publication.render_candidate(publication.capture_snapshot()))
    assert not result["ok"]
    assert any(error.get("target_id") == "absent" for error in result["errors"])


def test_conflicting_child_parent_metadata_blocks_without_parent_split_tag(capture):
    publication, rows = capture
    rows[:] = [row(), row("other"), row("child", parent_page_id="parent",
                                       tags=["wiki", "split_from:other"])]
    result = publication.validate_candidate(publication.render_candidate(publication.capture_snapshot()))
    assert not result["ok"]
    assert any(error.get("page_id") == "child" and error["code"] == "conflicting_parents"
               for error in result["errors"])


def test_relative_markdown_links_must_resolve(capture):
    publication, rows = capture
    rows[0]["body"] = "[child](child.md) [external](https://example.org/article.md)"
    result = publication.validate_candidate(publication.render_candidate(publication.capture_snapshot()))
    assert not result["ok"]
    assert any(error.get("page_id") == "parent" and error.get("target_id") == "child.md"
               for error in result["errors"])


def test_real_checked_capture_rejects_duplicate_store_rows(monkeypatch, tmp_path):
    from agent_console import wiki_publication as publication
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path))
    path = shared_memory._paths()["events"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row()) + "\n" + json.dumps(row()) + "\n")
    with pytest.raises(ValueError, match="store_corrupt"):
        publication.capture_snapshot()


@pytest.mark.parametrize("value", [None, [], 7, {"records": [], "schema_text": 7}])
def test_malformed_snapshot_is_validation_error(capture, value):
    publication, _ = capture
    assert not publication.validate_candidate({"snapshot.json": json.dumps(value).encode()})["ok"]
