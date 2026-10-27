"""Synthetic, temporary-store tests for the inactive v2 transaction boundary."""
import copy
import importlib
import json
import hashlib
from pathlib import Path

import pytest

from agent_console import shared_memory, wiki
from test_wiki_topic_integration import proposal, validate, NOW


def api():
    return importlib.import_module("reports.wiki_topic_transaction")


def build_transaction(evidence, tmp_path, monkeypatch):
    data, journal, rows = evidence
    # Task 2's minimal public-allowlist fixtures are not full v1 receipts.
    for path in (journal.root / "operations").glob("*.json"):
        old = json.loads(path.read_text())
        identity = hashlib.sha256(path.stem.encode()).hexdigest()
        old.update(operation_id=identity, artifacts="done", search="done", log="done")
        path.unlink()
        journal._path("operations", identity).write_text(json.dumps(old))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda **kw: {"ok": True})
    monkeypatch.setattr(wiki, "sync_qmd", lambda: {"ok": True})
    monkeypatch.setattr(wiki, "_append_wiki_log", lambda *a, **kw: None)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    data["context"]["notes"] = data["context"]["notes"][:1]
    data["context"]["dependencies"] = data["context"]["dependencies"][:1]
    return data, journal, rows


def prepare(transaction):
    data, journal, rows = transaction
    return api().prepare_topic_batch(validate(data, proposal(data)), journal=journal, records=rows, now=NOW)


def recover(batch, journal):
    return api().recover_topic_operation(batch["operation_id"], journal=journal, now=NOW)


def test_seed_retry_readonly_and_v1_enumeration(transaction):
    data, journal, rows = transaction
    originals = {str(p): p.read_bytes() for p in journal.root.parent.rglob("*") if p.is_file()}
    batch = prepare(transaction)
    assert batch["protocol_version"] == 2
    assert not journal.pending_operations()
    assert len(journal.pending_topic_operations()) == 1
    assert recover(batch, journal)["stage"] == "committed"
    after = shared_memory.inspect_records()
    assert [r for r in after if r["id"] in {x["id"] for x in rows}] == rows
    snap = wiki.semantic_snapshot(batch["target_ids"][0])
    assert len({c["source_revision_id"] for c in snap["claims"]}) == 2
    assert not recover(batch, journal)["newly_committed"]
    assert shared_memory.inspect_records() == after
    assert prepare(transaction) == batch
    for path, content in originals.items():
        if "/memory/" not in path:
            assert Path(path).read_bytes() == content
    assert not journal.pending_topic_operations()


@pytest.mark.parametrize("damage", ["page", "removed", "raw", "text", "compiled", "expiry", "receipt"])
def test_dependency_invalid_before_commit_is_atomic(transaction, damage):
    data, journal, rows = transaction
    batch = prepare(transaction)
    dep = data["context"]["dependencies"][0]
    receipt = journal.load_source(dep["revision_id"])
    if damage in {"page", "removed"}:
        changed = copy.deepcopy(rows)
        if damage == "removed": changed = [r for r in changed if r["id"] != dep["page_id"]]
        else: next(r for r in changed if r["id"] == dep["page_id"])["body"] += " changed"
        with shared_memory._events_lock():
            shared_memory._write_jsonl_locked(changed)
    elif damage == "receipt": journal._path("sources", dep["revision_id"]).unlink()
    elif damage == "expiry":
        path = Path(receipt["source"]["manifest_ref"])
        manifest = json.loads(path.read_text())
        manifest["expires_at"] = NOW.isoformat()
        path.write_text(json.dumps(manifest))
    else:
        path = receipt["compile_ref"] if damage == "compiled" else receipt["source"][damage + "_ref"]
        Path(path).write_text("corrupt")
    before = shared_memory.inspect_records()
    assert recover(batch, journal)["stage"] == "stale-base"
    assert shared_memory.inspect_records() == before


