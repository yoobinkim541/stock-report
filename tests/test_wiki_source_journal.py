"""Durable receipts must survive interruptions without claiming false completion."""
import importlib
import json
import multiprocessing
import time
from pathlib import Path

import pytest

import safe_io

REV = "a" * 64
OP = "b" * 64


def journal(root):
    return importlib.import_module("reports.wiki_source_journal").Journal(root)


def source(evidence="observed-one", role="support"):
    return {"revision_id": REV, "body": "original text", "provider": "official",
            "observed_evidence_ids": [evidence], "role": role}


def compilation(body="Synthesis " * 2000):
    return {"revision_id": REV, "body": body, "claims": [{"claim_id": "c1"}],
            "terms": ["capacity"], "limitations": [], "skip_reason": None}


def batch():
    return {"operation_id": OP, "revision_id": REV,
            "expected_before": {"page": None}, "expected_after": {"page": "c" * 64},
            "upserts": [{"id": "page", "body": "new draft"}],
            "snapshots": [], "target_ids": ["page"]}


def test_compiled_requires_durable_full_artifact(tmp_path):
    store = journal(tmp_path)
    compiled = compilation()
    receipt = store.save_compilation(source(), compiled)
    assert receipt["compile_state"] == "compiled"
    assert json.loads(Path(receipt["compile_ref"]).read_text())["body"] == compiled["body"]
    assert "body" not in receipt["source"]
    assert store.load_source(REV)["compile_sha256"] == receipt["compile_sha256"]


@pytest.mark.parametrize("boundary", ["artifact", "receipt"])
def test_interrupted_compilation_is_not_complete(tmp_path, monkeypatch, boundary):
    store = journal(tmp_path)
    original_json = safe_io.atomic_write_json
    original_text = safe_io.atomic_write_text

    def fail_json(path, obj, **kwargs):
        if boundary == "receipt" and Path(path).parent.name == "sources":
            raise OSError("interrupted before receipt rename")
        return original_json(path, obj, **kwargs)

    def fail_text(path, text):
        if boundary == "artifact" and Path(path).parent.name == "compiled":
            raise OSError("interrupted before artifact rename")
        return original_text(path, text)

    monkeypatch.setattr(safe_io, "atomic_write_json", fail_json)
    monkeypatch.setattr(safe_io, "atomic_write_text", fail_text)
    with pytest.raises(OSError):
        store.save_compilation(source(), compilation())
    receipt = store.load_source(REV)
    assert receipt is None or receipt["compile_state"] != "compiled"
    monkeypatch.setattr(safe_io, "atomic_write_json", original_json)
    monkeypatch.setattr(safe_io, "atomic_write_text", original_text)
    assert store.save_compilation(source(), compilation())["compile_state"] == "compiled"


def test_corrupt_receipt_fails_closed(tmp_path):
    store = journal(tmp_path)
    store.save_compilation(source(), compilation())
    (tmp_path / "sources" / f"{REV}.json").write_text("{corrupt")
    with pytest.raises(ValueError, match="journal_corrupt"):
        store.load_source(REV)


def test_corrupt_compile_artifact_is_not_reused(tmp_path):
    store = journal(tmp_path)
    receipt = store.save_compilation(source(), compilation())
    Path(receipt["compile_ref"]).write_text("changed artifact")
    with pytest.raises(ValueError, match="journal_corrupt"):
        store.load_source(REV)


def test_redelivery_preserves_artifact_and_unions_actual_ids(tmp_path):
    store = journal(tmp_path)
    first = store.save_compilation(source(), compilation())
    again = store.save_compilation(source("observed-two", "conflict"), compilation())
    assert again["compile_ref"] == first["compile_ref"]
    assert again["observed_evidence_ids"] == ["observed-one", "observed-two"]
    assert again["evidence_by_role"] == {"support": ["observed-one"], "conflict": ["observed-two"]}


def test_skip_and_failed_attempt_have_durable_reasons(tmp_path):
    store = journal(tmp_path)
    receipt = store.save_compilation(source(), {**compilation(""), "claims": [], "skip_reason": "No reusable knowledge"})
    assert receipt["compile_state"] == "skipped" and receipt["skip_reason"] == "No reusable knowledge"
    store.record_attempt({**source(), "revision_id": "d" * 64}, state="failed", error_code="invalid_model_output")
    assert store.load_source("d" * 64)["compile_state"] == "failed"


