from __future__ import annotations

import json
import hashlib
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Iterable

from . import shared_memory, storage


WIKI_TAG = "wiki"
WIKI_SURFACE = "wiki"
VALID_STATUSES = ("draft", "reviewed", "stable", "archived")
VALID_KINDS = ("note", "playbook", "decision", "risk", "concept")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object, limit: int = 2200) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", _clean(text, 120).lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "wiki"


def _dedupe_texts(values: Iterable[object], *, limit: int = 12, item_limit: int = 60) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = _clean(raw, item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _page_id(title: str, surface: str, kind: str) -> str:
    key = "|".join([_clean(title, 160), _clean(surface, 60).lower(), _clean(kind, 40).lower()])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _status_from_tags(tags: list[str]) -> str:
    for tag in tags:
        clean = _clean(tag, 60).lower()
        if clean in VALID_STATUSES:
            return clean
        if clean.startswith("status:"):
            candidate = clean.split(":", 1)[1].strip()
            if candidate in VALID_STATUSES:
                return candidate
    return "draft"


def _surface_from_record(record: dict) -> str:
    source = record.get("source") or {}
    surface = _clean(source.get("surface") or source.get("screen") or "", 60).lower()
    if surface:
        return surface
    for tag in record.get("tags") or []:
        clean = _clean(tag, 60).lower()
        if clean.startswith("surface:"):
            return clean.split(":", 1)[1].strip() or WIKI_SURFACE
    return WIKI_SURFACE


def _kind_from_record(record: dict) -> str:
    artifacts = record.get("artifacts") or []
    for item in artifacts:
        clean = _clean(item, 80).lower()
        if clean.startswith("kind:"):
            candidate = clean.split(":", 1)[1].strip()
            if candidate in VALID_KINDS:
                return candidate
    return _clean(record.get("kind") or "note", 40).lower() or "note"


def _is_wiki_record(record: dict) -> bool:
    tags = [_clean(tag, 60).lower() for tag in (record.get("tags") or [])]
    if WIKI_TAG in tags:
        return True
    source = record.get("source") or {}
    surface = _clean(source.get("surface") or source.get("screen") or "", 60).lower()
    return surface == WIKI_SURFACE


def _record_to_page(record: dict) -> dict:
    tags = _dedupe_texts(record.get("tags") or [], limit=20, item_limit=60)
    summary = _clean(record.get("summary") or "", 2400)
    decisions = _dedupe_texts(record.get("decisions") or [], limit=8, item_limit=280)
    open_questions = _dedupe_texts(record.get("openQuestions") or [], limit=8, item_limit=280)
    messages = record.get("messages") or []
    source = record.get("source") or {}
    body_parts = []
    body_text = _clean(record.get("body") or "", 6000)
    if body_text:
        body_parts.append(body_text)
    elif summary:
        body_parts.append(summary)
    if decisions:
        body_parts.append("핵심 정리\n- " + "\n- ".join(decisions))
    if open_questions:
        body_parts.append("열린 질문\n- " + "\n- ".join(open_questions))
    if messages:
        msg_lines = []
        for msg in messages[:4]:
            role = _clean((msg or {}).get("role") or "", 32)
            text = _clean((msg or {}).get("text") or "", 260)
            if text:
                msg_lines.append(f"{role}: {text}")
        if msg_lines:
            body_parts.append("대화 발췌\n- " + "\n- ".join(msg_lines))
    return {
        "id": record.get("id"),
        "title": _clean(record.get("title") or "위키 페이지", 160),
        "slug": _slugify(record.get("title") or "위키 페이지"),
        "summary": summary,
        "body": "\n\n".join(part for part in body_parts if part).strip(),
        "tags": tags,
        "status": _status_from_tags(tags),
        "surface": _surface_from_record(record),
        "kind": _kind_from_record(record),
        "confidence": float(record.get("confidence") or source.get("confidence") or 0.5),
        "created_at": record.get("createdAt") or "",
        "updated_at": record.get("updatedAt") or record.get("createdAt") or "",
        "source": source,
        "source_refs": _dedupe_texts(record.get("artifacts") or [], limit=12, item_limit=120),
        "decisions": decisions,
        "openQuestions": open_questions,
        "messages": messages,
        "snippet": summary[:260] if summary else "",
        "raw": record,
    }


def _candidate_score(record: dict, query: str, surface: str, status: str) -> int:
    page = _record_to_page(record)
    haystack = " ".join(
        [
            page["title"],
            page["summary"],
            " ".join(page["tags"]),
            " ".join(page["decisions"]),
            " ".join(page["openQuestions"]),
            " ".join((msg or {}).get("text") or "" for msg in page.get("messages") or []),
        ]
    ).lower()
    score = 0
    for token in _tokens(query):
        if token in haystack:
            score += 4 if len(token) > 3 else 2
    if surface and surface != "all" and page["surface"] == surface.lower():
        score += 4
    if status and status != "all" and page["status"] == status.lower():
        score += 4
    if page["status"] == "stable":
        score += 2
    elif page["status"] == "reviewed":
        score += 1
    try:
        updated = datetime.fromisoformat(str(page["updated_at"]).replace("Z", "+00:00"))
        score += min(3, max(0, int((datetime.now(timezone.utc) - updated).days < 30)))
    except Exception:
        pass
    return score


def _tokens(text: str) -> set[str]:
    text = _clean(text, 600).lower()
    return {
        token
        for token in re.findall(r"[0-9a-zA-Z가-힣_.$+-]{2,}", text)
        if token not in {"그리고", "그러면", "어떻게", "지금", "the", "and", "for", "with", "about"}
    }


def list_pages(*, query: str = "", surface: str = "all", status: str = "all", limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit or 20), 400))
    try:
        rows = shared_memory.list_records(limit=400)
    except Exception:
        rows = []
    pages = [row for row in rows if _is_wiki_record(row)]
    if not pages:
        return []
    scored: list[tuple[int, int, dict]] = []
    for idx, row in enumerate(pages):
        page = _record_to_page(row)
        if status and status != "all" and page["status"] != status.lower():
            continue
        score = _candidate_score(row, query, surface, status)
        scored.append((score, -idx, page))
    if not scored:
        scored = [(0, -idx, _record_to_page(row)) for idx, row in enumerate(pages)]
    scored.sort(key=lambda item: (item[0], item[1], item[2].get("updated_at", "")), reverse=True)
    return [page for _score, _idx, page in scored[:limit]]


def get_page(page_id: str) -> dict | None:
    page_id = _clean(page_id, 80)
    if not page_id:
        return None
    for row in shared_memory.list_records(limit=400):
        if row.get("id") == page_id and _is_wiki_record(row):
            return _record_to_page(row)
    return None