@pytest.mark.parametrize("when", ["before", "after"])
def test_crash_commit_recovery(transaction, monkeypatch, when):
    _, journal, _ = transaction
    batch = prepare(transaction)
    real = shared_memory.guarded_batch_upsert
    def crash(**kw):
        if when == "after": real(**kw)
        raise RuntimeError("synthetic crash")
    monkeypatch.setattr(shared_memory, "guarded_batch_upsert", crash)
    with pytest.raises(RuntimeError): recover(batch, journal)
    monkeypatch.setattr(shared_memory, "guarded_batch_upsert", real)
    assert recover(batch, journal)["stage"] == "committed"
    assert len(wiki.semantic_snapshot(batch["target_ids"][0])["claims"]) == 2


@pytest.mark.parametrize("effect", ["artifacts", "search", "log"])
def test_effect_failure_retry(transaction, monkeypatch, effect):
    _, journal, _ = transaction
    batch = prepare(transaction)
    name = {"artifacts": "rebuild_artifacts", "search": "sync_qmd", "log": "_append_wiki_log"}[effect]
    real = getattr(wiki, name)
    def fail(*a, **kw): raise RuntimeError("synthetic effect failure")
    monkeypatch.setattr(wiki, name, fail)
    result = recover(batch, journal)
    assert result["stage"] == "committed" and result["effects"][effect] == "failed"
    before = shared_memory.inspect_records()
    monkeypatch.setattr(wiki, name, real)
    assert set(recover(batch, journal)["effects"].values()) == {"done"}
    assert shared_memory.inspect_records() == before


def test_atomic_guard_catches_ordinary_writer(transaction, monkeypatch):
    data, journal, rows = transaction
    batch = prepare(transaction)
    real = shared_memory.guarded_batch_upsert
    def race(**kw):
        assert data["context"]["notes"][0]["page_id"] in kw["expected"]
        changed = copy.deepcopy(rows)
        changed[0]["body"] += " ordinary writer"
        with shared_memory._events_lock(): shared_memory._write_jsonl_locked(changed)
        return real(**kw)
    monkeypatch.setattr(shared_memory, "guarded_batch_upsert", race)
    assert recover(batch, journal)["stage"] == "stale-base"
    assert not any(r["id"] in batch["target_ids"] for r in shared_memory.inspect_records())


def test_third_source_append_preserves_prior_counterevidence(transaction):
    data, journal, _ = transaction
    from reports.wiki_topic_context import select_topic_context
    # Seed current + first note; the second note is a later independent source.
    batch = prepare(transaction)
    assert recover(batch, journal)["stage"] == "committed"
    page_id = batch["target_ids"][0]
    before = wiki.semantic_snapshot(page_id)
    revision = next(r for r in journal.root.joinpath("sources").glob("*.json")
                    if json.loads(r.read_text())["source"]["canonical_url"].endswith("/2")).stem
    source, compiled, _ = api()._verified_source(journal, revision, NOW)
    source.update(role="conflict", observed_evidence_ids=["opposing-evidence"])
    journal.save_compilation(source, compiled)
    rows = shared_memory.inspect_records()
    context = select_topic_context(compiled, journal=journal, records=rows, now=NOW, surface="market")
    later = dict(source=source, compiled=compiled, context=context, targets=[before])
    payload = proposal(later)
    t = payload["targets"][0]
    for key in ("title", "kind", "surface", "purpose"): t.pop(key)
    t.update(page_id=page_id, before_hash=before["revision_hash"])
    checked = validate(later, payload)
    append = api().prepare_topic_batch(checked, journal=journal, records=rows, now=NOW)
    assert recover(append, journal)["stage"] == "committed"
    after = wiki.semantic_snapshot(page_id)
    assert after["body"].startswith(before["body"])
    assert len({c["source_revision_id"] for c in after["claims"]}) == 3
    assert all(c in after["claims"] for c in before["claims"])
    assert "opposing-evidence" in after["conflicting_evidence_ids"]
    assert all(x in after["source_knowledge"]["limitations"] for x in before["source_knowledge"]["limitations"])


