"""Fail-closed, same-path pilot authorization. No operational initializer.

The separately approved operator must provision the retained approval/history,
canonical state, shared budget and coordination sidecars. Inspection opens those
sidecars without creating or truncating them. Lock order is automatic-run,
authorization index, canonical state; callers keep these through execution.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import stat
import time
import uuid

from agent_console import shared_memory, wiki
from reports import wiki_public_budget as quota
from reports.wiki_source_notes import (
    _bytes, _directory, _object, _read_file, _relative_read, _rename_noreplace,
    _sha, _subdirectory, _write, pin_source_note,
)
from reports.wiki_source_journal import _identity


def _now():
    return datetime.now(timezone.utc)


def _revisions():
    from scripts.run_public_topic_pilot import APPROVED_REVISIONS
    return list(APPROVED_REVISIONS)


def _require(condition, reason="pilot_history_unverified"):
    if not condition:
        raise ValueError(reason)


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _read(journal, relative):
    with _directory(journal.root) as root:
        return _relative_read(root, Path(relative))


def _list(journal, relative):
    with _directory(journal.root) as root:
        try:
            with _subdirectory(root, Path(relative).parts) as directory:
                return sorted(os.listdir(directory))
        except FileNotFoundError:
            return []


def _atomic(journal, relative, blob):
    """Descriptor-relative atomic replacement, with a durable directory rename."""
    relative = Path(relative)
    _require(not relative.is_absolute() and ".." not in relative.parts)
    with _directory(journal.root) as root:
        with _subdirectory(root, relative.parts[:-1], create=True) as parent:
            temporary = "." + relative.name + "." + uuid.uuid4().hex + ".tmp"
            _write(parent, temporary, blob)
            try:
                os.replace(temporary, relative.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                os.fsync(root)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass


@contextmanager
def pilot_locks(journal):
    with _directory(journal.root) as root, ExitStack() as stack:
        for name in ("automatic-run.lock", "topic-pilot-authorizations.lock", "topic-pilot.json.lock"):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
            deadline = time.monotonic() + 30
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    _require(time.monotonic() < deadline, "pilot_lock_timeout")
                    time.sleep(0.05)
        yield


def authorization_identity(approval):
    _require(type(approval) is dict and set(approval) == {
        "schema_version", "approval_ref", "revision_ids", "max_attempts", "daily_limit", "decision"})
    _require(type(approval["schema_version"]) is int and approval["schema_version"] == 1)
    _require(type(approval["approval_ref"]) is str and bool(approval["approval_ref"].strip()))
    _require(type(approval["revision_ids"]) is list and approval["revision_ids"] == _revisions())
    for key, expected in (("max_attempts", 2), ("daily_limit", 20)):
        _require(type(approval[key]) is int and approval[key] == expected)
    _require(approval["decision"] == "approved")
    return _sha(_bytes(approval))


def _guard(journal):
    names = _list(journal, "topic-pilot-authorizations")
    _require(len(names) == 1 and names[0].endswith(".json"), "pilot_authorization_ambiguous")
    path = "topic-pilot-authorizations/" + names[0]
    guard = _object(_read(journal, path))
    _require(set(guard) == {"schema_version", "authorization_id", "approval", "state_path",
                           "state_sha256", "history_proofs", "lifecycle", "attempts"})
    _require(type(guard["schema_version"]) is int and guard["schema_version"] == 1)
    identity = authorization_identity(guard["approval"])
    _require(guard["authorization_id"] == identity and names[0] == identity + ".json")
    _require(guard["state_path"] == str(journal.root / "topic-pilot.json"), "pilot_state_relocated")
    _identity(guard["state_sha256"])
    _require(guard["lifecycle"] in {"initialized", "migrating", "active", "needs_review", "exhausted"})
    _require(type(guard["history_proofs"]) is dict and bool(guard["history_proofs"]))
    for path, digest in guard["history_proofs"].items():
        _identity(digest)
        _require(type(path) is str and Path(path).parts[0] == "topic-pilot-history")
        _require(_sha(_read(journal, path)) == digest, "pilot_history_proof_changed")
    _require(type(guard["attempts"]) is list)
    return guard


def _validate_state(state, guard):
    version = state.get("schema_version")
    _require(type(version) is int and version in {1, 2}, "pilot_state_invalid")
    common = {"schema_version", "baseline", "attempts", "topic_ids"}
    if version == 1:
        _require(set(state) == common | {"revisions"} and state["revisions"] == _revisions())
    else:
        _require(set(state) == common | {"authorization_id", "context_mode", "revision_ids", "note_ids"})
        _require(state["authorization_id"] == guard["authorization_id"]
                 and state["context_mode"] == "source_note_snapshot"
                 and state["revision_ids"] == _revisions())
        _require(type(state["note_ids"]) is dict and set(state["note_ids"]) == set(_revisions()))
        for note_id in state["note_ids"].values():
            _identity(note_id)
        _require(len(set(state["note_ids"].values())) == 3)
    _require(type(state["attempts"]) is list and len(state["attempts"]) <= 2)
    _require(type(state["topic_ids"]) is list and len(state["topic_ids"]) <= 1
             and all(type(p) is str and bool(p) for p in state["topic_ids"]))
    _require(state["baseline"] is None or type(state["baseline"]) is list)
    seen = set()
    for index, attempt in enumerate(state["attempts"]):
        fields = {"stage", "revision_id", "intent_id", "reservation", "status"}
        _require(type(attempt) is dict and fields <= set(attempt)
                 and set(attempt) <= fields | {"operation_id", "protocol_version"})
        _require(attempt["stage"] == ("seed" if index == 0 else "followup")
                 and attempt["revision_id"] == _revisions()[index + 1]
                 and attempt["status"] in {"interrupted", "refused", "failed", "committed"})
        _identity(attempt["intent_id"])
        _require(attempt["intent_id"] not in seen)
        seen.add(attempt["intent_id"])
        _require(not index or state["attempts"][index - 1]["status"] == "committed")
        if "operation_id" in attempt:
            _identity(attempt["operation_id"])
            protocol = attempt.get("protocol_version")
            _require(type(protocol) is int and protocol in {2, 3})
            _require(protocol == (2 if version == 1 else 3))
        else:
            _require("protocol_version" not in attempt)
        if attempt["status"] == "committed":
            _require("operation_id" in attempt and attempt["reservation"] is not None
                     and bool(state["baseline"]) and len(state["topic_ids"]) == 1)
    _require(not state["topic_ids"] or (bool(state["attempts"])
             and state["attempts"][0]["status"] == "committed"))
    _require(_same(guard["attempts"], state["attempts"]), "pilot_attempt_guard_changed")


def _history(journal, state, guard):
    """Reconcile IDs, not counts: intents -> reservations -> versioned operations/witnesses."""
    budget_blob = _read(journal, "automatic-budget.json")  # Missing is unknown, never quota._read's zero.
    _object(budget_blob)
    budget = quota._read(journal.root / "automatic-budget.json")
    reservations = [r for day in budget["days"].values() for r in day]
    scoped = {r["id"]: r for r in reservations if r["revision_id"] in _revisions()}
    attempts = state["attempts"]
    intent_names = _list(journal, "topic-pilot-intents")
    _require(set(intent_names) == {a["intent_id"] + ".json" for a in attempts}, "pilot_intents_unaccounted")
    used, operations = set(), {}
    for attempt in attempts:
        intent = _object(_read(journal, "topic-pilot-intents/" + attempt["intent_id"] + ".json"))
        _require(_same(intent, {"authorization_id": guard["authorization_id"], "attempt": attempt}))
        reservation = attempt["reservation"]
        if reservation is not None:
            _require(type(reservation) is dict and set(reservation) == {"id", "revision_id", "at"})
            _require(reservation["id"] not in used and scoped.get(reservation["id"]) == reservation
                     and reservation["revision_id"] == attempt["revision_id"])
            used.add(reservation["id"])
        else:
            _require(attempt["status"] == "interrupted" and "operation_id" not in attempt)
        if "operation_id" in attempt:
            key = (attempt["protocol_version"], attempt["operation_id"])
            _require(key not in operations)
            operations[key] = attempt
    _require(used == set(scoped), "pilot_reservations_unaccounted")
    from reports.wiki_topic_transaction import _commit_record
    from reports.wiki_topic_transaction_v3 import topic_commit_record_v3
    rows = shared_memory.inspect_records()
    witnesses = [r for r in rows if "topic-commit" in r.get("tags", [])
                 or r.get("id", "").startswith(("topic-commit:", "topic-commit-v3:"))]
    accounted, found = [], set()
    latest = None
    for version, directory, load, witness_fn in (
        (2, "topic-operations", journal.load_topic_operation, _commit_record),
        (3, "topic-operations-v3", journal.load_topic_operation_v3, topic_commit_record_v3),
    ):
        for name in _list(journal, directory):
            if name.endswith(".lock"):
                continue
            _require(name.endswith(".json"))
            identity = _identity(name[:-5])
            raw = _object(_read(journal, directory + "/" + name))
            _require(type(raw.get("schema_version")) is int and raw["schema_version"] == version)
            receipt = load(identity)
            _require(receipt is not None and receipt["operation_id"] == identity)
            batch = receipt["batch"]
            _require(type(batch["protocol_version"]) is int and batch["protocol_version"] == version)
            key = (version, identity)
            expected = witness_fn(batch)
            matching = [w for w in witnesses if w.get("id") == expected["id"]]
            if matching:
                _require(_same(matching, [expected]), "pilot_commit_unverified")
                accounted.extend(matching)
            if batch["proposal"]["revision_id"] not in _revisions():
                continue
            _require(key in operations, "pilot_operations_unaccounted")
            found.add(key)
            attempt = operations[key]
            _require(batch["proposal"]["revision_id"] == attempt["revision_id"]
                     and batch["proposal"]["role"] == "support")
            _require(set(batch["revision_ids"]) <= set(_revisions()))
            if attempt["status"] == "committed":
                _require(receipt["stage"] == "committed" and len(matching) == 1
                         and batch["target_ids"] == state["topic_ids"]
                         and all(receipt[k] == "done" for k in ("artifacts", "search", "log")),
                         "pilot_commit_unverified")
                if attempt is attempts[-1]:
                    latest = batch
            else:
                _require(not matching and receipt["stage"] != "committed", "pilot_interrupted_commit")
    _require(found == set(operations) and _same(sorted(accounted, key=lambda r: r["id"]),
             sorted(witnesses, key=lambda r: r["id"])), "pilot_witnesses_unaccounted")
    if latest:
        _require(all(wiki._source_fingerprint(pid, rows) == value for pid, value in latest["expected_after"].items()),
                 "pilot_topic_changed")
    # Source operations are not model attempts, but unreadable retained history is not evidence of zero.
    for name in _list(journal, "operations"):
        if not name.endswith(".lock"):
            _require(name.endswith(".json"))
            receipt = _object(_read(journal, "operations/" + name))
            _require(type(receipt.get("schema_version")) is int and receipt["schema_version"] == 1)


def _migration_record(journal, identity):
    prefix = "topic-pilot-migrations/" + identity
    names = _list(journal, prefix)
    if not names:
        return None
    _require(set(names) == {"old-state.bin", "intent.json"})
    intent = _object(_read(journal, prefix + "/intent.json"))
    _require(set(intent) == {"schema_version", "authorization_id", "state_path", "before_sha256",
                             "after_sha256", "after_state"})
    _require(type(intent["schema_version"]) is int and intent["schema_version"] == 1
             and intent["authorization_id"] == identity
             and intent["state_path"] == str(journal.root / "topic-pilot.json"))
    old = _read(journal, prefix + "/old-state.bin")
    _require(_sha(old) == intent["before_sha256"] and _sha(_bytes(intent["after_state"])) == intent["after_sha256"])
    return old, intent


def _load_locked(journal, *, migration=False):
    guard = _guard(journal)
    blob = _read(journal, "topic-pilot.json")
    state = _object(blob)
    _validate_state(state, guard)
    records = _list(journal, "topic-pilot-migrations")
    allowed = {guard["authorization_id"], "." + guard["authorization_id"] + ".staging"}
    _require(set(records) <= allowed, "pilot_migrations_ambiguous")
    record = _migration_record(journal, guard["authorization_id"])
    _require(guard["lifecycle"] not in {"needs_review"}, "pilot_authorization_needs_review")
    if migration and record and not state["attempts"]:
        _, intent = record
        _require(_sha(blob) in {intent["before_sha256"], intent["after_sha256"]}
                 and guard["state_sha256"] in {intent["before_sha256"], intent["after_sha256"]})
    else:
        _require(_sha(blob) == guard["state_sha256"], "pilot_state_hash_changed")
        _require(guard["lifecycle"] != "migrating", "pilot_migration_pending")
        if state["schema_version"] == 2:
            _require(record is not None and guard["lifecycle"] in {"active", "exhausted"})
        else:
            _require(record is None and guard["lifecycle"] in {"initialized", "exhausted"})
    _history(journal, state, guard)
    return state, guard, blob


def _report(state, guard):
    attempts = state["attempts"]
    status = "ready"
    if attempts and attempts[-1]["status"] != "committed":
        status = attempts[-1]["status"]
    elif len(attempts) == 2:
        status = "awaiting_semantic_review"
    return {"status": status, "attempts_used": len(attempts), "authorization_id": guard["authorization_id"],
            "state": deepcopy(state)}


def _review(exc):
    return {"status": "needs_review", "reason": str(exc), "attempts_used": None}


_HISTORY_ERRORS = (OSError, ValueError, KeyError, TypeError, UnicodeError, IndexError)


def inspect_pilot_authorization(*, journal):
    """Read-only authorization/history inspection; never manufactures an empty state."""
    try:
        with pilot_locks(journal):
            state, guard, _ = _load_locked(journal)
            return _report(state, guard)
    except _HISTORY_ERRORS as exc:
        return _review(exc)


def _existing_pins(journal):
    pins = {}
    for name in _list(journal, "source-notes"):
        if name.startswith("."):
            continue
        _identity(name)
        pin = pin_source_note(name, journal=journal, now=_now())
        if pin["revision_id"] in _revisions():
            _require(pin["revision_id"] not in pins, "pilot_source_note_ambiguous")
            pins[pin["revision_id"]] = name
    _require(set(pins) == set(_revisions()), "pilot_source_note_unavailable")
    return pins


def _publish_record(journal, identity, old, intent):
    with _directory(journal.root) as root:
        with _subdirectory(root, ("topic-pilot-migrations",), create=True) as parent:
            staging = "." + identity + ".staging"
            with _subdirectory(parent, (staging,), create=True) as directory:
                for name, blob in (("old-state.bin", old), ("intent.json", _bytes(intent))):
                    try:
                        _require(_read_file(directory, name) == blob)
                    except FileNotFoundError:
                        _write(directory, name, blob)
                _require(set(os.listdir(directory)) == {"old-state.bin", "intent.json"})
                os.fsync(directory)
            os.fsync(parent)
            os.fsync(root)
            _rename_noreplace(parent, staging, identity)
            os.fsync(parent)


def migrate_empty_pilot_state(*, journal, expected_sha256, authorization_id, apply=False):
    """Explicit CAS migration only. Retained bytes/intent permit crash reconciliation."""
    try:
        _identity(expected_sha256)
        _identity(authorization_id)
        _require(type(apply) is bool)
        with pilot_locks(journal):
            state, guard, blob = _load_locked(journal, migration=True)
            _require(authorization_id == guard["authorization_id"])
            _require(not state["attempts"] and not state["topic_ids"] and state["baseline"] is None,
                     "pilot_migration_requires_empty_state")
            record = _migration_record(journal, authorization_id)
            if record:
                old, intent = record
                _require(expected_sha256 == intent["before_sha256"])
                _require(_object(old) == {"schema_version": 1, "revisions": _revisions(),
                         "baseline": None, "attempts": [], "topic_ids": []})
                target = intent["after_state"]
                _validate_state(target, guard)
                _require(target["note_ids"] == _existing_pins(journal))
            else:
                _require(state["schema_version"] == 1 and _sha(blob) == expected_sha256)
                old = blob
                target = {"schema_version": 2, "authorization_id": authorization_id,
                          "context_mode": "source_note_snapshot", "revision_ids": _revisions(),
                          "note_ids": _existing_pins(journal), "baseline": None, "attempts": [], "topic_ids": []}
                intent = {"schema_version": 1, "authorization_id": authorization_id,
                          "state_path": str(journal.root / "topic-pilot.json"), "before_sha256": expected_sha256,
                          "after_sha256": _sha(_bytes(target)), "after_state": target}
            if not apply:
                return {"status": "migration_ready", "state": target, "authorization_id": authorization_id}
            if record is None:
                _publish_record(journal, authorization_id, old, intent)
            guard_path = "topic-pilot-authorizations/" + authorization_id + ".json"
            if _sha(blob) == intent["before_sha256"]:
                guard.update(lifecycle="migrating")
                _atomic(journal, guard_path, _bytes(guard))
                _require(_read(journal, "topic-pilot.json") == blob, "pilot_migration_cas_failed")
                _atomic(journal, "topic-pilot.json", _bytes(target))
            guard.update(state_sha256=intent["after_sha256"], lifecycle="active")
            _atomic(journal, guard_path, _bytes(guard))
            return _report(target, guard)
    except _HISTORY_ERRORS as exc:
        return _review(exc)


def persist_pilot_state(journal, state, guard):
    """Under pilot_locks: intent and external consumption precede replaceable state.

    An interrupted multi-file update deliberately needs review. Neither a lost
    state nor a reservation/write gap can reopen an attempt or repeat a model.
    """
    _require(_sha(_read(journal, "topic-pilot.json")) == guard["state_sha256"], "pilot_state_cas_failed")
    for attempt in state["attempts"]:
        _atomic(journal, "topic-pilot-intents/" + attempt["intent_id"] + ".json",
                _bytes({"authorization_id": guard["authorization_id"], "attempt": attempt}))
    guard.update(attempts=deepcopy(state["attempts"]), state_sha256=_sha(_bytes(state)))
    if len(state["attempts"]) == 2 or (state["attempts"] and state["attempts"][-1]["status"] != "committed"):
        guard["lifecycle"] = "exhausted"
    else:
        guard["lifecycle"] = "active" if state["schema_version"] == 2 else "initialized"
    _validate_state(state, guard)
    _atomic(journal, "topic-pilot-authorizations/" + guard["authorization_id"] + ".json", _bytes(guard))
    _atomic(journal, "topic-pilot.json", _bytes(state))
