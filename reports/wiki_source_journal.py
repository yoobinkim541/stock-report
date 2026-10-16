"""Local, recoverable receipts; source and wiki stores are not one transaction."""
from __future__ import annotations

import hashlib
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
                    effects: dict | None = None, error_code: str = "", topic: bool = False) -> dict:
        transitions = {"prepared": {"prepared", "applied", "committed", "stale-base", "failed"},
                       "applied": {"applied", "committed", "stale-base", "failed"},
                       "committed": {"committed"}, "stale-base": {"stale-base"}, "failed": {"failed"}}
        path = self._path("topic-operations" if topic else "operations", operation_id)
        with safe_io.file_write_lock(str(path)):
            receipt = self.load_topic_operation(operation_id) if topic else self._read(path)
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
        return self._transition(operation_id, topic=True, **kwargs)

    def pending_topic_operations(self) -> list[dict]:
        result = []
        for path in sorted((self.root / "topic-operations").glob("*.json")):
            receipt = self.load_topic_operation(path.stem)
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
