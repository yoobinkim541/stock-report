"""Local, recoverable receipts; source and wiki stores are not one transaction."""
from __future__ import annotations

import hashlib
import base64
import binascii
import json
import re
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TypedDict

import safe_io
from reports import raw_archive


class OperationReceipt(TypedDict):
    schema_version: int
    operation_id: str
    revision_id: str
    batch: dict
    stage: str
    artifacts: str
    search: str
    log: str
    stage_times: dict[str, str]
    error_code: str


class TopicOperationReceipt(TypedDict):
    """V2 has multiple revision IDs in batch, never a synthetic revision_id."""
    schema_version: int
    operation_id: str
    batch: dict
    batch_sha256: str
    stage: str
    artifacts: str
    search: str
    log: str
    stage_times: dict[str, str]
    error_code: str


def _identity(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("identity_invalid")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encoded(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Journal:
    """Worker locks cover model calls; distinct receipt locks protect local updates."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or raw_archive.reports_root() / "wiki-source-v1")

    def _path(self, directory: str, identity: str) -> Path:
        return self.root / directory / f"{_identity(identity)}.json"

    def source_lock(self, revision_id: str):
        return safe_io.file_write_lock(str(self._path("source-locks", revision_id)))

    @contextmanager
    def target_locks(self, ids: list[str]) -> Iterator[None]:
        with ExitStack() as stack:
            for page_id in sorted(set(ids)):
                key = hashlib.sha256(page_id.encode("utf-8")).hexdigest()
                stack.enter_context(safe_io.file_write_lock(str(self._path("target-locks", key))))
            yield

    def _read(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise ValueError()
            return value
        except (OSError, ValueError, UnicodeError) as exc:
            raise ValueError("journal_corrupt") from exc

    def load_compilation(self, receipt: dict) -> dict:
        try:
            path = Path(receipt["compile_ref"]).resolve()
            path.relative_to((self.root / "compiled").resolve())
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != receipt["compile_sha256"]:
                raise ValueError()
            compiled = json.loads(data)
            if not isinstance(compiled, dict) or compiled.get("revision_id") != receipt["revision_id"]:
                raise ValueError()
            return compiled
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ValueError("journal_corrupt") from exc

    def load_source(self, revision_id: str) -> dict | None:
        receipt = self._read(self._path("sources", revision_id))
        if receipt is not None:
            if (receipt.get("revision_id") != revision_id or
                    receipt.get("compile_state") not in {"pending", "compiled", "skipped", "failed"} or
                    not isinstance(receipt.get("source"), dict) or
                    not isinstance(receipt.get("observed_evidence_ids"), list) or
                    not isinstance(receipt.get("evidence_by_role"), dict) or
                    not isinstance(receipt.get("routing"), dict)):
                raise ValueError("journal_corrupt")
            if receipt["compile_state"] in {"compiled", "skipped"}:
                self.load_compilation(receipt)
        return receipt

    def _source_receipt(self, source: dict) -> dict:
        revision_id = _identity(source["revision_id"])
        receipt = self.load_source(revision_id) or {
            "schema_version": 1, "revision_id": revision_id,
            "compile_state": "pending", "source": {k: v for k, v in source.items() if k != "body"},
            "observed_evidence_ids": [], "evidence_by_role": {}, "routing": {}, "created_at": _now(),
        }
        role = source.get("role", "support")
        if role not in {"support", "conflict"}:
            raise ValueError("evidence_role_invalid")
        observed = source.get("observed_evidence_ids") or []
        receipt["observed_evidence_ids"] = list(dict.fromkeys([*receipt["observed_evidence_ids"], *observed]))
        by_role = receipt["evidence_by_role"]
        by_role[role] = list(dict.fromkeys([*by_role.get(role, []), *observed]))
        receipt["updated_at"] = _now()
        return receipt

    def record_attempt(self, source: dict, *, state: str, error_code: str = "") -> dict:
        if state not in {"pending", "failed"} or (error_code and not re.fullmatch(r"[a-z0-9_:-]{1,100}", error_code)):
            raise ValueError("attempt_invalid")
        path = self._path("sources", source["revision_id"])
        with safe_io.file_write_lock(str(path)):
            receipt = self._source_receipt(source)
            if receipt["compile_state"] not in {"compiled", "skipped"}:
                receipt.update(compile_state=state, error_code=error_code)
            safe_io.atomic_write_json(str(path), receipt)
            return receipt

    def save_compilation(self, source: dict, compiled: dict) -> dict:
        path = self._path("sources", source["revision_id"])
        if compiled.get("revision_id") != source["revision_id"]:
            raise ValueError("compilation_identity_invalid")
        if not compiled.get("body") and not compiled.get("skip_reason"):
            raise ValueError("compilation_empty")
        with safe_io.file_write_lock(str(path)):
            receipt = self._source_receipt(source)
            text = _encoded(compiled)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if receipt["compile_state"] in {"compiled", "skipped"}:
                if receipt["compile_sha256"] != digest:
                    raise ValueError("compilation_conflict")
                safe_io.atomic_write_json(str(path), receipt)
                return receipt
            artifact = self._path("compiled", digest)
            if artifact.exists():
                if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                    raise ValueError("journal_corrupt")
            else:
                safe_io.atomic_write_text(str(artifact), text)
            receipt.update(compile_state="skipped" if compiled.get("skip_reason") else "compiled",
                           compile_ref=str(artifact), compile_sha256=digest,
                           skip_reason=compiled.get("skip_reason"), error_code="")
            safe_io.atomic_write_json(str(path), receipt)
            return receipt

    def save_routing(self, revision_id: str, role: str, state: dict) -> None:
        if role not in {"support", "conflict"}:
            raise ValueError("evidence_role_invalid")
        path = self._path("sources", revision_id)
        with safe_io.file_write_lock(str(path)):
            receipt = self.load_source(revision_id)
            if receipt is None or receipt["compile_state"] != "compiled":
                raise ValueError("compilation_not_ready")
            receipt["routing"][role] = state
            receipt["updated_at"] = _now()
            safe_io.atomic_write_json(str(path), receipt)

    def prepare(self, batch: dict) -> dict:
        path = self._path("operations", batch["operation_id"])
        _identity(batch["revision_id"])
        with safe_io.file_write_lock(str(path)):
            existing = self._read(path)
            if existing:
                if existing.get("batch") != batch:
                    raise ValueError("operation_conflict")
                return existing
            receipt = {"schema_version": 1, "operation_id": batch["operation_id"],
                       "revision_id": batch["revision_id"], "batch": batch, "stage": "prepared",
                       "artifacts": "pending", "search": "pending", "log": "pending",
                       "stage_times": {"prepared": _now()}, "error_code": ""}
            safe_io.atomic_write_json(str(path), receipt)
            return receipt

    def transition(self, operation_id: str, *, stage: str | None = None,
                   effects: dict | None = None, error_code: str = "") -> dict:
        return self._transition(operation_id, stage=stage, effects=effects, error_code=error_code)

    def _transition(self, operation_id: str, *, stage: str | None = None,
                    effects: dict | None = None, error_code: str = "", topic: bool = False,
                    version: int = 2) -> dict:
        transitions = {"prepared": {"prepared", "applied", "committed", "stale-base", "failed"},
                       "applied": {"applied", "committed", "stale-base", "failed"},
                       "committed": {"committed"}, "stale-base": {"stale-base"}, "failed": {"failed"}}
        directory = "topic-operations-v3" if topic and version == 3 else "topic-operations" if topic else "operations"
        path = self._path(directory, operation_id)
        with safe_io.file_write_lock(str(path)):
            receipt = (self.load_topic_operation_v3(operation_id) if topic and version == 3 else
                       self.load_topic_operation(operation_id) if topic else self._read(path))
            if receipt is None or stage is not None and stage not in transitions.get(receipt.get("stage"), set()):
                raise ValueError("transition_invalid")
            for name, value in (effects or {}).items():
                if name not in {"artifacts", "search", "log"} or value not in {"pending", "done", "failed"}:
                    raise ValueError("transition_invalid")
            if error_code and not re.fullmatch(r"[a-z0-9_:-]{1,100}", error_code):
                raise ValueError("error_code_invalid")
            if stage:
                receipt["stage"] = stage
                receipt["stage_times"][stage] = _now()
            receipt.update(effects or {})
            receipt["error_code"] = error_code
            safe_io.atomic_write_json(str(path), receipt)
            return receipt

    def load_topic_operation(self, operation_id: str) -> dict | None:
        """V2 is deliberately invisible to all legacy operation enumerators."""
        path = self._path("topic-operations", operation_id)
        if not path.exists():
            return None
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            batch = receipt["batch"]
            if (receipt["schema_version"] != 2 or batch["protocol_version"] != 2
                    or set(receipt) != set(TopicOperationReceipt.__required_keys__)
                    or receipt["operation_id"] != operation_id or batch["operation_id"] != operation_id
                    or receipt["batch_sha256"] != hashlib.sha256(_encoded(batch).encode()).hexdigest()
                    or operation_id != hashlib.sha256(_encoded(batch["proposal"]).encode()).hexdigest()
                    or receipt["stage"] not in {"prepared", "applied", "committed", "stale-base", "failed"}
                    or any(receipt[k] not in {"pending", "done", "failed"} for k in ("artifacts", "search", "log"))
                    or not isinstance(receipt["error_code"], str)
                    or not isinstance(receipt["stage_times"], dict)
                    or not isinstance(batch["expected_before"], dict)
                    or not isinstance(batch["expected_after"], dict)
                    or not batch["target_ids"] or not batch["upserts"]
                    or set(batch["expected_after"]) != {r["id"] for r in batch["upserts"]}
                    or not set(batch["expected_after"]) <= set(batch["expected_before"])):
                raise ValueError()
            return receipt
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ValueError("topic_journal_corrupt") from exc

    def prepare_topic(self, batch: dict) -> dict:
        if batch.get("protocol_version") != 2:
            raise ValueError("topic_protocol_invalid")
        path = self._path("topic-operations", batch["operation_id"])
        with safe_io.file_write_lock(str(path)):
            existing = self.load_topic_operation(batch["operation_id"])
            if existing:
                if existing["batch"] != batch:
                    raise ValueError("operation_conflict")
                return existing
            receipt = {"schema_version": 2, "operation_id": batch["operation_id"], "batch": batch,
                       "batch_sha256": hashlib.sha256(_encoded(batch).encode()).hexdigest(),
                       "stage": "prepared", "artifacts": "pending", "search": "pending", "log": "pending",
                       "stage_times": {"prepared": _now()}, "error_code": ""}
            safe_io.atomic_write_json(str(path), receipt)
            return receipt

    def transition_topic(self, operation_id: str, **kwargs) -> dict:
        if "version" in kwargs and (type(kwargs["version"]) is not int or kwargs["version"] != 2):
            raise ValueError("topic_protocol_invalid")
        kwargs.pop("version", None)
        return self._transition(operation_id, topic=True, version=2, **kwargs)

    def pending_topic_operations(self) -> list[dict]:
        result = []
        for path in sorted((self.root / "topic-operations").glob("*.json")):
            receipt = self.load_topic_operation(path.stem)
            if receipt["stage"] != "committed" or any(receipt[k] != "done" for k in ("artifacts", "search", "log")):
                result.append(receipt)
        return result

    def load_topic_operation_v3(self, operation_id: str) -> dict | None:
        path = self._path("topic-operations-v3", operation_id)
        if not path.exists():
            return None
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate key")
                    result[key] = value
                return result
            def constant(_):
                raise ValueError("non-finite number")
            receipt = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                                 parse_constant=constant)
            batch = receipt["batch"]
            required = {"schema_version", "operation_id", "batch", "batch_sha256", "stage", "artifacts",
                        "search", "log", "stage_times", "error_code"}
            batch_fields = {"protocol_version", "operation_id", "proposal", "revision_ids", "source_map",
                "dependencies", "source_inputs", "public_proof", "original_claims", "target_ids",
                "expected_before", "expected_after", "upserts", "snapshots", "prepared_at"}
            if (type(receipt) is not dict or set(receipt) != required or type(receipt["schema_version"]) is not int
                    or receipt["schema_version"] != 3 or type(batch) is not dict
                    or type(batch.get("protocol_version")) is not int or batch.get("protocol_version") != 3
                    or set(batch) != batch_fields
                    or receipt["operation_id"] != operation_id or batch.get("operation_id") != operation_id
                    or receipt["batch_sha256"] != hashlib.sha256(_encoded(batch).encode()).hexdigest()
                    or operation_id != hashlib.sha256(_encoded(batch["proposal"]).encode()).hexdigest()
                    or receipt["stage"] not in {"prepared", "applied", "committed", "stale-base", "failed"}
                    or any(receipt[k] not in {"pending", "done", "failed"} for k in ("artifacts", "search", "log"))
                    or type(receipt["stage_times"]) is not dict or type(receipt["error_code"]) is not str
                    or not all(k in batch for k in ("source_inputs", "public_proof", "expected_before",
                        "expected_after", "upserts", "target_ids", "prepared_at"))
                    or type(batch["source_inputs"]) is not dict or type(batch["public_proof"]) is not dict
                    or set(batch["public_proof"]) != {"export_metadata", "source_assets"}
                    or type(batch["public_proof"]["export_metadata"]) is not dict
                    or type(batch["public_proof"]["source_assets"]) is not dict
                    or type(batch["expected_before"]) is not dict or type(batch["expected_after"]) is not dict
                    or type(batch["upserts"]) is not list or type(batch["target_ids"]) is not list
                    or set(batch["expected_after"]) != {row["id"] for row in batch["upserts"]}
                    or not set(batch["expected_after"]) <= set(batch["expected_before"])):
                raise ValueError()
            proposal = batch["proposal"]
            if (type(proposal) is not dict
                    or set(proposal) != {"protocol_version", "revision_id", "role", "observed_evidence_ids",
                        "context", "dependencies", "targets", "refusal", "source_map"}
                    or type(proposal.get("protocol_version")) is not int or proposal["protocol_version"] != 3
                    or proposal["role"] not in {"support", "conflict"}
                    or type(proposal["observed_evidence_ids"]) is not list
                    or type(proposal["context"]) is not dict or type(proposal["dependencies"]) is not list
                    or type(proposal["targets"]) is not list or type(proposal["source_map"]) is not dict
                    or type(batch["revision_ids"]) is not list or type(batch["source_map"]) is not dict
                    or type(batch["dependencies"]) is not list or type(batch["original_claims"]) is not dict
                    or type(batch["snapshots"]) is not list or type(batch["prepared_at"]) is not str):
                raise ValueError()
            proposal_revisions = sorted({entry["revision_id"] for entry in proposal["source_map"].values()})
            if (batch["source_map"] != proposal["source_map"] or batch["dependencies"] != proposal["dependencies"]
                    or batch["revision_ids"] != proposal_revisions
                    or batch["target_ids"] != [target["page_id"] for target in proposal["targets"]]
                    or set(batch["source_inputs"]) != set(proposal_revisions)):
                raise ValueError()
            export = batch["public_proof"]["export_metadata"]
            if (set(export) != {"protocol_version", "source_note_ids", "target_ids"}
                    or type(export["protocol_version"]) is not int or export["protocol_version"] != 3
                    or type(export["source_note_ids"]) is not list
                    or type(export["target_ids"]) is not list
                    or export["source_note_ids"] != sorted(item["note_id"] for item in batch["source_inputs"].values())
                    or export["target_ids"] != batch["target_ids"]):
                raise ValueError()
            for revision, item in batch["source_inputs"].items():
                if (not re.fullmatch(r"[a-f0-9]{64}", revision) or type(item) is not dict
                        or set(item) != {"revision_id", "role", "observed_evidence_ids",
                            "selected_role_evidence_ids", "proposal_observed_evidence_ids", "evidence_by_role",
                            "source", "note_id", "note_hashes", "note_assets", "input_sha256"}
                        or item["revision_id"] != revision or item["role"] not in {"support", "conflict"}
                        or type(item["observed_evidence_ids"]) is not list
                        or type(item["selected_role_evidence_ids"]) is not list
                        or type(item["proposal_observed_evidence_ids"]) is not list
                        or type(item["evidence_by_role"]) is not dict or type(item["source"]) is not dict
                        or set(item["source"]) != {"revision_id", "protocol_version", "provider", "canonical_url",
                            "scope", "raw_sha256", "text_sha256"}
                        or set(item["note_hashes"]) != {"manifest_sha256", "raw_sha256", "text_sha256",
                            "compile_sha256", "note_json_sha256", "note_md_sha256"}
                        or set(item["note_assets"]) != {"note_json_bytes", "note_md_bytes", "manifest_bytes"}
                        or any(type(v) is not str for v in item["note_assets"].values())):
                    raise ValueError()
                if (set(item["evidence_by_role"]) - {"support", "conflict"}
                        or any(type(ids) is not list or any(type(eid) is not str for eid in ids)
                               for ids in item["evidence_by_role"].values())
                        or any(type(eid) is not str for eid in item["observed_evidence_ids"])
                        or any(type(eid) is not str for eid in item["selected_role_evidence_ids"])
                        or any(type(eid) is not str for eid in item["proposal_observed_evidence_ids"])
                        or item["selected_role_evidence_ids"] != item["evidence_by_role"].get(item["role"], [])
                        or not set(item["observed_evidence_ids"]) <= set().union(
                            *(set(ids) for ids in item["evidence_by_role"].values()))
                        or item["source"].get("revision_id") != revision
                        or type(item["source"].get("protocol_version")) is not int):
                    raise ValueError()
                if revision == proposal["revision_id"] and (item["role"] != proposal["role"]
                        or item["proposal_observed_evidence_ids"] != proposal["observed_evidence_ids"]):
                    raise ValueError()
                unhashed = {k: v for k, v in item.items() if k != "input_sha256"}
                if item["input_sha256"] != hashlib.sha256(_encoded(unhashed).encode()).hexdigest():
                    raise ValueError()
                decoded = {key: base64.b64decode(item["note_assets"][key], validate=True) for key in
                           ("note_json_bytes", "note_md_bytes", "manifest_bytes")}
                manifest = json.loads(decoded["manifest_bytes"], object_pairs_hook=unique, parse_constant=constant)
                note = json.loads(decoded["note_json_bytes"], object_pairs_hook=unique, parse_constant=constant)
                hashes = item["note_hashes"]
                manifest_fields = {"schema_version", "note_id", "identity_version", "source_protocol_version",
                    "revision_id", "projection_version", "canonical_url", "raw_sha256", "text_sha256",
                    "compile_sha256", "note_json_sha256", "note_md_sha256"}
                note_fields = {"revision_id", "canonical_url", "provider", "scope", "provenance_class",
                    "published_at", "collected_at", "projection_version", "source_protocol_version",
                    "title", "body", "claims", "terms", "verified_aliases", "limitations"}
                identity_fields = ("identity_version", "source_protocol_version", "revision_id", "projection_version",
                    "canonical_url", "raw_sha256", "text_sha256", "compile_sha256", "note_json_sha256", "note_md_sha256")
                identity = {key: manifest.get(key) for key in identity_fields} if type(manifest) is dict else {}
                if (type(manifest) is not dict or set(manifest) != manifest_fields
                        or type(note) is not dict or set(note) != note_fields
                        or any(type(manifest.get(key)) is not int for key in
                               ("schema_version", "identity_version", "source_protocol_version", "projection_version"))
                        or any(type(note.get(key)) is not int for key in ("projection_version", "source_protocol_version"))
                        or manifest.get("schema_version") != 1 or manifest.get("identity_version") != 1
                        or hashlib.sha256(_encoded(identity).encode()).hexdigest() != item["note_id"]
                        or manifest.get("note_id") != item["note_id"]
                        or manifest.get("revision_id") != revision or note.get("revision_id") != revision
                        or item["source"].get("revision_id") != revision
                        or item["source"].get("protocol_version") != manifest.get("source_protocol_version")
                        or hashlib.sha256(decoded["manifest_bytes"]).hexdigest() != hashes["manifest_sha256"]
                        or hashlib.sha256(decoded["note_json_bytes"]).hexdigest() != hashes["note_json_sha256"]
                        or hashlib.sha256(decoded["note_md_bytes"]).hexdigest() != hashes["note_md_sha256"]
                        or manifest.get("note_json_sha256") != hashes["note_json_sha256"]
                        or manifest.get("note_md_sha256") != hashes["note_md_sha256"]
                        or any(manifest.get(k) != hashes[k] for k in ("raw_sha256", "text_sha256", "compile_sha256"))
                        or _encoded(note).encode() != decoded["note_json_bytes"]
                        or _encoded(manifest).encode() != decoded["manifest_bytes"]):
                    raise ValueError()
            if set(batch["public_proof"]["source_assets"]) != set(batch["source_inputs"]):
                raise ValueError()
            for revision, asset in batch["public_proof"]["source_assets"].items():
                if asset != {k: batch["source_inputs"][revision][k] for k in ("note_id", "note_hashes", "note_assets")}:
                    raise ValueError()
            return receipt
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, binascii.Error) as exc:
            raise ValueError("topic_journal_corrupt") from exc

    def prepare_topic_v3(self, batch: dict) -> dict:
        if type(batch) is not dict or type(batch.get("protocol_version")) is not int or batch.get("protocol_version") != 3:
            raise ValueError("topic_protocol_invalid")
        path = self._path("topic-operations-v3", batch["operation_id"])
        with safe_io.file_write_lock(str(path)):
            existing = self.load_topic_operation_v3(batch["operation_id"])
            if existing:
                if existing["batch"] != batch:
                    raise ValueError("operation_conflict")
                return existing
            receipt = {"schema_version": 3, "operation_id": batch["operation_id"], "batch": batch,
                "batch_sha256": hashlib.sha256(_encoded(batch).encode()).hexdigest(), "stage": "prepared",
                "artifacts": "pending", "search": "pending", "log": "pending",
                "stage_times": {"prepared": _now()}, "error_code": ""}
            safe_io.atomic_write_json(str(path), receipt)
            return receipt

    def transition_topic_v3(self, operation_id: str, **kwargs) -> dict:
        return self._transition(operation_id, topic=True, version=3, **kwargs)

    def pending_topic_operations_v3(self) -> list[dict]:
        result = []
        for path in sorted((self.root / "topic-operations-v3").glob("*.json")):
            receipt = self.load_topic_operation_v3(path.stem)
            if receipt["stage"] != "committed" or any(receipt[k] != "done" for k in ("artifacts", "search", "log")):
                result.append(receipt)
        return result

    def pending_operations(self) -> list[dict]:
        pending = []
        for path in sorted((self.root / "operations").glob("*.json")):
            _identity(path.stem)
            receipt = self._read(path)
            if receipt is None or receipt.get("operation_id") != path.stem:
                raise ValueError("journal_corrupt")
            if receipt["stage"] != "committed" or any(receipt.get(k) != "done" for k in ("artifacts", "search", "log")):
                pending.append(receipt)
        return pending
