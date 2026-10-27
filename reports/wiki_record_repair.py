#!/usr/bin/env python3
"""Repair duplicate wiki record IDs without losing any knowledge cards.

Older distillation runs could reuse a previous ``last_result_id`` when two
source pages shared a collision ID.  JSONL kept both rows, but QMD exported
only one file per ID.  This module gives every colliding row a deterministic
ID, keeps the first row's ID for backward compatibility, and restores source
page links/state when the row identifies its source page.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import re
from collections.abc import Iterable

from agent_console import wiki


_TICKER_TITLE = re.compile(r"종목:([^·]+)")


def _text(value: object, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _fingerprint(page: dict) -> str:
    value = {
        key: page.get(key)
        for key in (
            "title", "summary", "body", "surface", "kind", "status", "tags",
            "source_refs", "links", "evidence_ids", "conflicting_evidence_ids",
        )
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _source_ids(page: dict, pages_by_id: dict[str, dict]) -> list[str]:
    ids: list[str] = []
    for raw in [*(page.get("links") or []), *[
        str(ref)[5:] for ref in (page.get("source_refs") or []) if str(ref).startswith("wiki:")
    ]]:
        value = _text(raw, 100)
        if value and (pages_by_id.get(value) or {}).get("kind") == "source_digest" and value not in ids:
            ids.append(value)
    return ids


def _primary_source_id(page: dict, pages_by_id: dict[str, dict]) -> str:
    candidates = _source_ids(page, pages_by_id)
    title_match = _TICKER_TITLE.search(_text(page.get("title"), 240))
    if title_match:
        ticker = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", title_match.group(1).strip().lower()).strip("-")
        expected = f"source-ticker-{ticker}"
        if expected in candidates:
            return expected
    return candidates[0] if candidates else ""


def _new_id(old_id: str, page: dict, occurrence: int, used: set[str]) -> str:
    seed = f"{old_id}|{occurrence}|{_fingerprint(page)}"
    base = "distill-repair-" + hashlib.sha256(seed.encode()).hexdigest()[:20]
    candidate = base
    suffix = 1
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _replace_link(links: list[object], old_id: str, new_id: str) -> list[str]:
    result: list[str] = []
    for raw in links:
        value = _text(raw, 100)
        if not value:
            continue
        value = new_id if value == old_id else value
        if value not in result:
            result.append(value)
    if new_id not in result:
        result.append(new_id)
    return result


def plan(pages: Iterable[dict] | None = None) -> dict:
    rows = [dict(page) for page in (pages if pages is not None else wiki._all_wiki_pages()) if isinstance(page, dict)]
    groups: dict[str, list[dict]] = defaultdict(list)
    for page in rows:
        page_id = _text(page.get("id"), 100)
        if page_id:
            groups[page_id].append(page)
    collisions = {page_id: group for page_id, group in groups.items() if len(group) > 1}
    if not collisions:
        return {
            "ok": True,
            "page_count": len(rows),
            "collision_group_count": 0,
            "collision_row_count": 0,
            "renamed_count": 0,
            "delete_ids": [],
            "upserts": [],
        }

    pages_by_id = {str(page.get("id")): page for page in rows if page.get("id")}
    used = set(pages_by_id)
    source_owner: dict[tuple[str, str], str] = {}
    upserts_by_id: dict[str, dict] = {}
    delete_ids = sorted(collisions)
    renamed_count = 0

    for old_id, group in sorted(collisions.items()):
        for occurrence, page in enumerate(group):
            new_id = old_id if occurrence == 0 else _new_id(old_id, page, occurrence, used)
            used.add(new_id)
            if new_id != old_id:
                renamed_count += 1
            updated = dict(page)
            updated["id"] = new_id
            upserts_by_id[new_id] = updated
            primary = _primary_source_id(page, pages_by_id)
            if primary and (primary, old_id) not in source_owner:
                source_owner[(primary, old_id)] = new_id

    # The source page is the only place where an ambiguous old ID can be
    # disambiguated safely.  Other inbound links keep the first ID as the
    # compatibility anchor instead of being guessed at.
    for page in rows:
        page_id = _text(page.get("id"), 100)
        if page_id not in pages_by_id or (pages_by_id[page_id].get("kind") != "source_digest"):
            continue
        updated = dict(page)
        changed = False
        state = dict(page.get("distillation_state") or {})
        for old_id in delete_ids:
            if old_id not in (page.get("links") or []) and state.get("last_result_id") != old_id:
                continue
            target = source_owner.get((page_id, old_id), old_id)
            if target != old_id:
                updated["links"] = _replace_link(updated.get("links") or [], old_id, target)
                if state.get("last_result_id") == old_id:
                    state["last_result_id"] = target
                changed = True
        if changed:
            if state:
                updated["distillation_state"] = state
            upserts_by_id[page_id] = updated

    upserts = list(upserts_by_id.values())
    return {
        "ok": True,
        "page_count": len(rows),
        "collision_group_count": len(collisions),
        "collision_row_count": sum(len(group) for group in collisions.values()),
        "renamed_count": renamed_count,
        "delete_ids": delete_ids,
        "upserts": upserts,
    }


def apply(pages: Iterable[dict] | None = None, *, dry_run: bool = True) -> dict:
    result = plan(pages)
    upserts = result.pop("upserts")
    delete_ids = result["delete_ids"]
    if not dry_run and upserts:
        saved = wiki.batch_replace_pages(upserts, deletes=delete_ids)
        result["saved_count"] = len(saved)
    else:
        result["saved_count"] = 0
    result["dry_run"] = bool(dry_run)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair duplicate wiki record IDs safely.")
    parser.add_argument("--apply", action="store_true", help="persist the repair plan")
    args = parser.parse_args(argv)
    print(json.dumps(apply(dry_run=not args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