def test_roles_have_independent_routing_backlogs(tmp_path):
    store = journal(tmp_path)
    store.save_compilation(source(), compilation())
    store.save_routing(REV, "support", {"cursor": 20, "unexamined": ["other"]})
    store.save_routing(REV, "conflict", {"cursor": 0, "unexamined": ["page"]})
    assert store.load_source(REV)["routing"]["support"]["cursor"] == 20
    assert store.load_source(REV)["routing"]["conflict"]["cursor"] == 0


def test_prepared_data_and_side_effects_have_distinct_completion(tmp_path):
    store = journal(tmp_path)
    prepared = store.prepare(batch())
    assert prepared["stage"] == "prepared"
    assert store.prepare(batch()) == prepared
    applied = store.transition(OP, stage="applied")
    assert applied["artifacts"] == "pending"
    store.transition(OP, stage="committed", effects={"artifacts": "failed"})
    assert store.pending_operations()[0]["stage"] == "committed"
    store.transition(OP, effects={"artifacts": "done", "search": "done", "log": "done"})
    assert store.pending_operations() == []
    with pytest.raises(ValueError, match="transition_invalid"):
        store.transition(OP, stage="prepared")


def test_same_operation_id_cannot_replace_prepared_batch(tmp_path):
    store = journal(tmp_path)
    store.prepare(batch())
    with pytest.raises(ValueError, match="operation_conflict"):
        store.prepare({**batch(), "target_ids": ["different"]})


@pytest.mark.parametrize("identity", ["../private", "", "a" * 63, "z" * 64])
def test_invalid_ids_cannot_escape_journal(tmp_path, identity):
    with pytest.raises(ValueError, match="identity_invalid"):
        journal(tmp_path).load_source(identity)


def _acquire_and_append(root, marker, label):
    with journal(Path(root)).source_lock(REV):
        with open(marker, "a") as stream:
            stream.write(label + "\n")


def test_source_lock_serializes_processes(tmp_path):
    store = journal(tmp_path / "journal")
    marker = tmp_path / "order.txt"
    ctx = multiprocessing.get_context("fork")
    with store.source_lock(REV):
        process = ctx.Process(target=_acquire_and_append, args=(str(store.root), str(marker), "worker"))
        process.start()
        time.sleep(0.1)
        assert not marker.exists()
    process.join(timeout=5)
    assert process.exitcode == 0
    assert marker.read_text() == "worker\n"


@pytest.mark.parametrize("boundary", ["artifact", "receipt"])
def test_crash_after_rename_is_recoverable(tmp_path, monkeypatch, boundary):
    store = journal(tmp_path)
    original_json, original_text = safe_io.atomic_write_json, safe_io.atomic_write_text

    def crash_json(path, obj, **kwargs):
        original_json(path, obj, **kwargs)
        if boundary == "receipt" and Path(path).parent.name == "sources":
            raise OSError("crash after receipt rename")

    def crash_text(path, text):
        original_text(path, text)
        if boundary == "artifact" and Path(path).parent.name == "compiled":
            raise OSError("crash after artifact rename")

    monkeypatch.setattr(safe_io, "atomic_write_json", crash_json)
    monkeypatch.setattr(safe_io, "atomic_write_text", crash_text)
    with pytest.raises(OSError):
        store.save_compilation(source(), compilation())
    monkeypatch.setattr(safe_io, "atomic_write_json", original_json)
    monkeypatch.setattr(safe_io, "atomic_write_text", original_text)
    receipt = store.save_compilation(source(), compilation())
    assert store.load_compilation(receipt)["body"] == "Synthesis " * 2000
    assert len(list((tmp_path / "compiled").glob("*.json"))) == 1


def test_target_locks_have_deterministic_order_and_no_source_reentrance(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import hashlib
    real = safe_io.file_write_lock
    observed = []

    @contextmanager
    def record(path, **kwargs):
        observed.append(Path(path).stem)
        with real(path, **kwargs):
            yield

    monkeypatch.setattr(safe_io, "file_write_lock", record)
    store = journal(tmp_path)
    with store.source_lock(REV):
        with store.target_locks(["z", "a", "z"]):
            store.save_compilation(source(), compilation())
    assert observed[1:3] == [hashlib.sha256(value.encode()).hexdigest() for value in ("a", "z")]
    assert observed.count(REV) == 2  # worker lock and distinct receipt lock


def test_structurally_corrupt_receipt_fails_closed(tmp_path):
    store = journal(tmp_path)
    store.save_compilation(source(), compilation())
    path = tmp_path / "sources" / f"{REV}.json"
    malformed = json.loads(path.read_text())
    malformed["evidence_by_role"] = []
    path.write_text(json.dumps(malformed))
    with pytest.raises(ValueError, match="journal_corrupt"):
        store.load_source(REV)
