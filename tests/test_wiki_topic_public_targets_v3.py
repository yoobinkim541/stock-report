"""Current public eligibility for actual v3 committed synthetic topics."""
from datetime import timedelta
import json

import pytest

from agent_console import shared_memory
from reports.wiki_source_journal import _encoded
from reports.wiki_source_notes import materialize_source_note
from reports import wiki_topic_transaction_v3 as transaction
from scripts import run_public_wiki_pilot as public
from test_wiki_source_notes import NOW, store
from test_wiki_topic_v3 import evidence, propose, payload, validate


@pytest.fixture
def committed(evidence, store, tmp_path, monkeypatch, request):
    _, journal, rows = evidence
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    proposal = propose(evidence)
    if getattr(request, "param", False):
        value = payload(evidence[0])
        for addition in value["targets"][0]["additions"]:
            addition["body"] += "\n" + "Synthetic retained qualification. " * 250
        proposal = validate(evidence[0], value)
    # Task3's fixture isolates read-only prompt inputs; this integration uses
    # the actual temporary canonical store for post-commit discovery.
    monkeypatch.setattr(shared_memory, "inspect_records", lambda: shared_memory._read_jsonl_checked(
        shared_memory._paths()["events"]))
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=NOW, apply=True)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=NOW)
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=NOW)
    assert result["newly_committed"], result
    return journal, batch, set(batch["revision_ids"])


def test_actual_v3_witness_and_current_sources_admit_editable_topic(committed):
    journal, batch, revisions = committed
    from reports.wiki_source_notes import pin_source_note
    receipt = journal.load_topic_operation_v3(batch["operation_id"])
    assert receipt["stage"] == "committed"
    rows = shared_memory.inspect_records()
    witness = transaction.topic_commit_record_v3(batch)
    assert [row for row in rows if row["id"] == witness["id"]] == [witness]
    for revision, frozen in batch["source_inputs"].items():
        pin = pin_source_note(frozen["note_id"], journal=journal, now=public.datetime.now(public.timezone.utc))
        assert pin["revision_id"] == revision
        assert all(pin[key] == digest for key, digest in frozen["note_hashes"].items())
    assert all(public._matches_receipt_page(identity, batch, rows) for identity in batch["target_ids"])
    assert set(batch["target_ids"]) <= public.public_target_ids(journal, revisions)


@pytest.mark.parametrize("damage", ["missing_witness", "changed_witness", "changed_target",
    "private_target", "unapproved_revision", "revoked_source", "expired_source"])
def test_stage_or_old_proof_never_substitutes_for_current_authority(committed, monkeypatch, damage):
    journal, batch, revisions = committed
    rows = shared_memory.inspect_records()
    witness_id = transaction.topic_commit_record_v3(batch)["id"]
    if damage == "missing_witness":
        rows = [row for row in rows if row["id"] != witness_id]
    elif damage == "changed_witness":
        next(row for row in rows if row["id"] == witness_id)["topic_batch_sha256"] = "0" * 64
    elif damage in {"changed_target", "private_target"}:
        row = next(row for row in rows if row["id"] == batch["target_ids"][0])
        row["body"] = "Changed prose" if damage == "changed_target" else "PRIVATE SENTINEL"
    elif damage == "unapproved_revision":
        revisions.pop()
    else:
        source = journal.load_source(batch["revision_ids"][0])["source"]
        from pathlib import Path
        path = Path(source["manifest_ref"])
        manifest = json.loads(path.read_text())
        if damage == "revoked_source":
            manifest["private"] = True
        else:
            manifest["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
        path.write_text(_encoded(manifest))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    assert set(batch["target_ids"]).isdisjoint(public.public_target_ids(journal, revisions))


def test_receipt_batch_edit_with_recomputed_checksum_does_not_match_store_witness(committed):
    journal, batch, revisions = committed
    path = journal._path("topic-operations-v3", batch["operation_id"])
    receipt = json.loads(path.read_text())
    receipt["batch"]["public_proof"]["export_metadata"]["target_ids"] = []
    import hashlib
    receipt["batch_sha256"] = hashlib.sha256(_encoded(receipt["batch"]).encode()).hexdigest()
    path.write_text(_encoded(receipt))
    assert set(batch["target_ids"]).isdisjoint(public.public_target_ids(journal, revisions))


def test_missing_receipt_cannot_be_replaced_by_witness_alone(committed):
    journal, batch, revisions = committed
    journal._path("topic-operations-v3", batch["operation_id"]).unlink()
    assert set(batch["target_ids"]).isdisjoint(public.public_target_ids(journal, revisions))


@pytest.mark.parametrize("committed", [True], indirect=True)
def test_witnessed_split_children_remain_eligible_without_invented_pages(committed):
    journal, batch, revisions = committed
    root = batch["target_ids"][0]
    children = {row["id"] for row in batch["upserts"] if row.get("parent_page_id") == root}
    assert children
    assert children <= public.public_target_ids(journal, revisions)


def test_v3_hydration_and_validation_use_current_witness(committed):
    journal, batch, revisions = committed
    identity = batch["target_ids"][0]
    snapshot = public.hydrate_public_target(identity, journal, revisions)
    public.validate_public_targets([snapshot], journal, revisions)
    rows = shared_memory.inspect_records()
    next(row for row in rows if row["id"] == identity)["body"] += " private drift"
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    with pytest.raises(ValueError, match="public_target_changed"):
        public.validate_public_targets([snapshot], journal, revisions)


@pytest.mark.parametrize("directory,version", [("operations", 3), ("operations", 99),
    ("topic-operations", 3), ("topic-operations-v3", 2), ("topic-operations-v3", 99)])
def test_unknown_or_misplaced_receipt_never_grants_authority(committed, directory, version):
    journal, batch, revisions = committed
    original = journal._path("topic-operations-v3", batch["operation_id"])
    receipt = json.loads(original.read_text())
    original.unlink()
    receipt["schema_version"] = version
    receipt["batch"]["protocol_version"] = version
    receipt.update(revision_id=batch["proposal"]["revision_id"])
    path = journal._path(directory, batch["operation_id"])
    path.parent.mkdir(exist_ok=True)
    path.write_text(_encoded(receipt))
    assert set(batch["target_ids"]).isdisjoint(public.public_target_ids(journal, revisions))
