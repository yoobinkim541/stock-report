import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from agent_console import shared_memory
from test_wiki_topic_v3 import propose, add_target, payload, update_payload, validate

from reports.wiki_source_journal import Journal, _encoded


@pytest.fixture
def evidence_fixture(source_note_evidence):
    return source_note_evidence


@pytest.mark.parametrize("prepared_at", ["2026-10-21T00:00:00+00:00", "2026-10-20T00:00:00", "not-a-time"])
def test_frozen_prepared_time_is_aware_and_not_in_future(evidence_fixture, store, tmp_path, monkeypatch, prepared_at):
    from reports import wiki_topic_transaction_v3 as transaction
    from reports.wiki_source_notes import materialize_source_note
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    proposal = propose(evidence_fixture)
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    before = {str(path): path.read_bytes() for path in journal.root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="topic_prepared_time_invalid"):
        transaction._freeze(proposal, journal=journal, records=rows, now=now, prepared_at=prepared_at)
    assert {str(path): path.read_bytes() for path in journal.root.rglob("*") if path.is_file()} == before


def test_v3_commit_witness_binds_frozen_source_inputs_and_public_assets():
    from reports import wiki_topic_transaction_v3 as transaction
    proposal = {"protocol_version": 3, "revision_id": "a" * 64, "role": "support",
                "observed_evidence_ids": ["e1"], "context": {"notes": [], "dependencies": [], "omitted": []},
                "dependencies": [], "targets": [], "refusal": "insufficient", "source_map": {}}
    operation_id = hashlib.sha256(_encoded(proposal).encode()).hexdigest()
    frozen = {"protocol_version": 3, "operation_id": operation_id, "proposal": proposal,
              "source_inputs": {"a" * 64: {"input_sha256": "1" * 64, "note_assets": {
                  "note_json_bytes": "{}", "note_md_bytes": "# note", "manifest_bytes": "{}"}}},
              "public_proof": {"export_metadata": {"generation": 7}}, "expected_before": {},
              "expected_after": {}, "target_ids": [], "upserts": [],
              "prepared_at": "2026-10-20T00:00:00+00:00"}
    witness = transaction.topic_commit_record_v3(frozen)
    assert witness["topic_batch_sha256"] == hashlib.sha256(_encoded(frozen).encode()).hexdigest()
    assert witness["topic_batch_sha256"] != hashlib.sha256(_encoded({**frozen,
        "source_inputs": {}}).encode()).hexdigest()
    assert witness["topic_protocol_version"] == 3