@pytest.mark.parametrize("damage", ["raw", "text", "compiled", "expired", "missing"])
def test_current_source_revalidated_at_commit(transaction, damage):
    data, journal, _ = transaction
    batch = prepare(transaction)
    source = data["source"]
    stored = journal.load_source(source["revision_id"])
    if damage == "expired":
        path = Path(source["manifest_ref"])
        value = json.loads(path.read_text())
        value["expires_at"] = NOW.isoformat()
        path.write_text(json.dumps(value))
    elif damage == "missing": journal._path("sources", source["revision_id"]).unlink()
    else: Path(stored["compile_ref"] if damage == "compiled" else source[damage + "_ref"]).write_text("tampered")
    assert recover(batch, journal)["stage"] == "stale-base"


def test_overlapping_multi_target_transactions_are_not_sequential(transaction):
    data, journal, rows = transaction
    payload = proposal(data)
    second = copy.deepcopy(payload["targets"][0])
    second["title"] = "Separate topic"
    payload["targets"].append(second)
    first = api().prepare_topic_batch(validate(data, payload), journal=journal, records=rows, now=NOW)
    alternative = copy.deepcopy(payload)
    alternative["targets"][0]["additions"][0]["body"] = "Alternative synthesis [S1]"
    other = api().prepare_topic_batch(validate(data, alternative), journal=journal, records=rows, now=NOW)
    assert recover(first, journal)["stage"] == "committed"
    before = shared_memory.inspect_records()
    assert recover(other, journal)["stage"] == "stale-base"
    assert shared_memory.inspect_records() == before


def test_v1_pending_and_public_allowlist_unchanged_with_v2(transaction):
    from scripts.run_public_wiki_pilot import public_target_ids
    data, journal, rows = transaction
    batch = prepare(transaction)
    legacy = journal.prepare({"operation_id": "f" * 64, "revision_id": data["source"]["revision_id"],
        "target_ids": [], "expected_after": {}})
    assert journal.pending_operations() == [legacy]
    expected = public_target_ids(journal, {d["revision_id"] for d in data["context"]["dependencies"]}, records=rows)
    assert expected == {data["context"]["notes"][0]["page_id"]}
    assert recover(batch, journal)["stage"] == "committed"
    assert journal.pending_operations() == [legacy]


def test_split_child_mutation_rejects_and_overflow_is_lossless(transaction):
    data, journal, rows = transaction
    row = {"id": "old-topic", "title": "Existing topic", "body": "Old exceptions. " * 900,
        "kind": "concept", "surface": "market", "status": "draft",
        "source_refs": [f"https://example.org/{i}" for i in range(18)],
        "evidence_ids": [f"prior-{i}" for i in range(100)]}
    children = wiki._prepare_split_records(row, records=rows)
    assert len(children) > 1
    with shared_memory._events_lock(): shared_memory._write_jsonl_locked([*rows, *children])
    rows = shared_memory.inspect_records()
    snap = wiki.semantic_snapshot("old-topic", records=rows)
    data["targets"] = [snap]
    payload = proposal(data)
    target = payload["targets"][0]
    for key in ("title", "kind", "surface", "purpose"): target.pop(key)
    target.update(page_id="old-topic", before_hash=snap["revision_hash"])
    batch = api().prepare_topic_batch(validate(data, payload), journal=journal, records=rows, now=NOW)
    assert any(r.get("provenance_only") for r in batch["upserts"])
    projected = [r for r in rows if r["id"] not in {u["id"] for u in batch["upserts"]}] + batch["upserts"]
    after = wiki.semantic_snapshot("old-topic", records=projected)
    assert after["body"].startswith(snap["body"])
    assert set(snap["source_refs"]) <= set(after["source_refs"])
    assert set(snap["evidence_ids"]) <= set(after["evidence_ids"])
    child_id = snap["children"][0]["id"]
    next(r for r in rows if r["id"] == child_id)["body"] += " changed child"
    with shared_memory._events_lock(): shared_memory._write_jsonl_locked(rows)
    assert recover(batch, journal)["stage"] == "stale-base"


