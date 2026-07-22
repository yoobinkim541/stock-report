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


def stats() -> dict:
    rows = [row for row in shared_memory.list_records(limit=400) if _is_wiki_record(row)]
    status_counts = Counter()
    kind_counts = Counter()
    surface_counts = Counter()
    latest: dict | None = None
    for row in rows:
        page = _record_to_page(row)
        status_counts[page.get("status", "draft")] += 1
        kind_counts[page.get("kind", "note")] += 1
        surface_counts[page.get("surface", WIKI_SURFACE)] += 1
        if not latest:
            latest = page
            continue
        latest_at = str(latest.get("updated_at") or latest.get("created_at") or "")
        page_at = str(page.get("updated_at") or page.get("created_at") or "")
        if page_at > latest_at:
            latest = page
    return {
        "total": len(rows),
        "status_counts": dict(status_counts),
        "kind_counts": dict(kind_counts),
        "surface_counts": dict(surface_counts),
        "latest": latest or {},
    }


def upsert_page(page: dict) -> dict:
    page = dict(page or {})
    title = _clean(page.get("title") or "위키 페이지", 160)
    surface = _clean(page.get("surface") or WIKI_SURFACE, 60).lower() or WIKI_SURFACE
    kind = _clean(page.get("kind") or "note", 40).lower()
    if kind not in VALID_KINDS:
        kind = "note"
    status = _clean(page.get("status") or "draft", 24).lower()
    if status not in VALID_STATUSES:
        status = "draft"
    page_id = _clean(page.get("id") or _page_id(title, surface, kind), 80)

    existing = get_page(page_id) or {}
    created_at = _clean(existing.get("created_at") or page.get("created_at") or _now(), 80)
    updated_at = _clean(page.get("updated_at") or _now(), 80)
    tags = _dedupe_texts([*([WIKI_TAG, surface, kind, status]), *(page.get("tags") or [])], limit=20, item_limit=60)
    record = {
        "id": page_id,
        "title": title,
        "summary": _clean(page.get("summary") or "", 2400),
        "body": _clean(page.get("body") or "", 6000),
        "tags": tags,
        "artifacts": _dedupe_texts(page.get("source_refs") or [], limit=12, item_limit=120),
        "messages": page.get("messages") or [],
        "decisions": _dedupe_texts(page.get("decisions") or [], limit=8, item_limit=280),
        "openQuestions": _dedupe_texts(page.get("openQuestions") or [], limit=8, item_limit=280),
        "confidence": float(page.get("confidence") or existing.get("confidence") or 0.5),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "kind": kind,
        "source": {
            "surface": surface,
            "screen": surface,
            "provider": "codex-cli",
            "providerLabel": "Codex CLI",
            "writer": "codex-cli",
        },
    }
    if existing and existing.get("id"):
        shared_memory.delete_record(page_id)
    saved = shared_memory.append_record(record)
    return _record_to_page(saved)


def delete_page(page_id: str) -> bool:
    page_id = _clean(page_id, 80)
    if not page_id:
        return False
    return shared_memory.delete_record(page_id)


def capture_from_chat(question: str, answer: str, *, surface: str = WIKI_SURFACE,
                      title: str | None = None, status: str = "draft",
                      kind: str = "playbook", tags: list[str] | None = None,
                      source_refs: list[str] | None = None,
                      confidence: float = 0.7) -> dict:
    title = _clean(title or question or "대화 위키", 160)
    body = "\n\n".join(
        part for part in [
            f"Q. {_clean(question, 2400)}" if question else "",
            f"A. {_clean(answer, 6000)}" if answer else "",
        ]
        if part
    )
    return upsert_page(
        {
            "title": title,
            "surface": surface,
            "kind": kind if kind in VALID_KINDS else "playbook",
            "status": status if status in VALID_STATUSES else "draft",
            "tags": tags or ["conversation"],
            "summary": _clean(answer or question or title, 2400),
            "body": body,
            "source_refs": source_refs or [],
            "confidence": confidence,
        }
    )


def build_context_section(*, query: str = "", surface: str = WIKI_SURFACE, limit: int = 4,
                          status: str = "all") -> str:
    pages = list_pages(query=query, surface=surface, status=status, limit=limit)
    if not pages:
        return ""
    lines = ["[AI 위키]"]
    for idx, page in enumerate(pages, start=1):
        header = f"{idx}. {page.get('title', '위키 페이지')}"
        meta = " · ".join(
            item for item in [
                page.get("surface", WIKI_SURFACE),
                page.get("kind", "note"),
                page.get("status", "draft"),
            ]
            if item
        )
        lines.append(f"{header} ({meta})")
        if page.get("summary"):
            lines.append(f"- 요약: {page['summary']}")
        if page.get("body"):
            lines.append(f"- 본문: {page['body'][:800]}")
        if page.get("tags"):
            lines.append(f"- 태그: {', '.join(page['tags'][:8])}")
    return "\n".join(lines).strip()
