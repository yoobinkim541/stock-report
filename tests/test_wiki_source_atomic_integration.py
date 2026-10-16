import copy
import json
import multiprocessing

import pytest

from agent_console import shared_memory, wiki


REV = "a" * 64


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(root))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    for page_id in ("entity", "topic", "unrelated"):
        wiki.upsert_page({"id": page_id, "title": page_id, "body": "Old exception.\nOpposing evidence.",
                          "kind": "concept", "surface": "market", "status": "reviewed",
                          "source_refs": ["https://example.org/old"]})
    return root


def inputs(ids=("entity", "topic")):
    claim = {"claim_id": "new-claim", "source_revision_id": REV, "as_of": "2026-10-09",
             "scope": "capacity", "text": "Capacity increased; demand is unproven", "knowledge_state": "draft"}
    compiled = {"revision_id": REV, "body": "Capacity not demand", "claims": [claim],
                "limitations": ["Not realized orders"], "terms": ["capacity"], "skip_reason": None}
    source = {"revision_id": REV, "role": "support", "canonical_url": "https://example.org/new",
              "raw_ref": "raw-sha256:" + "b" * 64, "text_ref": "https://example.org/new",
              "observed_evidence_ids": ["actual-id"]}
    targets = [{"page_id": page_id, "before_hash": wiki.semantic_snapshot(page_id)["revision_hash"],
                "additions": [{"claim_ids": ["new-claim"], "as_of": "2026-10-09", "scope": "capacity",
                               "body": "Added capacity, not demand [S1](https://example.org/new)."}],
                "relations": []} for page_id in ids]
    return {"revision_id": REV, "role": "support", "targets": targets}, compiled, source


def test_atomic_additions_preserve_old_prose_trust_history_and_unrelated(store):
    before = wiki.semantic_snapshot("entity")
    unrelated = wiki.get_page("unrelated")["raw"]
    batch = wiki.prepare_source_batch(*inputs())
    assert wiki.apply_source_batch(batch)["status"] == "applied"
    after = wiki.semantic_snapshot("entity")
    assert after["body"].startswith(before["body"])
    assert "2026-10-09" in after["body"] and "not demand" in after["body"]
    assert after["page"]["status"] == "reviewed"
    assert after["claims"][0]["knowledge_state"] == "draft"
    assert wiki.get_page("unrelated")["raw"] == unrelated
    assert list(wiki.wiki_artifacts_dir().glob("revisions/**/*.json"))
    rows = shared_memory.inspect_records()
    assert all(wiki._source_fingerprint(page_id, rows) == expected
               for page_id, expected in batch["expected_after"].items())


def test_conflict_without_claim_mapping_is_explicitly_unresolved(store):
    proposal, compiled, source = inputs(('entity',))
    proposal['role'] = source['role'] = 'conflict'
    batch = wiki.prepare_source_batch(proposal, compiled, source)
    assert wiki.apply_source_batch(batch)['status'] == 'applied'
    snapshot = wiki.semantic_snapshot('entity')
    integration = snapshot['source_knowledge']['integrations'][0]
    assert integration['relation_state'] == 'unresolved'
    assert 'not adjudicated' in integration['relation_note']
    assert snapshot['claims'][0]['knowledge_state'] == 'draft'
    assert 'Old exception.' in snapshot['body']
    assert snapshot['conflicting_evidence_ids'] == ['actual-id']


def test_dangling_historical_relation_does_not_mark_conflict_mapped(store):
    wiki.upsert_page({'id': 'entity', 'title': 'entity', 'kind': 'concept', 'surface': 'market',
                     'source_knowledge': {'relations': [{'from_revision_id': REV}]}})
    proposal, compiled, source = inputs(('entity',))
    proposal['role'] = source['role'] = 'conflict'
    wiki.apply_source_batch(wiki.prepare_source_batch(proposal, compiled, source))
    assert wiki.semantic_snapshot('entity')['source_knowledge']['integrations'][0]['relation_state'] == 'unresolved'


def test_conflict_can_be_mapped_and_mapping_survives_empty_relation_retry(store):
    old = {**inputs(('entity',))[1]['claims'][0], 'claim_id': 'old-claim', 'source_revision_id': 'b' * 64,
           'as_of': '2026-10-08'}
    wiki.upsert_page({'id': 'entity', 'title': 'entity', 'kind': 'concept', 'surface': 'market',
                     'source_knowledge': {'claims': [old]}})
    proposal, compiled, source = inputs(('entity',))
    proposal['role'] = source['role'] = 'conflict'
    wiki.apply_source_batch(wiki.prepare_source_batch(proposal, compiled, source))
    assert wiki.semantic_snapshot('entity')['source_knowledge']['integrations'][0]['relation_state'] == 'unresolved'
    proposal, compiled, source = inputs(('entity',))
    proposal['role'] = source['role'] = 'conflict'
    proposal['targets'][0]['relations'] = [{
        'type': 'compete', 'from_claim_id': 'new-claim', 'to_claim_id': 'old-claim',
        'from_revision_id': REV, 'to_revision_id': 'b' * 64,
        'from_as_of': '2026-10-09', 'to_as_of': '2026-10-08',
        'scope_explanation': 'Different observation windows; not adjudicated.'}]
    wiki.apply_source_batch(wiki.prepare_source_batch(proposal, compiled, source))
    for _ in range(2):
        integration = wiki.semantic_snapshot('entity')['source_knowledge']['integrations'][0]
        assert integration['relation_state'] == 'mapped'
        assert 'not factual adjudication' in integration['relation_note']
        proposal, compiled, source = inputs(('entity',))
        proposal['role'] = source['role'] = 'conflict'
        wiki.apply_source_batch(wiki.prepare_source_batch(proposal, compiled, source))