def test_effect_failure_reason_survives_other_successes(transaction, monkeypatch):
    _, journal, _ = transaction
    batch = prepare(transaction)
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda **kw: {"ok": False})
    result = recover(batch, journal)
    assert result["reason"] == "artifacts_refresh_failed"


def test_after_commit_crash_then_later_edit_recovers_effects_only(transaction, monkeypatch):
    _, journal, _ = transaction
    batch = prepare(transaction)
    real = shared_memory.guarded_batch_upsert
    def crash(**kw):
        real(**kw)
        raise RuntimeError("after commit")
    monkeypatch.setattr(shared_memory, "guarded_batch_upsert", crash)
    with pytest.raises(RuntimeError): recover(batch, journal)
    monkeypatch.setattr(shared_memory, "guarded_batch_upsert", real)
    rows = shared_memory.inspect_records()
    next(r for r in rows if r["id"] == batch["target_ids"][0])["body"] += " Later append"
    with shared_memory._events_lock(): shared_memory._write_jsonl_locked(rows)
    assert recover(batch, journal)["stage"] == "committed"
    assert shared_memory.inspect_records() == rows


def test_committed_effect_retry_ignores_now_unavailable_dependency(transaction, monkeypatch):
    data, journal, _ = transaction
    batch = prepare(transaction)
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda **kw: {"ok": False})
    assert recover(batch, journal)["stage"] == "committed"
    rows = shared_memory.inspect_records()
    row = next(r for r in rows if r["id"] == data["context"]["notes"][0]["page_id"])
    row["tags"] = ["status:archived" if t.startswith("status:") else t for t in row["tags"]]
    with shared_memory._events_lock(): shared_memory._write_jsonl_locked(rows)
    journal._path("sources", data["context"]["notes"][0]["revision_id"]).unlink()
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda **kw: {"ok": True})
    assert recover(batch, journal)["effects"]["artifacts"] == "done"
    assert shared_memory.inspect_records() == rows


def test_concurrent_overlapping_recovery_one_winner(transaction):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    data, journal, rows = transaction
    first = prepare(transaction)
    payload = proposal(data)
    payload["targets"][0]["additions"][0]["body"] = "Concurrent alternative [S1]"
    second = api().prepare_topic_batch(validate(data, payload), journal=journal, records=rows, now=NOW)
    barrier = Barrier(2)
    def run(batch):
        barrier.wait(timeout=5)
        return recover(batch, journal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [first, second]))
    assert sorted(r["stage"] for r in results) == ["committed", "stale-base"]
    assert len(wiki.semantic_snapshot(first["target_ids"][0])["claims"]) == 2


def test_all_claims_and_competing_ids_survive_provenance_overflow(transaction):
    from reports.wiki_source_compiler import validate_compilation
    data, journal, rows = transaction
    source = data["source"]
    journal._path("sources", source["revision_id"]).unlink()
    compiled = validate_compilation({"action": "create", "title": "Shipping",
        "body": "Dated accounts [S1]", "terms": ["Hormuz"], "limitations": ["All qualifications remain"],
        "claims": [{"text": f"Account {i}", "as_of": "2026-10-15", "scope": "Hormuz shipping"}
                   for i in range(115)]}, source)
    journal.save_compilation(source, compiled)
    data["compiled"] = compiled
    checked = validate(data, proposal(data))
    batch = api().prepare_topic_batch(checked, journal=journal, records=rows, now=NOW)
    assert recover(batch, journal)["stage"] == "committed"
    snap = wiki.semantic_snapshot(batch["target_ids"][0])
    assert len(snap["claims"]) == 116
    assert {c["claim_id"] for c in snap["claims"]} == {c["claim_id"] for c in checked["targets"][0]["original_claims"]}
    assert any(c["raw"].get("provenance_only") for c in snap["children"])


def test_v2_receipt_rejects_missing_required_fields(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    path = journal._path("topic-operations", batch["operation_id"])
    receipt = json.loads(path.read_text())
    receipt.pop("error_code")
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="topic_journal_corrupt"):
        journal.load_topic_operation(batch["operation_id"])


