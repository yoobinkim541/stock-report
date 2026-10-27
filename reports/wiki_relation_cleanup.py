#!/usr/bin/env python3
"""Safely remove low-value wiki graph edges without rewriting knowledge history.

The source curator intentionally creates event- and evidence-based edges.  This
module only removes edges that are mechanically invalid or point at an archive
explicitly marked stale.  Merge archives remain addressable for auditability.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable

from agent_console import wiki


_STALE_REASON = "archived_reason:stale"
_MERGED_REASONS = {"archived_reason:merged", "merged"}


def _text(value: object, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _page_id(page: dict) -> str:
    return _text(page.get("id"), 80)


def _tags(page: dict) -> set[str]:
    return {_text(tag, 120).lower() for tag in page.get("tags") or [] if _text(tag, 120)}


def _is_merge_archive(page: dict) -> bool:
    tags = _tags(page)
    return bool(
        page.get("merged_into")
        or page.get("merge_event_id")
        or any(tag.startswith("merged_into:") or tag.startswith("merge_event:") for tag in tags)
        or tags & _MERGED_REASONS
    )


def _is_stale_archive(page: dict) -> bool:
    return (
        _text(page.get("status"), 24).lower() == "archived"
        and _STALE_REASON in _tags(page)
        and not _is_merge_archive(page)
    )


def _normalized_links(page: dict) -> list[str]:
    """Keep order while stripping blank, self, and duplicate link ids."""
    page_id = _page_id(page)
    links: list[str] = []
    seen: set[str] = set()
    for raw in page.get("links") or []:
        link_id = _text(raw, 80)
        if not link_id or link_id == page_id or link_id in seen:
            continue
        seen.add(link_id)
        links.append(link_id)
    return links


def plan(pages: Iterable[dict] | None = None) -> dict:
    """Return a deterministic, non-mutating cleanup plan.

    Archived pages are never rewritten here: their links can be part of merge
    history. Active pages may lose only invalid links or links to an explicitly
    stale, non-merge archive.
    """
    rows = [page for page in (pages if pages is not None else wiki.list_pages(query="", status="all", limit=10000)) if isinstance(page, dict)]
    by_id = {_page_id(page): page for page in rows if _page_id(page)}
    updates: list[dict] = []
    reasons = Counter()
    edge_count = 0

    for page in rows:
        page_id = _page_id(page)
        if not page_id or _text(page.get("status"), 24).lower() == "archived":
            continue
        original = [_text(raw, 80) for raw in page.get("links") or [] if _text(raw, 80)]
        cleaned: list[str] = []
        seen: set[str] = set()
        for link_id in original:
            if link_id == page_id:
                reasons["self_link"] += 1
                continue
            if link_id in seen:
                reasons["duplicate_link"] += 1
                continue
            seen.add(link_id)
            target = by_id.get(link_id)
            if target is None:
                reasons["dangling_link"] += 1
                continue
            if page.get("kind") == "source_digest" and target.get("kind") == "source_digest":
                reasons["source_provenance_edge"] += 1
                continue
            if _is_stale_archive(target):
                reasons["stale_archived_target"] += 1
                continue
            if _text(target.get("status"), 24).lower() == "archived" and not _is_merge_archive(target):
                reasons["archived_target"] += 1
                continue
            cleaned.append(link_id)
        edge_count += len(cleaned)
        if cleaned != _normalized_links(page) or len(cleaned) != len(original):
            updated = dict(page)
            updated["links"] = cleaned
            updates.append(updated)

    candidate_count = sum(reasons.values())
    return {
        "ok": True,
        "page_count": len(rows),
        "active_page_count": sum(1 for page in rows if _text(page.get("status"), 24).lower() != "archived"),
        "candidate_count": candidate_count,
        "updated_count": len(updates),
        "edge_count_after": edge_count,
        "by_reason": dict(sorted(reasons.items())),
        "updates": updates,
    }


def apply(pages: Iterable[dict] | None = None, *, dry_run: bool = True) -> dict:
    result = plan(pages)
    updates = result.pop("updates")
    if not dry_run and updates:
        wiki.batch_upsert_pages(updates)
        result["applied"] = True
    else:
        result["applied"] = False
    result["dry_run"] = bool(dry_run)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove safe low-value wiki graph edges.")
    parser.add_argument("--apply", action="store_true", help="persist the cleanup plan")
    args = parser.parse_args(argv)
    result = apply(dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
