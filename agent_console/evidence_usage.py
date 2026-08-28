from __future__ import annotations

import json
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import safe_io

from . import shared_memory


def usage_path() -> Path:
    override = os.getenv("AGENT_CONSOLE_EVIDENCE_USAGE_PATH", "").strip()
    return Path(override).expanduser() if override else Path(shared_memory.shared_memory_dir()) / "evidence_usage.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_ids(values: list[str]) -> list[str]:
    return sorted({str(value).strip()[:120] for value in values or [] if str(value).strip()})


def _ordered_ids(values: object) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()[:120]
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_usage_rows() -> list[dict]:
    """Return telemetry rows without changing the append-only usage log."""
    return _read_rows(usage_path())


def query_id_for(query: str, surface: str, status: str = "all") -> str:
    """Build the same stable query id used by the wiki retrieval telemetry."""
    text = "|".join((str(surface or "").strip().lower()[:60],
                     str(status or "").strip().lower()[:40],
                     str(query or "").strip().lower()[:600]))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24] if str(query or "").strip() else ""


def validate_citations(cited_evidence_ids: object, provided_evidence_ids: object) -> dict:
    cited = _ordered_ids(cited_evidence_ids)
    provided = set(_clean_ids(_ordered_ids(provided_evidence_ids)))
    valid = [item for item in cited if item in provided]
    invalid = [item for item in cited if item not in provided]
    return {
        "cited_evidence_ids": valid,
        "invalid_cited_evidence_ids": invalid,
        "provided_evidence_ids": sorted(provided),
        "valid": not invalid,
    }


def _append_once(row: dict) -> None:
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    event_key = f"{row['kind']}:{row['query_id']}"
    with safe_io.file_write_lock(str(path)):
        if any(str(existing.get("event_key") or "") == event_key for existing in _read_rows(path)):
            return
        row["event_key"] = event_key
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def record_retrieval(query_id: str, page_ids: list[str], provider: str, fallback: bool) -> None:
    query_id = str(query_id or "").strip()[:120]
    if not query_id:
        return
    _append_once({
        "kind": "retrieval",
        "query_id": query_id,
        "page_ids": _clean_ids(page_ids),
        "provider": str(provider or "fallback")[:40],
        "fallback": bool(fallback),
        "created_at": _now(),
    })


def record_context_use(query_id: str, evidence_ids: list[str] | None = None, *,
                       retrieved_page_ids: object = None,
                       provided_evidence_ids: object = None) -> None:
    """Record context use while preserving the legacy positional API.

    The extended form records the exact wiki pages and original evidence IDs exposed to
    the final prompt. Legacy callers retain the old retrieval intersection behavior.
    """
    query_id = str(query_id or "").strip()[:120]
    if not query_id:
        return
    extended = retrieved_page_ids is not None or provided_evidence_ids is not None
    path = usage_path()
    retrieved = set()
    for row in _read_rows(path):
        if row.get("kind") == "retrieval" and row.get("query_id") == query_id:
            retrieved.update(_clean_ids(row.get("page_ids") or []))
    if extended:
        page_ids = _clean_ids(_ordered_ids(retrieved_page_ids))
        provided = _clean_ids(_ordered_ids(provided_evidence_ids))
        _append_once({
            "kind": "context_use",
            "query_id": query_id,
            "page_ids": page_ids,
            "retrieved_page_ids": page_ids,
            "provided_evidence_ids": provided,
            "created_at": _now(),
        })
        return
    used = sorted(retrieved.intersection(_clean_ids(evidence_ids or [])))
    if not used:
        return
    _append_once({
        "kind": "context_use",
        "query_id": query_id,
        "page_ids": used,
        "created_at": _now(),
    })


def record_answer_use(query_id: str, cited_evidence_ids: object,
                      provided_evidence_ids: object, *, engine: str = "",
                      retrieved_page_ids: object = None, structured: bool = False) -> dict:
    """Record the answer's verified citations and return the validation result.

    Invalid ids are retained only as diagnostic telemetry and never returned as citations.
    """
    query_id = str(query_id or "").strip()[:120]
    validation = validate_citations(cited_evidence_ids, provided_evidence_ids)
    if not query_id:
        return validation
    _append_once({
        "kind": "answer_use",
        "query_id": query_id,
        "cited_evidence_ids": validation["cited_evidence_ids"],
        "invalid_cited_evidence_ids": validation["invalid_cited_evidence_ids"],
        "provided_evidence_ids": validation["provided_evidence_ids"],
        "retrieved_page_ids": _clean_ids(_ordered_ids(retrieved_page_ids)),
        "engine": str(engine or "")[:40],
        "structured": bool(structured),
        "citation_valid": bool(validation["valid"]),
        "created_at": _now(),
    })
    return validation


def usage_summary(*, hours: int = 24, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(hours=max(1, int(hours or 24)))
    rows = []
    for row in read_usage_rows():
        try:
            created = datetime.fromisoformat(str(row.get("created_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= cutoff:
            rows.append(row)
    retrievals = [row for row in rows if row.get("kind") == "retrieval"]
    context_rows = [row for row in rows if row.get("kind") == "context_use"]
    answer_rows = [row for row in rows if row.get("kind") == "answer_use"]
    retrieved_pages = {page_id for row in retrievals for page_id in _clean_ids(row.get("page_ids") or [])}
    context_pages = {page_id for row in context_rows for page_id in _clean_ids(row.get("page_ids") or [])}
    cited_evidence = {evidence_id for row in answer_rows for evidence_id in _clean_ids(row.get("cited_evidence_ids") or [])}
    invalid_citations = sum(len(_clean_ids(row.get("invalid_cited_evidence_ids") or [])) for row in answer_rows)
    fallbacks = sum(bool(row.get("fallback")) for row in retrievals)
    return {
        "hours": max(1, int(hours or 24)),
        "retrieval_count": len(retrievals),
        "context_use_count": len(context_rows),
        "answer_use_count": len(answer_rows),
        "cited_evidence_count": len(cited_evidence),
        "invalid_citation_count": invalid_citations,
        "retrieved_page_count": len(retrieved_pages),
        "context_page_count": len(context_pages),
        "unused_retrieved_page_count": len(retrieved_pages - context_pages),
        "retrieval_to_context_ratio": len(context_pages) / len(retrieved_pages) if retrieved_pages else 0.0,
        "fallback_retrieval_count": fallbacks,
        "fallback_ratio": fallbacks / len(retrievals) if retrievals else 0.0,
    }