def test_v3_recovery_does_not_use_legacy_transaction_dispatch(tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    from reports import wiki_topic_transaction as legacy
    journal = Journal(tmp_path / "journal")
    monkeypatch.setattr(legacy, "recover_topic_operation", lambda *a, **k: pytest.fail("legacy recovery called"))
    with pytest.raises(ValueError, match="topic_operation_unavailable"):
        transaction.recover_topic_operation_v3("a" * 64, journal=journal, now=datetime.now(timezone.utc))


def test_committed_receipt_without_atomic_store_witness_is_not_authority(tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    journal = Journal(tmp_path / "journal")
    proposal = {"protocol_version": 3, "revision_id": "a" * 64, "role": "support",
        "observed_evidence_ids": [], "context": {"notes": [], "dependencies": [], "omitted": []},
        "dependencies": [], "targets": [], "refusal": "insufficient", "source_map": {}}
    operation_id = hashlib.sha256(_encoded(proposal).encode()).hexdigest()
    batch = {"protocol_version": 3, "operation_id": operation_id, "proposal": proposal,
        "revision_ids": [], "source_map": {}, "dependencies": [], "source_inputs": {},
        "public_proof": {"export_metadata": {"protocol_version": 3, "source_note_ids": [], "target_ids": []},
            "source_assets": {}}, "original_claims": {}, "target_ids": [], "expected_before": {},
        "expected_after": {}, "upserts": [], "snapshots": [], "prepared_at": "2026-10-20T00:00:00+00:00"}
    journal.prepare_topic_v3(batch)
    journal.transition_topic_v3(operation_id, stage="committed")
    monkeypatch.setattr(transaction, "_rows", lambda: [])
    with pytest.raises(ValueError, match="topic_commit_witness_mismatch"):
        transaction.recover_topic_operation_v3(operation_id, journal=journal,
            now=datetime(2026, 10, 20, tzinfo=timezone.utc))


@pytest.mark.parametrize("receipt_stage", ["committed", "prepared"])
@pytest.mark.parametrize("mutation", ["float_protocol", "nested_bool_int"])
def test_recovery_requires_structurally_exact_store_witness(tmp_path, monkeypatch, receipt_stage, mutation):
    from reports import wiki_topic_transaction_v3 as transaction
    journal = Journal(tmp_path / "journal")
    proposal = {"protocol_version": 3, "revision_id": "a" * 64, "role": "support",
        "observed_evidence_ids": [], "context": {"notes": [], "dependencies": [], "omitted": []},
        "dependencies": [], "targets": [], "refusal": "insufficient", "source_map": {}}
    operation_id = hashlib.sha256(_encoded(proposal).encode()).hexdigest()
    batch = {"protocol_version": 3, "operation_id": operation_id, "proposal": proposal,
        "revision_ids": [], "source_map": {}, "dependencies": [], "source_inputs": {},
        "public_proof": {"export_metadata": {"protocol_version": 3, "source_note_ids": [], "target_ids": []},
            "source_assets": {}}, "original_claims": {}, "target_ids": [], "expected_before": {},
        "expected_after": {}, "upserts": [], "snapshots": [], "prepared_at": "2026-10-20T00:00:00+00:00"}
    journal.prepare_topic_v3(batch)
    if receipt_stage == "committed":
        journal.transition_topic_v3(operation_id, stage="committed")
    witness = transaction.topic_commit_record_v3(batch)
    if mutation == "float_protocol":
        witness["topic_protocol_version"] = 3.0
    else:
        witness["source"]["sequence"] = False
        expected = transaction.topic_commit_record_v3(batch)
        expected["source"]["sequence"] = 0
        monkeypatch.setattr(transaction, "topic_commit_record_v3", lambda _: expected)
    monkeypatch.setattr(transaction, "_rows", lambda: [witness])
    with pytest.raises(ValueError, match="topic_commit_witness_mismatch"):
        transaction.recover_topic_operation_v3(operation_id, journal=journal,
            now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert journal.load_topic_operation_v3(operation_id)["stage"] == receipt_stage


def test_genuine_v3_seed_freezes_json_safe_note_assets(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    data, journal, rows = evidence_fixture
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal,
            now=datetime(2026, 10, 20, tzinfo=timezone.utc), apply=True)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows,
                                                now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert batch["protocol_version"] == 3
    assert set(batch["source_inputs"]) == {x["revision_id"] for x in proposal["source_map"].values()}
    assert all(set(item["note_assets"]) == {"note_json_bytes", "note_md_bytes", "manifest_bytes"}
               for item in batch["source_inputs"].values())
    assert all(type(blob) is str for item in batch["source_inputs"].values()
               for blob in item["note_assets"].values())
    assert set(batch["expected_before"]).isdisjoint({d["note_id"] for d in proposal["dependencies"]})
    from reports.wiki_source_notes import pin_source_note
    import base64
    for item in batch["source_inputs"].values():
        pin = pin_source_note(item["note_id"], journal=journal,
                              now=datetime(2026, 10, 20, tzinfo=timezone.utc))
        assert base64.b64decode(item["note_assets"]["note_json_bytes"]) == pin["note_json_bytes"]
        assert base64.b64decode(item["note_assets"]["note_md_bytes"]) == pin["note_md_bytes"]
        assert base64.b64decode(item["note_assets"]["manifest_bytes"]) == pin["manifest_bytes"]


def test_prepare_never_materializes_missing_current_source_note(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    proposal = propose(evidence_fixture)
    current = proposal["revision_id"]
    from reports.wiki_source_notes import materialize_source_note
    preview = materialize_source_note(current, journal=journal,
        now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    note_path = journal.root / "source-notes" / preview["note_id"]
    assert not note_path.exists()
    before = {str(path): path.read_bytes() for path in journal.root.rglob("*") if path.is_file()
              and "-locks" not in str(path)}
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    with pytest.raises(ValueError):
        transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows,
            now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert not note_path.exists()
    assert before == {str(path): path.read_bytes() for path in journal.root.rglob("*") if path.is_file()
                      and "-locks" not in str(path)}


def test_new_target_identity_must_be_absent_and_absence_is_cas(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    target_id = proposal["targets"][0]["page_id"]
    rows.append(shared_memory.normalize_record({"id": target_id, "title": "Private collision",
        "surface": "portfolio", "tags": ["wiki", "surface:portfolio", "status:draft"]}))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    with pytest.raises(ValueError, match="topic_new_target_collision"):
        transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)

    rows.pop()
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    assert batch["expected_before"][target_id] is None


def test_proposal_structure_comparison_distinguishes_bool_and_int():
    from reports import wiki_topic_transaction_v3 as transaction
    assert not transaction._strict_equal({"anchor": {"start": False}}, {"anchor": {"start": 0}})


def test_task3_revalidation_rejects_bool_int_shape_drift(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    from reports import wiki_topic_v3 as task3
    _, journal, rows = evidence_fixture
    proposal = propose(evidence_fixture)
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    for entry in proposal["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    original = task3.validate_topic_integration_v3
    def altered(*args, **kwargs):
        checked = original(*args, **kwargs)
        checked["context"]["notes"][0]["claims"][0]["anchor"]["start"] = False
        return checked
    monkeypatch.setattr(task3, "validate_topic_integration_v3", altered)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    with pytest.raises(ValueError, match="topic_proposal_changed"):
        transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)


def test_float_protocol_is_rejected_before_v3_receipt_write(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    proposal = propose(evidence_fixture)
    proposal["protocol_version"] = 3.0
    with pytest.raises(ValueError, match="topic_protocol_invalid"):
        transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows,
            now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert not (journal.root / "topic-operations-v3").exists()


def test_stale_base_receipt_cannot_become_committable_after_inputs_restore(
        evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    journal.transition_topic_v3(batch["operation_id"], stage="stale-base")
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "stale-base"
    assert not any(row["id"] == batch["target_ids"][0] for row in shared_memory.inspect_records())


@pytest.mark.parametrize("source_kind", ["current", "support"])
def test_expired_current_or_support_source_stales_before_commit(
        evidence_fixture, store, tmp_path, monkeypatch, source_kind):
    import json
    from datetime import timedelta
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    proposal = propose(evidence_fixture)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    for entry in proposal["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    revision = proposal["revision_id"] if source_kind == "current" else next(
        value["revision_id"] for key, value in proposal["source_map"].items() if key != "S1")
    source_receipt = journal.load_source(revision)
    from pathlib import Path
    manifest_path = Path(source_receipt["source"]["manifest_ref"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    from reports.wiki_source_journal import _encoded
    manifest_path.write_text(_encoded(manifest), encoding="utf-8")
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "stale-base"
    assert not any(row["id"] in batch["target_ids"] for row in shared_memory.inspect_records())


def test_new_target_stale_cas_does_not_overwrite_competing_record(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    proposal = propose(evidence_fixture)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    for entry in proposal["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    competitor = shared_memory.normalize_record({"id": batch["target_ids"][0], "title": "Concurrent page",
        "surface": "market", "tags": ["wiki", "surface:market"]})
    with shared_memory._events_lock():
        current = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
        shared_memory._write_jsonl_locked([*current, competitor])
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "stale-base"
    assert [row for row in transaction._rows() if row["id"] == competitor["id"]] == [competitor]


@pytest.mark.parametrize("field,change", [
    ("observed_evidence_ids", lambda ids: ids + ["other-role-evidence"]),
    ("evidence_by_role", lambda mapping: {**mapping, "conflict": ["new-conflict-evidence"]}),
])
def test_complete_receipt_evidence_drift_stales_frozen_proof(
        evidence_fixture, store, tmp_path, monkeypatch, field, change):
    import json
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    proposal = propose(evidence_fixture)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    for entry in proposal["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    revision = proposal["revision_id"]
    path = journal._path("sources", revision)
    receipt = json.loads(path.read_text())
    receipt[field] = change(receipt[field])
    path.write_text(_encoded(receipt))
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "stale-base"
    assert not any(row["id"] in batch["target_ids"] for row in shared_memory.inspect_records())


def test_apply_checks_expected_after_before_writing_witness(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    proposal = propose(evidence_fixture)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    for entry in proposal["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    before = shared_memory.inspect_records()
    tampered = {**batch, "expected_after": {**batch["expected_after"], batch["upserts"][0]["id"]: "0" * 64}}
    with pytest.raises(ValueError, match="topic_expected_after_mismatch"):
        transaction._apply_with_witness_v3(tampered, transaction.topic_commit_record_v3(batch))
    assert shared_memory.inspect_records() == before


def test_crash_before_store_apply_can_retry(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    proposal = propose(evidence_fixture)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    for entry in proposal["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    apply = transaction._apply_with_witness_v3
    monkeypatch.setattr(transaction, "_apply_with_witness_v3",
                        lambda *_: (_ for _ in ()).throw(RuntimeError("before store")))
    with pytest.raises(RuntimeError, match="before store"):
        transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    monkeypatch.setattr(transaction, "_apply_with_witness_v3", apply)
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "committed" and result["newly_committed"]


def test_receipt_transition_crash_recovers_from_atomic_witness(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    proposal = propose(evidence_fixture)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    for entry in proposal["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    transition = journal.transition_topic_v3
    def crash_on_commit(operation_id, **kwargs):
        if kwargs.get("stage") == "committed":
            raise RuntimeError("receipt transition lost")
        return transition(operation_id, **kwargs)
    monkeypatch.setattr(journal, "transition_topic_v3", crash_on_commit)
    with pytest.raises(RuntimeError, match="receipt transition lost"):
        transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    monkeypatch.setattr(journal, "transition_topic_v3", transition)
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "committed" and result["newly_committed"]


def test_prepare_revalidates_exact_task3_proposal_and_followup(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    data, journal, rows = evidence_fixture
    add_target(evidence_fixture)
    followup = validate(data, update_payload(data))
    from reports.wiki_source_notes import materialize_source_note
    for entry in followup["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal,
            now=datetime(2026, 10, 20, tzinfo=timezone.utc), apply=True)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    valid_batch = transaction.prepare_topic_batch_v3(followup, journal=journal, records=rows,
        now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert valid_batch["target_ids"] == ["topic"]
    followup["targets"][0]["original_claims"][0]["text"] = "Forged retained claim."
    with pytest.raises(ValueError):
        transaction.prepare_topic_batch_v3(followup, journal=journal, records=rows,
            now=datetime(2026, 10, 20, tzinfo=timezone.utc))


def test_real_seed_followup_commits_lossless_split_and_witnessed_children(
        evidence_fixture, store, tmp_path, monkeypatch):
    from agent_console import wiki
    from reports import wiki_topic_transaction_v3 as transaction
    from scripts import run_public_wiki_pilot as public
    data, journal, prompt_rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    memory = tmp_path / "memory"
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(memory))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    monkeypatch.setattr(shared_memory, "inspect_records", lambda: shared_memory._read_jsonl_checked(
        shared_memory._paths()["events"]))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(prompt_rows)
    seed = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in seed["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    seed_batch = transaction.prepare_topic_batch_v3(seed, journal=journal, records=prompt_rows, now=now)
    seed_result = transaction.recover_topic_operation_v3(seed_batch["operation_id"], journal=journal, now=now)
    assert seed_result["stage"] == "committed" and seed_result["newly_committed"]

    root_id = seed_batch["target_ids"][0]
    rows = shared_memory.inspect_records()
    data["targets"] = [wiki.semantic_snapshot(root_id, records=rows)]
    update = payload(data)
    target = update["targets"][0]
    target.pop("title")
    target.pop("kind")
    target.pop("surface")
    target.pop("purpose")
    target.update(page_id=root_id, before_hash=data["targets"][0]["revision_hash"])
    current_claim = data["compiled"]["claims"][0]
    target["contributing_revision_ids"] = [data["source"]["revision_id"]]
    target["additions"] = [{"claim_ids": [current_claim["claim_id"]],
        "revision_ids": [data["source"]["revision_id"]], "as_of": current_claim["as_of"],
        "scope": current_claim["scope"], "body": "A [S1] " + ("x" * 15000)}]
    followup = validate(data, update)

    unsplit_bodies = []
    prepare_split = wiki._prepare_split_records
    def capture_split(page, **kwargs):
        unsplit_bodies.append(page["body"])
        return prepare_split(page, **kwargs)
    monkeypatch.setattr(wiki, "_prepare_split_records", capture_split)
    followup_batch = transaction.prepare_topic_batch_v3(followup, journal=journal, records=rows, now=now)
    acquired_lock_sets = []
    target_locks = journal.target_locks
    def capture_locks(ids):
        acquired_lock_sets.append(sorted(set(ids)))
        return target_locks(ids)
    monkeypatch.setattr(journal, "target_locks", capture_locks)
    result = transaction.recover_topic_operation_v3(
        followup_batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "committed" and result["newly_committed"]
    final_rows = shared_memory.inspect_records()
    children = [row for row in followup_batch["upserts"] if row.get("parent_page_id") == root_id]
    assert len(children) > 1
    generated_ids = {row["id"] for row in children}
    assert any(generated_ids <= set(lock_ids) for lock_ids in acquired_lock_sets)
    assert all(lock_ids == sorted(set(lock_ids)) for lock_ids in acquired_lock_sets)
    assert "".join(child["body"] for child in sorted(children, key=lambda row: row["title"])) == unsplit_bodies[-1]
    assert {row["id"] for row in children} <= public.public_target_ids(journal, set(followup_batch["revision_ids"]),
                                                                        records=final_rows)
    witness = transaction.topic_commit_record_v3(followup_batch)
    assert any(row == witness for row in final_rows)


def test_witness_survives_mutable_receipt_loss_and_keeps_effects_pending(
        evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "committed" and result["newly_committed"]
    for revision in batch["source_inputs"]:
        journal._path("sources", revision).unlink()
    monkeypatch.setattr(journal, "load_source", lambda *_: pytest.fail("historical receipt consulted"))
    retry = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert retry["stage"] == "committed" and not retry["newly_committed"]
    assert set(retry["effects"].values()) == {"done"}


def test_crash_after_atomic_store_witness_recovers_without_receipt_authority(
        evidence_fixture, store, tmp_path, monkeypatch):
    from agent_console import wiki
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    apply = transaction._apply_with_witness_v3
    witness = transaction.topic_commit_record_v3(batch)
    def crash_after(batch, record):
        applied = apply(batch, record)
        raise RuntimeError("synthetic process loss after witness")
    monkeypatch.setattr(transaction, "_apply_with_witness_v3", crash_after)
    with pytest.raises(RuntimeError, match="synthetic process loss"):
        transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    monkeypatch.setattr(transaction, "_apply_with_witness_v3", apply)
    monkeypatch.setattr(journal, "load_source", lambda *_: pytest.fail("witness recovery consulted mutable receipt"))
    recovered = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert recovered["stage"] == "committed" and recovered["newly_committed"]


def test_simultaneous_v3_preparers_share_one_frozen_batch(evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    barrier = Barrier(2)
    def prepare():
        barrier.wait()
        return transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = [future.result() for future in [workers.submit(prepare), workers.submit(prepare)]]
    assert first == second


def test_distinct_overlapping_v3_writers_have_one_cas_winner(evidence_fixture, store, tmp_path, monkeypatch):
    from copy import deepcopy
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    proposal_a = propose(evidence_fixture)
    proposal_b = deepcopy(proposal_a)
    proposal_b["targets"][0]["scope_explanation"] += " Additional attribution detail."
    for entry in proposal_a["source_map"].values():
        from reports.wiki_source_notes import materialize_source_note
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    first = transaction.prepare_topic_batch_v3(proposal_a, journal=journal, records=rows, now=now)
    second = transaction.prepare_topic_batch_v3(proposal_b, journal=journal, records=rows, now=now)
    assert first["operation_id"] != second["operation_id"]
    assert first["target_ids"] == second["target_ids"]
    barrier = Barrier(2)
    def recover(batch):
        barrier.wait()
        return transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [future.result() for future in [workers.submit(recover, first), workers.submit(recover, second)]]
    assert sorted(result["stage"] for result in results) == ["committed", "stale-base"]
    assert sum(result["newly_committed"] for result in results) == 1


@pytest.mark.parametrize("drift", ["role", "evidence"])
def test_source_role_or_evidence_drift_stales_unwitnessed_batch(
        evidence_fixture, store, tmp_path, monkeypatch, drift):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    current = journal._path("sources", proposal["revision_id"])
    import json
    receipt = json.loads(current.read_text())
    if drift == "role":
        receipt["evidence_by_role"].pop(proposal["role"])
    else:
        receipt["evidence_by_role"][proposal["role"]] = []
    current.write_text(_encoded(receipt))
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "stale-base"
    assert not any(row["id"] in batch["target_ids"] for row in shared_memory.inspect_records())


def test_split_child_closure_drift_is_rejected_after_locks(tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    parent = {"id": "topic", "tags": ["wiki"]}
    child = {"id": "child", "parent_page_id": "topic", "tags": ["split_from:topic"]}
    reads = iter(([parent], [parent, child]))
    monkeypatch.setattr(transaction, "_rows", lambda: next(reads))
    proposal = {"source_map": {}, "targets": [{"page_id": "topic", "before_hash": "snapshot"}]}
    with pytest.raises(ValueError, match="topic_split_closure_changed"):
        with transaction._locks_v3(proposal, Journal(tmp_path / "journal")):
            pytest.fail("stale split closure entered transaction")


def test_child_added_after_discovery_fails_store_lock_closure_cas(
        evidence_fixture, store, tmp_path, monkeypatch):
    from reports import wiki_topic_transaction_v3 as transaction
    _, journal, rows = evidence_fixture
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    proposal = propose(evidence_fixture)
    from reports.wiki_source_notes import materialize_source_note
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=now, apply=True)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=now)
    real = shared_memory.guarded_batch_upsert
    child = shared_memory.normalize_record({"id": "racing-child", "parent_page_id": batch["target_ids"][0],
                                            "tags": ["split_from:" + batch["target_ids"][0]]})
    def race(**kwargs):
        with shared_memory._events_lock():
            current = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
            shared_memory._write_jsonl_locked([*current, child])
        return real(**kwargs)
    monkeypatch.setattr(shared_memory, "guarded_batch_upsert", race)
    result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=now)
    assert result["stage"] == "stale-base"
    assert not any(row["id"] == batch["target_ids"][0] for row in shared_memory.inspect_records())
