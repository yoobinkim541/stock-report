"""Plan and safely archive empty or exact-duplicate draft wiki pages."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import safe_io
from agent_console import shared_memory, wiki


MAX_CLEANUP_ACTIONS = 50
_EMPTY_FIELDS = (
    "body", "summary", "source_refs", "artifacts", "evidence_ids",
    "conflicting_evidence_ids", "links", "backlinks", "messages", "decisions",
    "openQuestions", "merge_history", "merged_into", "merge_event_id", "history",
    "review_history", "change_history", "feedback", "lastQuery", "lastUsedAt",
    "report_citation", "answer_hints", "staleness_policy",
)
_HISTORY_FIELDS = (
    "merge_history", "merged_into", "merge_event_id", "history", "review_history",
    "change_history", "messages", "decisions", "openQuestions", "feedback",
)
_SYNTHESIS_PROTECTED_FIELDS = (
    "conflicting_evidence_ids", "answer_hints", "staleness_policy", "report_citation",
)
_PROVENANCE_FIELDS = (
    "raw_path", "raw_sha256", "raw_source", "text_path", "manifest_path",
    "source_digest", "provenance", "source_provenance",
)


def _has_value(value: object) -> bool:
    if value is None or value is False or value == 0:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _fingerprint(page: dict) -> str:
    encoded = json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _semantic_key(page: dict) -> tuple:
    return tuple(
        json.dumps(page.get(field) or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if field in {"source_refs", "evidence_ids"}
        else str(page.get(field) or "").strip()
        for field in ("title", "surface", "kind", "body", "summary", "source_refs", "evidence_ids")
    )


def _page_id(page: dict) -> str:
    return str(page.get("id") or "").strip()


def _relationship_protected_ids(pages: list[dict]) -> set[str]:
    protected: set[str] = set()
    for page in pages:
        page_id = _page_id(page)
        if not page_id:
            continue
        if page.get("parent_page_id"):
            protected.add(page_id)
            protected.add(str(page["parent_page_id"]))
        for link in page.get("links") or []:
            protected.add(str(link))
        for backlink in page.get("backlinks") or []:
            protected.update((page_id, str(backlink)))
        for tag in page.get("tags") or []:
            tag = str(tag)
            if tag.startswith("split_from:"):
                protected.update((page_id, tag.split(":", 1)[1]))
            elif tag.startswith("split_into:"):
                protected.update((page_id, tag.split(":", 1)[1]))
    return protected


def _is_protected_metadata(page: dict) -> bool:
    if page.get("status") != "draft" or str(page.get("kind") or "").lower() == "source_digest":
        return True
    if _has_provenance(page):
        return True
    for record in (page, page.get("raw") or {}):
        if not isinstance(record, dict):
            continue
        knowledge = record.get("source_knowledge")
        if (any(_has_value(value) for value in knowledge.values()) if isinstance(knowledge, dict)
                else _has_value(knowledge)):
            return True
    tags = [str(tag).lower() for tag in page.get("tags") or []]
    if any(any(marker in tag for marker in ("raw", "provenance", "source_digest", "source-digest")) for tag in tags):
        return True
    if any(_has_value(page.get(field)) for field in _HISTORY_FIELDS):
        return True
    if any(_has_value(page.get(field)) for field in _SYNTHESIS_PROTECTED_FIELDS):
        return True
    state = page.get("distillation_state")
    if isinstance(state, dict) and any(_has_value(value) for value in state.values()):
        return True
    if any(_has_value(page.get(field)) for field in ("lastUsedAt", "lastQuery", "reviewed_at", "reviewedAt", "review_status")):
        return True
    for field, value in page.items():
        name = str(field).lower()
        if _has_value(value) and any(marker in name for marker in ("history", "review", "usage", "use_count")):
            return True
    try:
        if int(page.get("useCount") or 0) > 0:
            return True
    except (TypeError, ValueError):
        return True
    return False


def _has_provenance(page: dict) -> bool:
    if any(_has_value(page.get(field)) for field in _PROVENANCE_FIELDS):
        return True
    stored = page.get("raw")
    return isinstance(stored, dict) and any(_has_value(stored.get(field)) for field in _PROVENANCE_FIELDS)


def _make_action(page: dict, reason: str) -> dict:
    return {
        "action": "archive",
        "page_id": _page_id(page),
        "reason": reason,
        "fingerprint": _fingerprint(page),
    }


def plan_cleanup(pages: Iterable[dict]) -> list[dict]:
    """Return deterministic archive proposals; this function never writes."""
    snapshot = [deepcopy(page) for page in pages if isinstance(page, dict) and _page_id(page)]
    protected_ids = _relationship_protected_ids(snapshot)
    eligible: list[dict] = []
    actions: list[dict] = []

    for page in snapshot:
        page_id = _page_id(page)
        if _is_protected_metadata(page) or page_id in protected_ids:
            continue
        if any(_has_value(page.get(field)) for field in _EMPTY_FIELDS):
            eligible.append(page)
        else:
            actions.append(_make_action(page, "empty_draft"))

    duplicate_groups: dict[tuple, list[dict]] = {}
    for page in eligible:
        duplicate_groups.setdefault(_semantic_key(page), []).append(page)
    for group in duplicate_groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda page: (str(page.get("created_at") or ""), _page_id(page)))
        actions.extend(_make_action(page, "exact_duplicate") for page in ordered[1:])

    return sorted(actions, key=lambda action: (action["page_id"], action["reason"]))


def _current_pages() -> list[dict]:
    wiki._CACHE.clear()
    return wiki._all_wiki_pages()


def _snapshot_backup() -> str:
    events_path = Path(shared_memory.shared_memory_dir()) / "events.jsonl"
    contents = events_path.read_text(encoding="utf-8", errors="replace") if events_path.exists() else ""
    backup_dir = events_path.parent / "wiki-cleanup-backups"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = backup_dir / f"events-{timestamp}.jsonl"
    safe_io.atomic_write_text(str(backup_path), contents)
    return str(backup_path)


def apply_cleanup(actions: Iterable[dict], dry_run: bool = True) -> dict:
    """Recheck a plan against current state, then archive at most 50 pages."""
    supplied = [action for action in actions if isinstance(action, dict)]
    requested = supplied[:MAX_CLEANUP_ACTIONS]
    result = {
        "dry_run": bool(dry_run), "planned": [], "archived": [], "skipped_stale": [],
        "backup_path": "", "truncated_actions": max(0, len(supplied) - MAX_CLEANUP_ACTIONS),
    }
    backup_created = False

    for action in requested:
        page_id = str(action.get("page_id") or "").strip()
        if action.get("action") != "archive" or not page_id:
            continue
        pages = _current_pages()
        current_plan = plan_cleanup(pages)
        eligible = next((candidate for candidate in current_plan
                         if candidate["page_id"] == page_id
                         and candidate["reason"] == action.get("reason")
                         and candidate["fingerprint"] == action.get("fingerprint")), None)
        if not eligible:
            result["skipped_stale"].append(page_id)
            continue
        result["planned"].append(page_id)
        if dry_run:
            continue
        if not backup_created:
            result["backup_path"] = _snapshot_backup()
            backup_created = True
        page = next((row for row in pages if _page_id(row) == page_id), None)
        if not page:
            result["planned"].pop()
            result["skipped_stale"].append(page_id)
            continue
        payload = deepcopy(page)
        payload["status"] = "archived"
        wiki.upsert_page(payload)
        result["archived"].append(page_id)

    return result
