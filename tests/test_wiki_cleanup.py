from __future__ import annotations

import json
from pathlib import Path

import pytest


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    memory_dir = tmp_path / "memory"
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(memory_dir))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki-artifacts"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    wiki._CACHE.clear()
    return memory_dir


def _save(wiki, page_id: str, **changes) -> dict:
    page = {
        "id": page_id,
        "title": page_id,
        "surface": "market",
        "kind": "note",
        "status": "draft",
        "body": "",
        "summary": "",
        "source_refs": [],
        "evidence_ids": [],
        "links": [],
    }
    page.update(changes)
    return wiki.upsert_page(page)


def test_plan_cleanup_selects_only_truly_empty_drafts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import plan_cleanup

    empty = _save(wiki, "empty")
    with_body = _save(wiki, "body", body="has content")
    with_summary = _save(wiki, "summary", summary="has summary")
    with_use = _save(wiki, "used", useCount=1)
    with_history = _save(wiki, "history", merge_history=[{"event_id": "old"}])
    with_reference = _save(wiki, "target")
    _save(wiki, "incoming", links=[with_reference["id"]])

    actions = plan_cleanup(wiki._all_wiki_pages())

    assert [(action["page_id"], action["reason"]) for action in actions] == [
        (empty["id"], "empty_draft")
    ]
    assert all(page["id"] != empty["id"] for page in (with_body, with_summary, with_use, with_history))


def test_plan_cleanup_archives_only_exact_duplicate_drafts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import plan_cleanup

    first = _save(wiki, "duplicate-a", title="Same", body="Exact", summary="Summary",
                  source_refs=["https://source.test/a"], evidence_ids=["e1"])
    second = _save(wiki, "duplicate-b", title="Same", body="Exact", summary="Summary",
                   source_refs=["https://source.test/a"], evidence_ids=["e1"])
    _save(wiki, "similar-body", title="Same", body="Almost", summary="Summary",
          source_refs=["https://source.test/a"], evidence_ids=["e1"])
    _save(wiki, "similar-entity", title="Same", surface="portfolio", body="Exact",
          summary="Summary", source_refs=["https://source.test/a"], evidence_ids=["e1"])

    actions = plan_cleanup(wiki._all_wiki_pages())

    assert [(action["page_id"], action["reason"]) for action in actions] == [
        (second["id"], "exact_duplicate")
    ]
    assert first["id"] != second["id"]