def test_malformed_historical_relation_does_not_crash_conflict_state(store):
    wiki.upsert_page({'id': 'entity', 'title': 'entity', 'kind': 'concept', 'surface': 'market',
                     'source_knowledge': {'relations': [{'type': 'compete', 'from_revision_id': REV,
                                                        'from_claim_id': [], 'to_claim_id': []}]}})
    proposal, compiled, source = inputs(('entity',))
    proposal['role'] = source['role'] = 'conflict'
    wiki.apply_source_batch(wiki.prepare_source_batch(proposal, compiled, source))
    assert wiki.semantic_snapshot('entity')['source_knowledge']['integrations'][0]['relation_state'] == 'unresolved'


def test_repeated_readability_split_preserves_complete_child_body(store):
    original = "Complete evidence and caveat.\n" * 900
    wiki.upsert_page({"id": "entity", "title": "entity", "body": original,
                      "kind": "concept", "surface": "market"})
    wiki.split_for_readability("entity", 2600)
    wiki.split_for_readability("entity", 2600)
    assert wiki.semantic_snapshot("entity")["body"] == original
    assert wiki.semantic_snapshot("entity")["children"]


def test_resplitting_source_update_preserves_child_only_caveats_and_relations(store):
    original = ''.join(f"Evidence {i:04d} with uncertainty.\n" for i in range(600))
    wiki.upsert_page({"id": "entity", "title": "entity", "body": original,
                      "kind": "concept", "surface": "market"})
    wiki.split_for_readability("entity", 500)
    child = wiki.semantic_snapshot("entity")["children"][1]["raw"]
    relation = {"type": "compete", "from_claim_id": "historical-a", "to_claim_id": "historical-b",
                "scope_explanation": "Unresolved historical contradiction"}
    child['source_knowledge'] = {"limitations": ["Child-only caveat"], "relations": [relation]}
    shared_memory.upsert_record(child)
    proposal, compiled, source = inputs(("entity",))
    batch = wiki.prepare_source_batch(proposal, compiled, source)
    assert wiki.apply_source_batch(batch)['status'] == 'applied'
    snapshot = wiki.semantic_snapshot('entity')
    active_knowledge = [snapshot['page']['source_knowledge'],
                        *[page['source_knowledge'] for page in snapshot['children']]]
    assert any("Child-only caveat" in item.get('limitations', []) for item in active_knowledge)
    assert any(relation in item.get('relations', []) for item in active_knowledge)
    assert snapshot['body'].startswith(original)


