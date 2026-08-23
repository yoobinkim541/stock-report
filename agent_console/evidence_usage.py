from __future__ import annotations

import json
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


def record_context_use(query_id: str, evidence_ids: list[str]) -> None:
    query_id = str(query_id or "").strip()[:120]
    if not query_id:
        return
    path = usage_path()
    retrieved = set()
    for row in _read_rows(path):
        if row.get("kind") == "retrieval" and row.get("query_id") == query_id:
            retrieved.update(_clean_ids(row.get("page_ids") or []))
    used = sorted(retrieved.intersection(_clean_ids(evidence_ids)))
    if not used:
        return
    _append_once({
        "kind": "context_use",
        "query_id": query_id,
        "page_ids": used,
        "created_at": _now(),
    })


def usage_summary(*, hours: int = 24, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(hours=max(1, int(hours or 24)))
    rows = []
    for row in _read_rows(usage_path()):
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
    retrieved_pages = {page_id for row in retrievals for page_id in _clean_ids(row.get("page_ids") or [])}
    context_pages = {page_id for row in context_rows for page_id in _clean_ids(row.get("page_ids") or [])}
    fallbacks = sum(bool(row.get("fallback")) for row in retrievals)
    return {
        "hours": max(1, int(hours or 24)),
        "retrieval_count": len(retrievals),
        "context_use_count": len(context_rows),
        "retrieved_page_count": len(retrieved_pages),
        "context_page_count": len(context_pages),
        "unused_retrieved_page_count": len(retrieved_pages - context_pages),
        "retrieval_to_context_ratio": len(context_pages) / len(retrieved_pages) if retrieved_pages else 0.0,
        "fallback_retrieval_count": fallbacks,
        "fallback_ratio": fallbacks / len(retrievals) if retrievals else 0.0,
    }
