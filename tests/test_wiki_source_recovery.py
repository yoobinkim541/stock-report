import json
import multiprocessing
import threading

import pytest

from agent_console import wiki
from reports.wiki_source_journal import Journal
from test_wiki_source_pipeline import module, NOW


def test_crash_after_data_write_recovers_without_duplicates(pipeline_env, monkeypatch):
    original = wiki.apply_source_batch
    def crash(batch):
        original(batch)
        raise SystemExit("simulated process death")
    monkeypatch.setattr(wiki, "apply_source_batch", crash)
    with pytest.raises(SystemExit):
        module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    monkeypatch.setattr(wiki, "apply_source_batch", original)
    result = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert result["effects_pending"] == 0
    assert pipeline_env["calls"].count("compile") == 1
    assert wiki.semantic_snapshot("entity")["body"].count("Added capacity") == 1
    assert Journal().pending_operations() == []
    log = (wiki.wiki_artifacts_dir() / "log.md").read_text()
    assert sum('"action": "source_ingest"' in line for line in log.splitlines()) == 1


def test_artifact_failure_keeps_data_committed_and_retry_only_repairs_effects(pipeline_env, monkeypatch):
    original = wiki.rebuild_artifacts
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda **kwargs: (_ for _ in ()).throw(OSError("disk")))
    result = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert result["data_committed"] == 1 and result["effects_pending"] == 1
    receipt = Journal().pending_operations()[0]
    assert receipt["stage"] == "committed" and receipt["artifacts"] == "failed"
    before = wiki.semantic_snapshot("entity")["body"]
    monkeypatch.setattr(wiki, "rebuild_artifacts", original)
    module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert wiki.semantic_snapshot("entity")["body"] == before
    assert not Journal().pending_operations()


def _recover(receipt, queue):
    queue.put(module().recover_operation(receipt, journal=Journal())["stage"])


def test_two_processes_recover_once_without_duplicate_ingest_log(pipeline_env, monkeypatch):
    original = wiki.apply_source_batch
    def crash(batch):
        original(batch)
        raise SystemExit()
    monkeypatch.setattr(wiki, "apply_source_batch", crash)
    with pytest.raises(SystemExit):
        module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    monkeypatch.setattr(wiki, "apply_source_batch", original)
    receipt = Journal().pending_operations()[0]
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    workers = [ctx.Process(target=_recover, args=(receipt, queue)) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers:
        worker.join(10)
        if worker.is_alive(): worker.terminate(); worker.join(); pytest.fail("recovery deadlock")
        assert worker.exitcode == 0
    assert [queue.get(timeout=1) for _ in workers] == ["committed", "committed"]
    log = (wiki.wiki_artifacts_dir() / "log.md").read_text()
    assert sum('"action": "source_ingest"' in line for line in log.splitlines()) == 1


def test_operation_log_is_idempotent(pipeline_env):
    operation_id = "f" * 64
    wiki._append_wiki_log("source_ingest", operation_id=operation_id, page_ids=["entity"])
    wiki._append_wiki_log("source_ingest", operation_id=operation_id, page_ids=["entity"])
    log = (wiki.wiki_artifacts_dir() / "log.md").read_text()
    entries = [json.loads(line.removeprefix("- metadata: ")) for line in log.splitlines() if line.startswith("- metadata: ")]
    assert len([entry for entry in entries if entry.get("operation_id") == operation_id]) == 1


def test_old_effect_recovery_does_not_roll_back_routing_cursor(pipeline_env):
    module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    journal = Journal()
    source = journal.load_source(pipeline_env["source"]["revision_id"])
    state = source["routing"]["support"]
    state.update(cursor=9, examined=["entity", "new-page"], selected=["entity", "new-page"])
    journal.save_routing(source["revision_id"], "support", state)
    operation_path = next((journal.root / "operations").glob("*.json"))
    receipt = journal._read(operation_path)
    module().recover_operation(receipt, journal=journal)
    after = journal.load_source(source["revision_id"])["routing"]["support"]
    assert after["cursor"] == 9 and "new-page" in after["selected"]


def test_old_effect_recovery_preserves_failed_final_pass(pipeline_env, monkeypatch):
    wiki.upsert_page({"id": "topic", "title": "Capacity planning", "body": "Prior caveat.",
                      "kind": "concept", "surface": "market"})
    original = wiki.rebuild_artifacts
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda **kwargs: (_ for _ in ()).throw(OSError("disk")))
    first = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], target_limit=1, now=NOW)
    assert first["data_committed"] == 1 and first["effects_pending"] == 1
    def fail_proposal(prompt):
        if prompt.startswith("Propose"):
            raise ValueError("temporary model failure")
        return pipeline_env["llm"](prompt)
    failed = module().run(events=[{}], dry_run=False, llm_fn=fail_proposal, target_limit=1, now=NOW)
    assert failed["routing_pending"] == 1
    monkeypatch.setattr(wiki, "rebuild_artifacts", original)
    retried = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], target_limit=1, now=NOW)
    assert retried["data_committed"] == 1
    assert "Added capacity" in wiki.semantic_snapshot("topic")["body"]
    assert not Journal().pending_operations()