def test_stale_child_aborts_whole_batch(store):
    wiki.upsert_page({"id": "entity", "title": "entity", "body": "Old exception. " * 1500,
                      "surface": "market", "kind": "concept", "source_refs": ["https://example.org/old"]})
    batch = wiki.prepare_source_batch(*inputs())
    child = wiki.semantic_snapshot("entity")["children"][0]["raw"]
    shared_memory.upsert_record({**child, "body": child["body"] + " Child changed"})
    before = (store / "events.jsonl").read_bytes()
    result = wiki.apply_source_batch(batch)
    assert result["status"] == "stale-base"
    assert result["changed_ids"] == []
    assert (store / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("ref_count", [13, 21])
def test_provenance_overflow_remains_discoverable(store, ref_count):
    row = wiki.get_page("entity")["raw"]
    refs = [f"https://example.org/history/{i}" for i in range(ref_count)]
    # Older stores can contain more pointers than current metadata limits.
    row.update(artifacts=refs, evidence_ids=[f"old-{i}" for i in range(101)])
    with shared_memory._events_lock():
        rows = shared_memory.inspect_records()
        shared_memory._write_jsonl_locked([row if r["id"] == "entity" else r for r in rows])
    batch = wiki.prepare_source_batch(*inputs(("entity",)))
    assert wiki.apply_source_batch(batch)["status"] == "applied"
    snap = wiki.semantic_snapshot("entity")
    assert set(refs) <= set(snap["source_refs"])
    assert {f"old-{i}" for i in range(101)} <= set(snap["evidence_ids"])
    assert snap["body"].startswith(row["body"])
    assert all(len(r["artifacts"]) <= 20 for r in batch["upserts"])


def test_long_additions_are_lossless_and_usage_changes_are_preserved(store):
    proposal, compiled, source = inputs(("entity",))
    long = "Full prose with exception.\n" * 1200 + "[S1](https://example.org/new) IMPORTANT LAST CAVEAT"
    proposal["targets"][0]["additions"][0]["body"] = long
    before = wiki.semantic_snapshot("entity")["body"]
    batch = wiki.prepare_source_batch(proposal, compiled, source)
    row = wiki.get_page("entity")["raw"]
    shared_memory.upsert_record({**row, "useCount": 7, "lastUsedAt": "2026-10-10", "lastQuery": "question"})
    assert wiki.apply_source_batch(batch)["status"] == "applied"
    after = wiki.semantic_snapshot("entity")
    assert after["body"].startswith(before)
    assert after["body"].endswith(long)
    assert after["page"]["useCount"] == 7
    assert all(len(r["body"]) <= wiki.WIKI_BODY_LIMIT for r in batch["upserts"])


def _apply_worker(batch, queue):
    try:
        queue.put(wiki.apply_source_batch(batch)["status"])
    except Exception as exc:
        queue.put(type(exc).__name__)


def test_two_workers_do_not_overwrite_knowledge(store):
    first = wiki.prepare_source_batch(*inputs(("entity",)))
    proposal, compiled, source = inputs(("entity",))
    proposal["targets"][0]["additions"][0]["body"] += " Another result"
    second = wiki.prepare_source_batch(proposal, compiled, source)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    workers = [ctx.Process(target=_apply_worker, args=(batch, queue)) for batch in (first, second)]
    for worker in workers: worker.start()
    for worker in workers:
        worker.join(8)
        if worker.is_alive(): worker.terminate(); worker.join(); pytest.fail("worker deadlock")
        assert worker.exitcode == 0
    assert sorted(queue.get(timeout=1) for _ in workers) == ["applied", "stale-base"]
    assert wiki.semantic_snapshot("entity")["body"].count("Added capacity") == 1


def test_corrupt_store_is_not_rewritten_as_empty(store):
    batch = wiki.prepare_source_batch(*inputs())
    path = store / "events.jsonl"
    path.write_text(path.read_text() + "not JSON\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="store_corrupt"):
        wiki.apply_source_batch(batch)
    assert path.read_bytes() == before


def test_index_failure_reports_applied_not_false_failure(store, monkeypatch):
    batch = wiki.prepare_source_batch(*inputs())
    def fail_index(): raise OSError("index unavailable")
    monkeypatch.setattr(shared_memory, "_write_index_locked", fail_index)
    result = wiki.apply_source_batch(batch)
    assert result["status"] == "applied" and result["index_pending"] is True
    assert "Added capacity" in wiki.semantic_snapshot("entity")["body"]


def test_semantically_changed_root_or_new_id_collision_is_stale(store):
    batch = wiki.prepare_source_batch(*inputs())
    row = wiki.get_page("topic")["raw"]
    shared_memory.upsert_record({**row, "title": "Changed topic"})
    before = (store / "events.jsonl").read_bytes()
    assert wiki.apply_source_batch(batch)["status"] == "stale-base"
    assert (store / "events.jsonl").read_bytes() == before


def test_new_split_child_collision_aborts_batch(store):
    proposal, compiled, source = inputs(("entity",))
    proposal["targets"][0]["additions"][0]["body"] += " Long prose" * 2000
    batch = wiki.prepare_source_batch(proposal, compiled, source)
    new = next(row for row in batch["upserts"] if batch["expected_before"][row["id"]] is None)
    shared_memory.upsert_record({**new, "title": "Unrelated new record"})
    before = (store / "events.jsonl").read_bytes()
    assert wiki.apply_source_batch(batch)["status"] == "stale-base"
    assert (store / "events.jsonl").read_bytes() == before


def test_cross_surface_batch_is_not_allowed(store):
    row = wiki.get_page("topic")["raw"]
    shared_memory.upsert_record({**row, "source": {**row["source"], "surface": "portfolio"}})
    with pytest.raises(ValueError, match="scope"):
        wiki.prepare_source_batch(*inputs())


def test_duplicate_json_keys_are_corruption_not_last_value_wins(store):
    batch = wiki.prepare_source_batch(*inputs())
    path = store / "events.jsonl"
    path.write_text(path.read_text() + '{"id":"one","id":"two"}\n')
    with pytest.raises(ValueError, match="store_corrupt"):
        wiki.apply_source_batch(batch)


def test_legacy_split_without_explicit_id_remains_compatible(store):
    saved = wiki.upsert_page({"title": "A new long wiki", "body": "Complete prose " * 2000,
                            "surface": "market", "kind": "concept", "source_refs": ["https://example.org"]})
    assert wiki.semantic_snapshot(saved["id"])["body"] == "Complete prose " * 2000