def test_ingest_log_lists_contributors_not_unused_context(transaction, monkeypatch):
    from reports.wiki_topic_context import select_topic_context
    data, journal, rows = transaction
    data["context"] = select_topic_context(data["compiled"], journal=journal, records=rows, now=NOW, surface="market")
    payload = proposal(data)
    target = payload["targets"][0]
    unused = target["contributing_revision_ids"].pop()
    target["additions"].pop()
    checked = validate(data, payload)
    batch = api().prepare_topic_batch(checked, journal=journal, records=rows, now=NOW)
    events = []
    monkeypatch.setattr(wiki, "_append_wiki_log", lambda action, **kw: events.append((action, kw)))
    assert recover(batch, journal)["stage"] == "committed"
    assert len(events) == 1
    action, metadata = events[0]
    assert action == "topic_ingest"
    assert unused not in metadata["revision_ids"]
    assert metadata["title"] == target["title"]
    assert set(metadata) == {"operation_id", "page_ids", "revision_ids", "title"}


def rewrite_batch(journal, batch, mutate):
    path = journal._path("topic-operations", batch["operation_id"])
    receipt = json.loads(path.read_text())
    mutate(receipt["batch"])
    # A checksum is deliberately NOT an authentication boundary.
    receipt["batch_sha256"] = api()._digest(receipt["batch"])
    path.write_text(json.dumps(receipt))


@pytest.mark.parametrize("damage", ["body", "target", "dependency", "source_hashes", "snapshots", "before", "marker",
                                    "readonly_upsert", "redirect"])
def test_recomputed_batch_checksum_cannot_authorize_unvalidated_writes(transaction, damage):
    _, journal, _ = transaction
    batch = prepare(transaction)
    rows = shared_memory.inspect_records()
    def tamper(frozen):
        row = frozen["upserts"][0]
        if damage == "body": row["body"] = "UNVALIDATED REPLACEMENT"
        elif damage == "target": frozen["target_ids"] = ["unlisted-target"]
        elif damage == "dependency": frozen["dependencies"] = []
        elif damage == "source_hashes": frozen["source_hashes"] = {}
        elif damage == "snapshots": frozen["snapshots"] = [copy.deepcopy(rows[0])]
        elif damage == "before": frozen["expected_before"].pop(next(d["page_id"] for d in frozen["proposal"]["dependencies"]))
        elif damage in {"readonly_upsert", "redirect"}:
            row["id"] = rows[0]["id"] if damage == "readonly_upsert" else "unlisted-target"
            frozen["expected_before"][row["id"]] = wiki._source_fingerprint(row["id"], rows)
        else: row["source_knowledge"]["topic_operations"] = ["forged-marker"]
        ids = {r["id"] for r in frozen["upserts"]}
        projected = [r for r in rows if r["id"] not in ids] + frozen["upserts"]
        frozen["expected_after"] = {pid: wiki._source_fingerprint(pid, projected) for pid in ids}
    rewrite_batch(journal, batch, tamper)
    result = recover(batch, journal)
    assert result["stage"] == "stale-base"
    assert shared_memory.inspect_records() == rows


