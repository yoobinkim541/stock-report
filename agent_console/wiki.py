from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from . import shared_memory

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
    body_parts = [summary]
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
            page["body"],
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
        score += 1 if (datetime.now(timezone.utc) - updated).days < 30 else 0
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
    limit = max(1, min(int(limit or 20), 100))
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


def delete_page(page_id: str) -> bool:
    page_id = _clean(page_id, 80)
    if not page_id:
        return False
    try:
        return bool(shared_memory.delete_record(page_id))
    except Exception:
        return False


def upsert_page(payload: dict) -> dict:
    payload = dict(payload or {})
    title = _clean(payload.get("title") or "위키 페이지", 160)
    surface = _clean(payload.get("surface") or WIKI_SURFACE, 60).lower() or WIKI_SURFACE
    kind = _clean(payload.get("kind") or "note", 40).lower() or "note"
    status = _clean(payload.get("status") or "draft", 40).lower() or "draft"
    if status not in VALID_STATUSES:
        status = "draft"
    if kind not in VALID_KINDS:
        kind = "note"
    page_id = _clean(payload.get("id") or _page_id(title, surface, kind), 80)
    tags = _dedupe_texts([
        WIKI_TAG,
        surface,
        kind,
        status,
        *(payload.get("tags") or []),
    ], limit=20, item_limit=60)
    source_refs = _dedupe_texts(payload.get("source_refs") or payload.get("sourceRefs") or [], limit=12, item_limit=180)
    summary = _clean(payload.get("summary") or payload.get("body") or "", 2400)
    body = _clean(payload.get("body") or summary, 6000)
    decisions = _dedupe_texts(payload.get("decisions") or [], limit=8, item_limit=280)
    open_questions = _dedupe_texts(payload.get("openQuestions") or payload.get("open_questions") or [], limit=8, item_limit=280)
    messages = payload.get("messages") or []
    record = {
        "id": page_id,
        "createdAt": _clean(payload.get("createdAt") or _now(), 80),
        "updatedAt": _now(),
        "title": title,
        "summary": summary,
        "body": body,
        "tags": tags,
        "decisions": decisions,
        "openQuestions": open_questions,
        "artifacts": [f"surface:{surface}", f"kind:{kind}", *source_refs],
        "messages": [
            {
                "role": _clean((msg or {}).get("role") or "user", 32),
                "text": _clean((msg or {}).get("text") or "", 2200),
                "createdAt": _clean((msg or {}).get("createdAt") or _now(), 80),
            }
            for msg in messages
            if isinstance(msg, dict) and _clean((msg or {}).get("text") or "", 2200)
        ][:8],
        "source": {
            "app": "stock-report",
            "surface": WIKI_SURFACE,
            "screen": WIKI_SURFACE,
            "provider": "codex-cli",
            "writer": "codex-cli",
            "confidence": float(payload.get("confidence") or 0.5),
        },
        "contextPacket": payload.get("contextPacket") if isinstance(payload.get("contextPacket"), dict) else None,
    }
    existing = get_page(page_id)
    if existing:
        delete_page(page_id)
        record["createdAt"] = existing.get("created_at") or record["createdAt"]
    shared_memory.append_record(record)
    return get_page(page_id) or _record_to_page(record)


def capture_from_chat(
    question: str,
    answer: str,
    *,
    surface: str = "market",
    title: str | None = None,
    status: str = "draft",
    kind: str = "playbook",
    tags: Iterable[str] | None = None,
    source_refs: Iterable[str] | None = None,
) -> dict:
    question = _clean(question, 1200)
    answer = _clean(answer, 4000)
    title = _clean(title or question or answer[:80] or "위키 페이지", 160)
    summary = answer[:2400] or question[:2400]
    return upsert_page(
        {
            "title": title,
            "summary": summary,
            "body": answer or summary,
            "surface": surface,
            "kind": kind,
            "status": status,
            "tags": [*(tags or []), surface, "conversation"],
            "source_refs": list(source_refs or []),
            "messages": [
                {"role": "user", "text": question},
                {"role": "assistant", "text": answer},
            ],
            "decisions": _extract_decisions(answer),
            "openQuestions": _extract_questions(answer),
            "confidence": 0.7,
        }
    )


def capture_from_conversation(conversation: list[dict], *, surface: str = "market", status: str = "draft") -> dict | None:
    question = ""
    answer = ""
    for row in conversation:
        role = _clean(row.get("role") or "", 32).lower()
        text = _clean(row.get("message") or row.get("content") or "", 4000)
        if role == "user" and text:
            question = text
        elif role == "assistant" and text and question:
            answer = text
    if not question or not answer:
        return None
    return capture_from_chat(question, answer, surface=surface, status=status)


def stats() -> dict:
    pages = list_pages(limit=400)
    status_counts = Counter(page.get("status") or "draft" for page in pages)
    surface_counts = Counter(page.get("surface") or "wiki" for page in pages)
    kind_counts = Counter(page.get("kind") or "note" for page in pages)
    return {
        "total": len(pages),
        "status_counts": dict(status_counts),
        "surface_counts": dict(surface_counts),
        "kind_counts": dict(kind_counts),
        "latest": pages[0] if pages else None,
    }


def build_context_section(query: str = "", *, surface: str = "market", limit: int = 4) -> str:
    pages = list_pages(query=query, surface=surface, limit=limit)
    if not pages:
        return ""
    lines = [
        "[위키 지식]",
        "아래 항목은 shared-memory에서 승격한 정리 카드다. 현재 사용자 질문과 화면 컨텍스트가 우선한다.",
        "",
    ]
    for idx, page in enumerate(pages, start=1):
        lines.append(f"{idx}. {page['title']} ({page.get('status', 'draft')} · {page.get('surface', 'wiki')})")
        if page.get("summary"):
            lines.append(f"   요약: {page['summary'][:260]}")
        if page.get("tags"):
            lines.append(f"   태그: {', '.join(page['tags'][:8])}")
    return "\n".join(lines)


def _extract_decisions(text: str) -> list[str]:
    lines = []
    for raw in _clean(text, 3000).splitlines():
        stripped = raw.strip().lstrip("-•*").strip()
        if not stripped:
            continue
        if len(stripped) < 12:
            continue
        lines.append(stripped)
        if len(lines) >= 6:
            break
    return lines


def _extract_questions(text: str) -> list[str]:
    out: list[str] = []
    for raw in _clean(text, 3000).splitlines():
        stripped = raw.strip()
        if "?" in stripped or stripped.endswith("다?") or stripped.endswith("까"):
            out.append(stripped[:220])
        if len(out) >= 4:
            break
    return out