@pytest.mark.parametrize("stage", ["prepared", "applied", "committed", "log"])
def test_receipt_stage_crashes_recover_without_duplicate_work(pipeline_env, monkeypatch, stage):
    original_prepare = Journal.prepare
    original_transition = Journal.transition
    crashed = []
    def prepare(self, batch):
        value = original_prepare(self, batch)
        if stage == "prepared" and not crashed:
            crashed.append(True)
            raise SystemExit()
        return value
    def transition(self, operation_id, **kwargs):
        value = original_transition(self, operation_id, **kwargs)
        point = kwargs.get("stage") or ("log" if kwargs.get("effects", {}).get("log") == "done" else None)
        if point == stage and not crashed:
            crashed.append(True)
            raise SystemExit()
        return value
    monkeypatch.setattr(Journal, "prepare", prepare)
    monkeypatch.setattr(Journal, "transition", transition)
    with pytest.raises(SystemExit):
        module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    result = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert result["effects_pending"] == 0 and not Journal().pending_operations()
    assert pipeline_env["calls"].count("compile") == 1
    assert wiki.semantic_snapshot("entity")["body"].count("Added capacity") == 1
    log = (wiki.wiki_artifacts_dir() / "log.md").read_text()
    assert sum('"action": "source_ingest"' in line for line in log.splitlines()) == 1


def test_overlapping_artifact_publications_keep_the_latest_store_snapshot(pipeline_env, monkeypatch):
    captured_old = threading.Event()
    newer_done = threading.Event()
    original = wiki._render_index_md
    errors = []
    def render(pages):
        if threading.current_thread().name == "older-rebuild":
            captured_old.set()
            newer_done.wait(3)
        return original(pages)
    monkeypatch.setattr(wiki, "_render_index_md", render)
    def rebuild(newer=False):
        try:
            wiki.rebuild_artifacts()
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            if newer:
                newer_done.set()
    old = threading.Thread(target=rebuild, name="older-rebuild")
    old.start()
    assert captured_old.wait(3)
    wiki.upsert_page({"id": "latest", "title": "Newest knowledge", "body": "Newly committed knowledge.",
                      "kind": "concept", "surface": "market"})
    new = threading.Thread(target=rebuild, args=(True,), name="newer-rebuild")
    new.start()
    old.join(8)
    new.join(8)
    assert not old.is_alive() and not new.is_alive() and errors == []
    assert "Newest knowledge" in (wiki.wiki_artifacts_dir() / "index.md").read_text()


def test_overlapping_search_exports_keep_the_latest_store_snapshot(pipeline_env, monkeypatch):
    from agent_console import qmd_search
    monkeypatch.setenv("AGENT_CONSOLE_QMD_DOCS_DIR", str(pipeline_env["root"] / "markdown"))
    captured_old, newer_done = threading.Event(), threading.Event()
    original = qmd_search.sync_pages
    errors = []
    def publish(pages, **kwargs):
        if threading.current_thread().name == "older-search":
            captured_old.set()
            newer_done.wait(3)
        return original(pages, **kwargs)
    monkeypatch.setattr(qmd_search, "sync_pages", publish)
    def sync(newer=False):
        try:
            wiki.sync_qmd()
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            if newer:
                newer_done.set()
    old = threading.Thread(target=sync, name="older-search")
    old.start()
    assert captured_old.wait(3)
    wiki.upsert_page({"id": "latest", "title": "Newest knowledge", "body": "New knowledge.",
                      "kind": "concept", "surface": "market"})
    new = threading.Thread(target=sync, args=(True,), name="newer-search")
    new.start()
    old.join(8)
    new.join(8)
    assert not old.is_alive() and not new.is_alive() and errors == []
    assert any("Newest knowledge" in path.read_text() for path in qmd_search.wiki_docs_dir().glob("*.md"))