def test_forged_target_marker_does_not_prove_commit(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    rows = shared_memory.inspect_records()
    forged = copy.deepcopy(batch["upserts"][0])
    forged["body"] = "UNVALIDATED REPLACEMENT"
    with shared_memory._events_lock(): shared_memory._write_jsonl_locked([*rows, forged])
    before = shared_memory.inspect_records()
    assert recover(batch, journal)["stage"] == "stale-base"
    assert shared_memory.inspect_records() == before


def test_committed_batch_tamper_cannot_use_existing_marker(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    assert recover(batch, journal)["stage"] == "committed"
    rows = shared_memory.inspect_records()
    rewrite_batch(journal, batch, lambda frozen: frozen.update(target_ids=["forged-target"]))
    with pytest.raises(ValueError, match="topic_commit_witness_mismatch"):
        recover(batch, journal)
    assert shared_memory.inspect_records() == rows


def test_forged_committed_receipt_without_store_witness_is_rejected(transaction):
    _, journal, _ = transaction
    batch = prepare(transaction)
    journal.transition_topic(batch["operation_id"], stage="committed")
    before = shared_memory.inspect_records()
    with pytest.raises(ValueError, match="topic_commit_witness_mismatch"):
        recover(batch, journal)
    assert shared_memory.inspect_records() == before


def test_frozen_split_writes_do_not_depend_on_recovery_wall_clock(transaction, monkeypatch):
    data, journal, rows = transaction
    payload = proposal(data)
    payload["targets"][0]["additions"][0]["body"] = "Attributed account. " * 900 + " [S1]"
    batch = api().prepare_topic_batch(validate(data, payload), journal=journal, records=rows, now=NOW)
    assert len(batch["upserts"]) > 1
    monkeypatch.setattr(wiki, "_now", lambda: "2099-01-01T00:00:00+00:00")
    monkeypatch.setattr(shared_memory, "_now", lambda: "2099-01-01T00:00:00+00:00")
    assert recover(batch, journal)["stage"] == "committed"


def test_topic_links_summary_with_derived_reverse_link_and_preserves_receipt(transaction):
    data, journal, rows = transaction
    current_id = hashlib.sha256(("compiled-source:" + data["source"]["revision_id"]).encode()).hexdigest()
    rows.append({**copy.deepcopy(rows[0]), "id": current_id, "title": "Current source",
                 "source_knowledge": {"claims": data["compiled"]["claims"]}})
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = prepare(transaction)
    assert recover(batch, journal)["stage"] == "committed"
    topic = wiki.get_page(batch["target_ids"][0])
    summary_id = data["context"]["notes"][0]["page_id"]
    assert summary_id in topic["links"]
    assert topic["id"] in wiki.get_page(summary_id)["backlinks"]
    assert current_id in topic["links"]
    assert topic["id"] in wiki.get_page(current_id)["backlinks"]
    from reports.wiki_topic_context import revalidate_topic_context
    revalidate_topic_context(data["context"], journal=journal,
                             records=shared_memory.inspect_records(), now=NOW)
    assert next(row for row in shared_memory.inspect_records() if row["id"] == summary_id) == next(
        row for row in rows if row["id"] == summary_id)


def test_competing_claims_survive_reusing_same_sources(transaction):
    data, journal, rows = transaction
    payload = proposal(data)
    target = payload["targets"][0]
    first, second = target["additions"]
    relation = {"type": "compete", "from_claim_id": first["claim_ids"][0],
        "to_claim_id": second["claim_ids"][0], "from_revision_id": first["revision_ids"][0],
        "to_revision_id": second["revision_ids"][0], "from_as_of": first["as_of"],
        "to_as_of": second["as_of"], "scope_explanation": "Unresolved dated accounts"}
    target["relations"] = [relation]
    batch = api().prepare_topic_batch(validate(data, payload), journal=journal, records=rows, now=NOW)
    assert recover(batch, journal)["stage"] == "committed"
    before = wiki.semantic_snapshot(batch["target_ids"][0])
    by_id = {claim["claim_id"]: claim for claim in before["claims"]}
    assert second["claim_ids"][0] in by_id[first["claim_ids"][0]]["competing_claim_ids"]
    assert first["claim_ids"][0] in by_id[second["claim_ids"][0]]["competing_claim_ids"]
    data["targets"] = [before]
    for key in ("title", "kind", "surface", "purpose"):
        target.pop(key)
    target.update(page_id=before["page_id"], before_hash=before["revision_hash"])
    target["relations"] = []
    fresh = shared_memory.inspect_records()
    appended = api().prepare_topic_batch(validate(data, payload), journal=journal, records=fresh, now=NOW)
    assert recover(appended, journal)["stage"] == "committed"
    after = wiki.semantic_snapshot(before["page_id"])
    assert after["body"].startswith(before["body"])
    assert before["claims"] == after["claims"]
