"""Inactive v3 topic transaction boundary with frozen source proof."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime
import base64
import hashlib

from agent_console import shared_memory, wiki
from reports.wiki_source_journal import Journal, _encoded
from reports import wiki_topic_transaction as v2

_CLOSURE_PREFIX = "__topic_closure_v3__:"


def _digest(value):
    return hashlib.sha256(_encoded(value).encode("utf-8")).hexdigest()


def _strict_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_strict_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def _rows():
    return shared_memory._read_jsonl_checked(shared_memory._paths()["events"])


def _child_ids(parent_id, rows):
    parents = {parent_id}
    explicit = {tag.split(":", 1)[1] for row in rows if row.get("id") in parents
                for tag in row.get("tags", []) if tag.startswith("split_into:")}
    return sorted(row["id"] for row in rows if row.get("id") != parent_id and
        (row.get("parent_page_id") == parent_id or row.get("id") in explicit
         or f"split_from:{parent_id}" in row.get("tags", [])))


def _closure_hash(parent_id, rows):
    return _digest(_child_ids(parent_id, rows))


@contextmanager
def _locks_v3(proposal, journal, extra=()):
    revisions = sorted({entry["revision_id"] for entry in proposal["source_map"].values()})
    page_ids = {target["page_id"] for target in proposal["targets"]}
    with ExitStack() as stack:
        for revision in revisions:
            stack.enter_context(journal.source_lock(revision))
        rows = _rows()
        explicit = {tag.split(":", 1)[1] for row in rows if row["id"] in page_ids
                    for tag in row.get("tags", []) if tag.startswith("split_into:")}
        children = {row["id"] for row in rows if row.get("parent_page_id") in page_ids
                    or row["id"] in explicit or any(f"split_from:{pid}" in row.get("tags", []) for pid in page_ids)}
        stack.enter_context(journal.target_locks([*page_ids, *children, *extra]))
        fresh = _rows()
        fresh_explicit = {tag.split(":", 1)[1] for row in fresh if row["id"] in page_ids
                          for tag in row.get("tags", []) if tag.startswith("split_into:")}
        fresh_children = {row["id"] for row in fresh if row.get("parent_page_id") in page_ids
                          or row["id"] in fresh_explicit
                          or any(f"split_from:{pid}" in row.get("tags", []) for pid in page_ids)}
        if fresh_children != children:
            raise ValueError("topic_split_closure_changed")
        yield


def _source_inputs(proposal, journal, now):
    from reports.wiki_source_notes import materialize_source_note, pin_source_note

    revisions = {entry["revision_id"] for entry in proposal["source_map"].values()}
    frozen = {}
    for revision in sorted(revisions):
        receipt = journal.load_source(revision)
        if receipt is None or receipt["compile_state"] != "compiled":
            raise ValueError("topic_source_changed")
        role = proposal["role"] if revision == proposal["revision_id"] else "support"
        selected_role_ids = receipt["evidence_by_role"].get(role, [])
        proposal_observed = (proposal["observed_evidence_ids"]
                             if revision == proposal["revision_id"] else [])
        if not set(proposal_observed) <= set(selected_role_ids):
            raise ValueError("topic_evidence_changed")
        note_preview = materialize_source_note(revision, journal=journal, now=now)
        pin = pin_source_note(note_preview["note_id"], journal=journal, now=now)
        source = {key: receipt["source"].get(key) for key in
                  ("revision_id", "protocol_version", "provider", "canonical_url", "scope",
                   "raw_sha256", "text_sha256")}
        assets = {key: base64.b64encode(pin[key]).decode("ascii") for key in
                  ("note_json_bytes", "note_md_bytes", "manifest_bytes")}
        item = {"revision_id": revision, "role": role,
                "observed_evidence_ids": deepcopy(receipt["observed_evidence_ids"]),
                "selected_role_evidence_ids": deepcopy(selected_role_ids),
                "proposal_observed_evidence_ids": list(proposal_observed),
                "evidence_by_role": deepcopy(receipt["evidence_by_role"]), "source": source,
                "note_id": pin["note_id"], "note_hashes": {key: pin[key] for key in
                    ("manifest_sha256", "raw_sha256", "text_sha256", "compile_sha256",
                     "note_json_sha256", "note_md_sha256")}, "note_assets": assets}
        item["input_sha256"] = _digest(item)
        frozen[revision] = item
    return frozen


def topic_commit_record_v3(batch: dict) -> dict:
    return shared_memory.normalize_record({"id": "topic-commit-v3:" + batch["operation_id"],
        "title": "Topic transaction v3 commit", "tags": ["topic-commit", "topic-protocol:3"],
        "createdAt": batch["prepared_at"], "updatedAt": batch["prepared_at"],
        "topic_protocol_version": 3, "topic_operation_id": batch["operation_id"],
        "topic_batch_sha256": _digest(batch), "source": {"surface": "internal",
        "provider": "topic-transaction-v3", "writer": "topic-transaction-v3"}})


def _freeze(proposal, *, journal, records, now, prepared_at):
    from reports.wiki_topic_snapshot_context import revalidate_snapshot_context
    from reports.wiki_topic_v3 import (validate_topic_integration_v3, verified_current_v3,
                                       verified_targets_v3)
    from reports.wiki_topic_context import _verified_source

    try:
        fixed_time = datetime.fromisoformat(prepared_at)
        if fixed_time.tzinfo is None or now.tzinfo is None or fixed_time > now:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise ValueError("topic_prepared_time_invalid") from exc
    if type(proposal.get("protocol_version")) is not int or proposal["protocol_version"] != 3:
        raise ValueError("topic_protocol_invalid")
    revalidate_snapshot_context(proposal["context"], journal=journal, now=now)
    source, compiled, _ = _verified_source(journal, proposal["revision_id"], now)
    source = {**source,
              "role": proposal["role"], "observed_evidence_ids": proposal["observed_evidence_ids"]}
    verified_current_v3(compiled, source, journal=journal, now=now)
    proposed_targets = [t for t in proposal["targets"] if t.get("before_hash") is not None]
    present_ids = {row["id"] for row in records}
    if any(target["before_hash"] is None and target["page_id"] in present_ids
           for target in proposal["targets"]):
        raise ValueError("topic_new_target_collision")
    snapshots = [wiki.semantic_snapshot(t["page_id"], records=records) for t in proposed_targets]
    targets = verified_targets_v3(snapshots, journal=journal, records=records, now=now)
    payload_targets = []
    for target in proposal["targets"]:
        keys = ("page_id", "before_hash", "contributing_revision_ids", "scope_explanation", "additions", "relations")
        model_target = {key: deepcopy(target[key]) for key in keys}
        if target.get("before_hash") is None:
            model_target["page_id"] = None
            model_target.update({key: deepcopy(target[key]) for key in ("title", "kind", "surface", "purpose")})
        payload_targets.append(model_target)
    checked = validate_topic_integration_v3({"targets": payload_targets, "refusal": proposal["refusal"]},
        compiled=compiled, source=source, context=proposal["context"], targets=targets)
    if not _strict_equal(checked, proposal):
        raise ValueError("topic_proposal_changed")
    source_inputs = _source_inputs(proposal, journal, now)
    # V3 note dependencies are immutable assets, not editorial page links.
    build_proposal = {**proposal, "dependencies": []}
    upserts = v2._build(build_proposal, snapshots, records=records, journal=journal, now=fixed_time,
                        operation_id=_digest(proposal))
    write_ids = {row["id"] for row in upserts}
    before = {pid: wiki._source_fingerprint(pid, records) for pid in sorted(write_ids)}
    before.update({_CLOSURE_PREFIX + target["page_id"]: _closure_hash(target["page_id"], records)
                   for target in proposal["targets"]})
    projected = [row for row in records if row["id"] not in write_ids] + upserts
    after = {pid: wiki._source_fingerprint(pid, projected) for pid in sorted(write_ids)}
    return {"protocol_version": 3, "operation_id": _digest(proposal), "proposal": deepcopy(proposal),
        "revision_ids": sorted(source_inputs), "source_map": deepcopy(proposal["source_map"]),
        "dependencies": deepcopy(proposal["dependencies"]), "source_inputs": source_inputs,
        "public_proof": {"export_metadata": {"protocol_version": 3,
            "source_note_ids": sorted(item["note_id"] for item in source_inputs.values()),
            "target_ids": [target["page_id"] for target in proposal["targets"]]},
            "source_assets": {revision: {key: deepcopy(item[key]) for key in
                ("note_id", "note_hashes", "note_assets")} for revision, item in source_inputs.items()}},
        "original_claims": {t["page_id"]: deepcopy(t["original_claims"]) for t in proposal["targets"]},
        "target_ids": [t["page_id"] for t in proposal["targets"]], "expected_before": before,
        "expected_after": after, "upserts": upserts,
        "snapshots": [deepcopy(r) for r in records if r["id"] in write_ids], "prepared_at": prepared_at}


def prepare_topic_batch_v3(proposal: dict, *, journal: Journal, records: list[dict], now: datetime) -> dict:
    proposal = deepcopy(proposal)
    if type(proposal.get("protocol_version")) is not int or proposal["protocol_version"] != 3:
        raise ValueError("topic_protocol_invalid")
    operation_id = _digest(proposal)
    with journal.target_locks(["topic-operation-v3:" + operation_id]):
        existing = journal.load_topic_operation_v3(operation_id)
        if existing:
            if not _strict_equal(existing["batch"]["proposal"], proposal):
                raise ValueError("operation_conflict")
            return existing["batch"]
        try:
            with _locks_v3(proposal, journal):
                fresh = _rows()
                batch = _freeze(proposal, journal=journal, records=fresh, now=now, prepared_at=now.isoformat())
                journal.prepare_topic_v3(batch)
                return batch
        except ValueError as exc:
            if str(exc) != "topic_split_closure_changed":
                raise
            raise ValueError("topic_split_closure_changed") from exc


def _apply_with_witness_v3(batch, witness):
    projected = [row for row in _rows() if row["id"] not in {item["id"] for item in batch["upserts"]}]
    projected.extend(shared_memory.normalize_record(row) for row in batch["upserts"])
    for page_id, expected in batch["expected_after"].items():
        if wiki._source_fingerprint(page_id, projected) != expected:
            raise ValueError("topic_expected_after_mismatch")
    commit = deepcopy(batch)
    commit["upserts"].append(witness)
    commit["expected_before"][witness["id"]] = None
    commit["expected_after"][witness["id"]] = wiki._source_fingerprint(witness["id"], [witness])

    def fingerprint(page_id, rows):
        if page_id.startswith(_CLOSURE_PREFIX):
            return _closure_hash(page_id[len(_CLOSURE_PREFIX):], rows)
        return wiki._source_fingerprint(page_id, rows)

    for row in commit["snapshots"]:
        wiki._snapshot_page_revision(row)
    result = shared_memory.guarded_batch_upsert(upserts=commit["upserts"], expected=commit["expected_before"],
        fingerprint=fingerprint, preserve_fields=("useCount", "lastUsedAt", "lastQuery"))
    wiki._CACHE.clear()
    return result


def recover_topic_operation_v3(operation_id: str, *, journal: Journal, now: datetime) -> dict:
    with journal.target_locks(["topic-operation-v3:" + operation_id]):
        receipt = journal.load_topic_operation_v3(operation_id)
        if receipt is None:
            raise ValueError("topic_operation_unavailable")
        batch = receipt["batch"]
        witness = topic_commit_record_v3(batch)
        if receipt["stage"] == "committed":
            rows = _rows()
            if not _strict_equal([r for r in rows if r["id"] == witness["id"]], [witness]):
                raise ValueError("topic_commit_witness_mismatch")
            return _committed_effects(receipt, journal, newly_committed=False)
        if receipt["stage"] not in {"prepared", "applied"}:
            return {"operation_id": operation_id, "stage": receipt["stage"], "effects": {},
                    "reason": receipt.get("error_code") or "topic_operation_not_committable",
                    "newly_committed": False}
        with _locks_v3(batch["proposal"], journal, [*batch["expected_before"], witness["id"]]):
            rows = _rows()
            stored_witnesses = [r for r in rows if r["id"] == witness["id"]]
            if stored_witnesses:
                if not _strict_equal(stored_witnesses, [witness]):
                    raise ValueError("topic_commit_witness_mismatch")
                receipt = journal.transition_topic_v3(operation_id, stage="committed")
                return _committed_effects(receipt, journal, newly_committed=True)
            try:
                rebuilt = _freeze(batch["proposal"], journal=journal, records=rows, now=now,
                                  prepared_at=batch["prepared_at"])
            except (ValueError, OSError, KeyError, TypeError, UnicodeError):
                journal.transition_topic_v3(operation_id, stage="stale-base", error_code="topic_dependency_changed")
                return {"operation_id": operation_id, "stage": "stale-base", "effects": {},
                        "reason": "topic_dependency_changed", "newly_committed": False}
            if not _strict_equal(rebuilt, batch):
                journal.transition_topic_v3(operation_id, stage="stale-base", error_code="topic_frozen_batch_changed")
                return {"operation_id": operation_id, "stage": "stale-base", "effects": {},
                        "reason": "topic_frozen_batch_changed", "newly_committed": False}
            result = _apply_with_witness_v3(batch, witness)
            if result["status"] != "applied":
                journal.transition_topic_v3(operation_id, stage="stale-base", error_code="topic_target_changed")
                return {"operation_id": operation_id, "stage": "stale-base", "effects": {},
                        "reason": "topic_target_changed", "newly_committed": False}
            receipt = journal.transition_topic_v3(operation_id, stage="committed")
        return _committed_effects(receipt, journal, newly_committed=True)


def _committed_effects(receipt, journal, *, newly_committed):
    from reports.wiki_publication_effects import dispatch_effects
    receipt = dispatch_effects(receipt, journal)
    return {"operation_id": receipt["operation_id"], "stage": "committed",
            "effects": {key: receipt[key] for key in ("artifacts", "search", "log")},
            "reason": receipt["error_code"], "newly_committed": newly_committed}
