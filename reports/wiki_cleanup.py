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


def _pages_from_rows(rows: list[dict]) -> list[dict]:
    """Project stored rows exactly as ``wiki._all_wiki_pages`` does, for fingerprint parity."""
    ordered = sorted(rows, key=lambda row: row.get("createdAt") or "", reverse=True)
    wiki_rows = [row for row in ordered if wiki._is_wiki_record(row)]
    return wiki._apply_backlinks([wiki._record_to_page(row) for row in wiki_rows], wiki_rows)


def apply_cleanup(actions: Iterable[dict], dry_run: bool = True) -> dict:
    """Recheck a plan against current state, then archive at most 50 pages.

    The recheck, the backup and the write happen under one store lock, so a page
    changed after planning is skipped as stale instead of being overwritten.
    """
    supplied = [action for action in actions if isinstance(action, dict)]
    requested = supplied[:MAX_CLEANUP_ACTIONS]
    result = {
        "dry_run": bool(dry_run), "planned": [], "archived": [], "skipped_stale": [],
        "backup_path": "", "truncated_actions": max(0, len(supplied) - MAX_CLEANUP_ACTIONS),
    }
    events_path = shared_memory._paths()["events"]
    with shared_memory._events_lock():
        rows = shared_memory._read_jsonl_checked(events_path)
        pages = {_page_id(page): page for page in _pages_from_rows(rows)}
        current = {(action["page_id"], action["reason"]): action for action in plan_cleanup(pages.values())}
        for action in requested:
            page_id = str(action.get("page_id") or "").strip()
            if action.get("action") != "archive" or not page_id:
                continue
            match = current.get((page_id, action.get("reason")))
            if match and match["fingerprint"] == action.get("fingerprint") and page_id not in result["planned"]:
                result["planned"].append(page_id)
            else:
                result["skipped_stale"].append(page_id)
        if dry_run or not result["planned"]:
            return result
        backup_dir = Path(shared_memory.shared_memory_dir()) / "wiki-cleanup-backups"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        result["backup_path"] = str(backup_dir / f"events-{stamp}.jsonl")
        safe_io.atomic_write_text(result["backup_path"], "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))
        by_id = {
            page_id: shared_memory.normalize_record(
                wiki._build_wiki_record({**pages[page_id], "status": "archived"}, existing=pages[page_id]))
            for page_id in result["planned"]
        }
        shared_memory._write_jsonl_locked([by_id.pop(row["id"], row) for row in rows] + list(by_id.values()))
        shared_memory._write_index_locked()
        result["archived"] = list(result["planned"])
    wiki._CACHE.clear()
    wiki._append_wiki_log("archive", title=f"{len(result['archived'])} empty or duplicate draft pages",
                          page_ids=result["archived"][:100], archived_count=len(result["archived"]),
                          backup=Path(result["backup_path"]).name)
    wiki._debounced_rebuild()
    return result


def plan_mechanical_archive(pages: Iterable[dict]) -> list[dict]:
    """Propose archiving catalog/template pages that carry no compiled knowledge.

    Pages with structured source knowledge, raw-layer catalogs and pages already
    archived are never proposed. The detector is the acceptance audit's own.
    """
    from reports.wiki_karpathy_audit import mechanical_reasons

    actions = []
    for page in pages:
        if not isinstance(page, dict) or not _page_id(page) or page.get("status") == "archived":
            continue
        if wiki.is_raw_layer_page(page) or (page.get("source_knowledge") or {}).get("claims"):
            continue
        reasons = mechanical_reasons(page)
        if reasons:
            # backlinks are derived from other pages and their order varies with read order
            stable = {key: value for key, value in page.items() if key != "backlinks"}
            actions.append({**_make_action(page, "mechanical:" + "+".join(reasons)), "fingerprint": _fingerprint(stable)})
    return sorted(actions, key=lambda action: action["page_id"])


def apply_mechanical_archive(actions: Iterable[dict], dry_run: bool = True) -> dict:
    """Archive every still-matching proposal in one guarded store rewrite.

    A page that changed since planning is skipped (fingerprint), the full store is
    backed up first, and links from surviving pages to archived ones are removed so
    the wiki has no dead links. Archived records keep their bodies and provenance.
    """
    planned = {a["page_id"]: a for a in actions if isinstance(a, dict) and a.get("action") == "archive"}
    result = {"dry_run": bool(dry_run), "archived": [], "skipped_stale": [], "unlinked_pages": [], "backup_path": ""}
    with shared_memory._events_lock():
        rows = shared_memory._read_jsonl_checked(shared_memory._paths()["events"])
        wiki_rows = [row for row in rows if wiki._is_wiki_record(row)]
        pages = {page["id"]: page for page in wiki._apply_backlinks([wiki._record_to_page(row) for row in wiki_rows], wiki_rows)}
        current = {a["page_id"]: a for a in plan_mechanical_archive(pages.values())}
        archive_ids = set()
        for page_id, action in planned.items():
            match = current.get(page_id)
            if match and match["fingerprint"] == action.get("fingerprint"):
                archive_ids.add(page_id)
            else:
                result["skipped_stale"].append(page_id)
        upserts = []
        for page_id in sorted(archive_ids):
            upserts.append(wiki._build_wiki_record({**pages[page_id], "status": "archived"}, existing=pages[page_id]))
        for page_id, page in pages.items():
            if page_id in archive_ids or page.get("status") == "archived":
                continue
            kept = [link for link in page.get("links") or [] if link not in archive_ids]
            if kept != list(page.get("links") or []):
                upserts.append(wiki._build_wiki_record({**page, "links": kept}, existing=page))
                result["unlinked_pages"].append(page_id)
        result["archived"] = sorted(archive_ids)
        if dry_run or not upserts:
            return result
        backup_dir = Path(shared_memory.shared_memory_dir()) / "wiki-cleanup-backups"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        result["backup_path"] = str(backup_dir / f"events-{stamp}.jsonl")
        safe_io.atomic_write_text(result["backup_path"], "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))
        by_id = {row["id"]: shared_memory.normalize_record(row) for row in upserts}
        shared_memory._write_jsonl_locked([by_id.pop(row["id"], row) for row in rows] + list(by_id.values()))
        shared_memory._write_index_locked()
    wiki._CACHE.clear()
    wiki._append_wiki_log("archive", title=f"{len(result['archived'])} mechanical catalog pages",
                          page_ids=result["archived"][:100], archived_count=len(result["archived"]),
                          backup=Path(result["backup_path"]).name)
    return result