def test_plan_cleanup_keeps_drafts_with_distinct_counterevidence(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import plan_cleanup

    shared = {"title": "Same", "body": "Same body", "summary": "Same summary"}
    _save(wiki, "counter-a", **shared, conflicting_evidence_ids=["counter-a"])
    _save(wiki, "counter-b", **shared, conflicting_evidence_ids=["counter-b"])

    actions = plan_cleanup(wiki._all_wiki_pages())

    assert actions == []


@pytest.mark.parametrize("protected", [
    {"kind": "source_digest"},
    {"tags": ["raw:artifact"]},
    {"merge_history": [{"event_id": "merged"}]},
    {"status": "reviewed"},
    {"status": "stable"},
    {"useCount": 1},
    {"parent_page_id": "parent"},
    {"reviewed_at": "2026-01-01T00:00:00Z"},
])
def test_plan_cleanup_protects_sensitive_or_non_draft_pages(monkeypatch, tmp_path, protected):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import plan_cleanup

    page = _save(wiki, "protected", **protected)
    if protected.get("status") in {"reviewed", "stable"}:
        page["status"] = protected["status"]
        page["source_refs"] = ["https://source.test/evidence"]
    if "reviewed_at" in protected:
        page["reviewed_at"] = protected["reviewed_at"]

    actions = plan_cleanup([page])

    assert page["id"] not in {action["page_id"] for action in actions}


def test_apply_cleanup_defaults_to_dry_run_and_archives_with_backup(monkeypatch, tmp_path):
    memory_dir = _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import apply_cleanup, plan_cleanup

    page = _save(wiki, "empty")
    actions = plan_cleanup(wiki._all_wiki_pages())

    preview = apply_cleanup(actions)
    assert preview["dry_run"] is True
    assert preview["archived"] == []
    assert wiki.get_page(page["id"])["status"] == "draft"

    result = apply_cleanup(actions, dry_run=False)

    archived = wiki.get_page(page["id"])
    assert result["archived"] == [page["id"]]
    assert archived["status"] == "archived"
    assert archived["title"] == page["title"]
    backups = list((memory_dir / "wiki-cleanup-backups").glob("*.jsonl"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8").splitlines()[0])["id"] == page["id"]


def test_apply_cleanup_rechecks_eligibility_against_current_store(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import apply_cleanup, plan_cleanup

    page = _save(wiki, "empty")
    actions = plan_cleanup(wiki._all_wiki_pages())
    _save(wiki, "new-incoming", links=[page["id"]])

    result = apply_cleanup(actions, dry_run=False)

    assert result["archived"] == []
    assert result["skipped_stale"] == [page["id"]]
    assert wiki.get_page(page["id"])["status"] == "draft"


def test_plan_cleanup_protects_split_parents_and_children(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import plan_cleanup

    parent = _save(wiki, "parent", tags=["split_into:child"])
    child = _save(wiki, "child", parent_page_id=parent["id"], tags=[f"split_from:{parent['id']}"])

    actions = plan_cleanup(wiki._all_wiki_pages())

    assert actions == []
    assert wiki.split_children_for_page(wiki.get_page(parent["id"]))[0]["id"] == child["id"]


def test_apply_cleanup_caps_writes_at_fifty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports.wiki_cleanup import apply_cleanup, plan_cleanup

    pages = [_save(wiki, f"empty-{index:02d}") for index in range(52)]
    actions = plan_cleanup(wiki._all_wiki_pages())

    result = apply_cleanup(actions, dry_run=False)

    assert len(result["archived"]) == 50
    assert result["truncated_actions"] == 2
    assert sum(wiki.get_page(page["id"])["status"] == "draft" for page in pages) == 2
    backup_rows = [json.loads(line) for line in Path(result["backup_path"]).read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("id", "").startswith("empty-") for row in backup_rows) == 52


def test_apply_cleanup_never_overwrites_knowledge_written_during_its_recheck(monkeypatch, tmp_path):
    """B-05: a writer racing the recheck must not lose its reviewed body and claims."""
    import threading

    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_cleanup

    page = _save(wiki, "empty")
    actions = wiki_cleanup.plan_cleanup(wiki._all_wiki_pages())
    planned_pages = []
    real_plan = wiki_cleanup.plan_cleanup
    writer = threading.Thread(target=lambda: wiki.upsert_page({
        "id": page["id"], "title": page["title"], "surface": "market", "kind": "note",
        "status": "reviewed", "source_refs": ["https://example.org/review"],
        "body": "CONCURRENT_PROTECTED_BODY", "source_knowledge": {"claims": [{"claim_id": "new"}]},
    }))

    def recheck_while_another_writer_runs(pages):
        planned_pages.append(True)
        writer.start()
        # An unguarded check/write window lets the writer finish here, before cleanup writes.
        writer.join(timeout=1.0)
        return real_plan(pages)

    monkeypatch.setattr(wiki_cleanup, "plan_cleanup", recheck_while_another_writer_runs)
    wiki_cleanup.apply_cleanup(actions, dry_run=False)
    writer.join(timeout=60)

    assert planned_pages and not writer.is_alive()
    wiki._CACHE.clear()
    stored = wiki.get_page(page["id"])
    assert stored["status"] == "reviewed"
    assert "CONCURRENT_PROTECTED_BODY" in stored["body"]
    assert stored["source_knowledge"] == {"claims": [{"claim_id": "new"}]}
