from __future__ import annotations

import json
import os
import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from . import qmd_search, shared_memory, storage


WIKI_TAG = "wiki"
WIKI_SURFACE = "wiki"
VALID_STATUSES = ("draft", "reviewed", "stable", "archived")
VALID_KINDS = ("note", "playbook", "decision", "risk", "concept", "source_digest")
MAX_LINKS = 12


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


def _clean_links(values: Iterable[object], *, self_id: str = "", limit: int = MAX_LINKS) -> list[str]:
    filtered = [v for v in (values or []) if _clean(v, 80) != self_id]
    return _dedupe_texts(filtered, limit=limit, item_limit=80)


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


def has_non_conversation_source_refs(page_or_refs: object) -> bool:
    refs = page_or_refs
    if isinstance(page_or_refs, dict):
        refs = page_or_refs.get("source_refs") or page_or_refs.get("artifacts") or []
    for raw in refs or []:
        ref = _clean(raw, 300).lower()
        if not ref:
            continue
        if ref.startswith("conversation:") or ref.startswith("chat:"):
            continue
        return True
    return False


def verification_status_for(source_refs: list[str] | tuple[str, ...] | None) -> str:
    return "source-backed" if has_non_conversation_source_refs(source_refs or []) else "unverified"


def trust_warnings_for(status: str, source_refs: list[str] | tuple[str, ...] | None) -> list[str]:
    verification = verification_status_for(source_refs)
    if verification == "source-backed":
        return []
    if status in {"reviewed", "stable"}:
        return ["원문 출처 없음: conversation-only 페이지는 reviewed/stable 근거로 쓰지 않습니다."]
    return ["원문 출처 없음: 대화 기반 draft로만 참고합니다."]


def normalize_trust_status(status: str, source_refs: list[str] | tuple[str, ...] | None) -> str:
    status = _clean(status or "draft", 24).lower()
    if status not in VALID_STATUSES:
        status = "draft"
    if status in {"reviewed", "stable"} and not has_non_conversation_source_refs(source_refs or []):
        return "draft"
    return status


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


def _wiki_records() -> list[dict]:
    try:
        rows = shared_memory.all_records()
    except Exception:
        rows = []
    return [row for row in rows if _is_wiki_record(row)]


def _backlink_index(records: list[dict]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for row in records:
        row_id = _clean(row.get("id"), 80)
        if not row_id:
            continue
        for target_id in _clean_links(row.get("links") or [], self_id=row_id):
            index[target_id].append(row_id)
    return index


def _apply_backlinks(pages: list[dict], records: list[dict]) -> list[dict]:
    index = _backlink_index(records)
    for page in pages:
        page_id = _clean(page.get("id"), 80)
        page["backlinks"] = _dedupe_texts(index.get(page_id, []), limit=MAX_LINKS, item_limit=80)
    return pages


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
    source_refs = _dedupe_texts(record.get("artifacts") or [], limit=12, item_limit=120)
    status = normalize_trust_status(_status_from_tags(tags), source_refs)
    warnings = trust_warnings_for(status, source_refs)
    return {
        "id": record.get("id"),
        "title": _clean(record.get("title") or "위키 페이지", 160),
        "slug": _slugify(record.get("title") or "위키 페이지"),
        "summary": summary,
        "body": "\n\n".join(part for part in body_parts if part).strip(),
        "tags": tags,
        "status": status,
        "verification_status": verification_status_for(source_refs),
        "trust_warnings": warnings,
        "surface": _surface_from_record(record),
        "kind": _kind_from_record(record),
        "confidence": float(record.get("confidence") or source.get("confidence") or 0.5),
        "created_at": record.get("createdAt") or "",
        "updated_at": record.get("updatedAt") or record.get("createdAt") or "",
        "useCount": int(record.get("useCount") or 0),
        "lastUsedAt": record.get("lastUsedAt") or "",
        "lastQuery": record.get("lastQuery") or "",
        "source": source,
        "source_refs": source_refs,
        "links": _clean_links(record.get("links") or [], self_id=_clean(record.get("id"), 80)),
        "backlinks": [],
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
    query = _clean(query, 600)
    surface = _clean(surface or "all", 60).lower() or "all"
    status = _clean(status or "all", 40).lower() or "all"
    records = _wiki_records()
    if not records:
        return []
    fallback = _fallback_ranked_pages(records, query=query, surface=surface, status=status, limit=limit)
    qmd_pages = _qmd_ranked_pages(records, query=query, surface=surface, status=status, limit=limit)
    if not qmd_pages:
        return _apply_backlinks(fallback, records)
    merged: list[dict] = []
    seen: set[str] = set()
    for page in [*qmd_pages, *fallback]:
        page_id = _clean(page.get("id"), 120)
        if page_id and page_id in seen:
            continue
        if page_id:
            seen.add(page_id)
        merged.append(page)
        if len(merged) >= limit:
            break
    return _apply_backlinks(merged, records)


def _fallback_ranked_pages(records: list[dict], *, query: str, surface: str, status: str, limit: int) -> list[dict]:
    scored: list[tuple[int, int, dict]] = []
    for idx, row in enumerate(records):
        page = _record_to_page(row)
        if status and status != "all" and page["status"] != status.lower():
            continue
        score = _candidate_score(row, query, surface, status)
        scored.append((score, -idx, page))
    if not scored:
        scored = [(0, -idx, _record_to_page(row)) for idx, row in enumerate(records)]
    scored.sort(key=lambda item: (item[0], item[1], item[2].get("updated_at", "")), reverse=True)
    return [page for _score, _idx, page in scored[:limit]]


def _qmd_ranked_pages(records: list[dict], *, query: str, surface: str, status: str, limit: int) -> list[dict]:
    if not query:
        return []
    try:
        if not getattr(qmd_search, "enabled", lambda: True)():
            return []
        qmd_status = getattr(qmd_search, "status", lambda: {"installed": True})()
        if isinstance(qmd_status, dict) and qmd_status.get("installed") is False:
            return []
    except Exception:
        return []
    source_pages = [_record_to_page(row) for row in records]
    try:
        qmd_search.export_pages(source_pages)
    except Exception:
        pass
    try:
        hits = qmd_search.search(query, limit=limit, surface=surface, status=status)
    except Exception:
        return []
    if not hits:
        return []
    by_id = {_clean(page.get("id"), 120): page for page in source_pages if page.get("id")}
    out: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        page = _page_from_qmd_hit(hit, by_id=by_id, surface=surface, status=status)
        if not page:
            continue
        page_id = _clean(page.get("id"), 120)
        if page_id and page_id in seen:
            continue
        if page_id:
            seen.add(page_id)
        out.append(page)
        if len(out) >= limit:
            break
    return out


def _page_from_qmd_hit(hit: dict, *, by_id: dict[str, dict], surface: str, status: str) -> dict | None:
    if not isinstance(hit, dict):
        return None
    page_id = _clean(hit.get("page_id") or hit.get("id"), 120)
    source = by_id.get(page_id)
    if not source:
        return None
    page = dict(source)
    if status and status != "all" and page.get("status") != status:
        return None
    if surface and surface != "all" and page.get("surface") != surface:
        return None
    page["search_provider"] = "qmd"
    page["search_score"] = hit.get("score")
    if hit.get("summary"):
        page["qmd_snippet"] = _clean(hit.get("summary"), 500)
    return page


def get_page(page_id: str) -> dict | None:
    page_id = _clean(page_id, 80)
    if not page_id:
        return None
    records = _wiki_records()
    for row in records:
        if row.get("id") == page_id:
            page = _record_to_page(row)
            return _apply_backlinks([page], records)[0]
    return None


def stats() -> dict:
    rows = _wiki_records()
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


def wiki_artifacts_dir() -> Path:
    override = os.getenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(shared_memory.shared_memory_dir()) / "llm-wiki"


def search_health() -> dict:
    try:
        qmd = qmd_search.health() if hasattr(qmd_search, "health") else qmd_search.status()
    except Exception:
        qmd = {"enabled": False, "installed": False}
    qmd_available = bool(qmd.get("enabled") and qmd.get("installed"))
    return {
        "provider": "qmd" if qmd_available else "fallback",
        "qmd": qmd,
        "fallback_available": True,
    }


def _lint_relational_issues(pages: list[dict]) -> list[dict]:
    issues: list[dict] = []
    valid_pages = [page for page in pages or [] if isinstance(page, dict) and _clean(page.get("id") or "", 80)]

    for page in valid_pages:
        page_id = _clean(page.get("id"), 80)
        title = _clean(page.get("title") or "위키 페이지", 160)
        links = set(_clean_links(page.get("links") or [], self_id=page_id))
        backlinks = set(_clean_links(page.get("backlinks") or [], self_id=page_id))
        if not links and not backlinks:
            issues.append({
                "code": "orphan_page",
                "severity": "info",
                "page_id": page_id,
                "title": title,
                "message": "다른 페이지와 연결이 없습니다.",
            })

    ticker_index: dict[str, list[dict]] = defaultdict(list)
    ref_index: dict[str, list[dict]] = defaultdict(list)
    for page in valid_pages:
        for tag in page.get("tags") or []:
            clean_tag = _clean(tag, 60).lower()
            if clean_tag.startswith("ticker:"):
                ticker_index[clean_tag].append(page)
        for ref in page.get("source_refs") or page.get("artifacts") or []:
            clean_ref = _clean(ref, 200)
            if clean_ref:
                ref_index[clean_ref].append(page)

    seen_pairs: set[tuple[str, str]] = set()
    for group in [*ticker_index.values(), *ref_index.values()]:
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                left, right = group[i], group[j]
                left_id = _clean(left.get("id"), 80)
                right_id = _clean(right.get("id"), 80)
                if not left_id or not right_id or left_id == right_id:
                    continue
                pair = tuple(sorted((left_id, right_id)))
                if pair in seen_pairs:
                    continue
                left_links = set(_clean_links(left.get("links") or [], self_id=left_id))
                right_links = set(_clean_links(right.get("links") or [], self_id=right_id))
                if right_id in left_links or left_id in right_links:
                    continue
                seen_pairs.add(pair)
                left_title = _clean(left.get("title") or "위키 페이지", 160)
                right_title = _clean(right.get("title") or "위키 페이지", 160)
                issues.append({
                    "code": "missing_cross_ref",
                    "severity": "warning",
                    "page_id": left_id,
                    "title": f"{left_title} / {right_title}",
                    "message": f"'{left_title}'와(과) '{right_title}'가 태그·출처를 공유하지만 서로 연결되어 있지 않습니다.",
                    "suggested": "merge",
                })
    return issues


def lint_pages(pages: list[dict] | None = None) -> dict:
    if pages is None:
        pages = list_pages(status="all", surface="all", limit=400)
    issues: list[dict] = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        page_id = _clean(page.get("id") or "", 80)
        title = _clean(page.get("title") or "위키 페이지", 160)
        status = _clean(page.get("status") or "draft", 40).lower()
        refs = page.get("source_refs") or page.get("artifacts") or []
        if status in {"reviewed", "stable"} and not has_non_conversation_source_refs(refs):
            issues.append({
                "code": "source_missing_for_promoted",
                "severity": "error",
                "page_id": page_id,
                "title": title,
                "message": "reviewed/stable 페이지에는 conversation 이외의 원문 출처가 필요합니다.",
            })
        open_questions = page.get("openQuestions") or page.get("open_questions") or []
        if open_questions:
            issues.append({
                "code": "open_questions_present",
                "severity": "info",
                "page_id": page_id,
                "title": title,
                "message": f"열린 질문 {len(open_questions)}건이 남아 있습니다.",
            })
        if not page.get("summary") and not page.get("body"):
            issues.append({
                "code": "empty_page",
                "severity": "warning",
                "page_id": page_id,
                "title": title,
                "message": "요약과 본문이 모두 비어 있습니다.",
            })
        if status != "archived":
            last_used = _last_used_or_created(page)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            if not last_used or last_used < cutoff:
                issues.append({
                    "code": "zero_usage",
                    "severity": "minor",
                    "page_id": page_id,
                    "title": title,
                    "message": "이 페이지가 30일간 사용되지 않았습니다. archived 또는 삭제를 고려하세요.",
                })
    issues.extend(_lint_relational_issues(pages or []))
    return {"ok": not issues, "issue_count": len(issues), "issues": issues}


def rebuild_artifacts() -> dict:
    pages = list_pages(status="all", surface="all", limit=400)
    out_dir = wiki_artifacts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    lint = lint_pages(pages)
    payloads = {
        "index.md": _render_index_md(pages),
        "log.md": _render_log_md(pages),
        "open-questions.md": _render_open_questions_md(pages),
        "lint.md": _render_lint_md(lint),
    }
    for name, body in payloads.items():
        (out_dir / name).write_text(body, encoding="utf-8")
    return {
        "ok": True,
        "dir": str(out_dir),
        "files": sorted(payloads),
        "page_count": len(pages),
        "lint": lint,
    }


def _render_index_md(pages: list[dict]) -> str:
    lines = ["# LLM Wiki Index", "", f"Generated: {_now()}", ""]
    active_pages = [page for page in pages if page.get("status") != "archived"]
    archived_pages = [page for page in pages if page.get("status") == "archived"]
    by_surface: dict[str, list[dict]] = {}
    for page in active_pages:
        by_surface.setdefault(page.get("surface") or WIKI_SURFACE, []).append(page)
    for surface in sorted(by_surface):
        lines += [f"## {surface}", ""]
        for page in sorted(by_surface[surface], key=lambda item: str(item.get("title") or "")):
            lines.append(_render_index_entry(page))
        lines.append("")
    if archived_pages:
        lines += ["<details>", "<summary>## Archived</summary>", ""]
        for page in sorted(archived_pages, key=lambda item: str(item.get("title") or "")):
            lines.append(_render_index_entry(page))
        lines += ["", "</details>", ""]
    return "\n".join(lines).strip() + "\n"


def _render_index_entry(page: dict) -> str:
    title = _clean(page.get("title") or "위키 페이지", 160)
    meta = " · ".join([
        _clean(page.get("kind") or "note", 40),
        _clean(page.get("status") or "draft", 40),
        _clean(page.get("verification_status") or "unverified", 40),
    ])
    summary = _clean(page.get("summary") or page.get("body") or "", 180)
    link_count = len({*(page.get("links") or []), *(page.get("backlinks") or [])})
    marker = f" [\U0001f517{link_count}]" if link_count else ""
    return f"- [[{title}]] ({meta}) — {summary}{marker}"


def _render_log_md(pages: list[dict]) -> str:
    lines = ["# LLM Wiki Log", ""]
    ordered = sorted(pages, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    for page in ordered:
        stamp = _clean(page.get("updated_at") or page.get("created_at") or "unknown", 80)
        title = _clean(page.get("title") or "위키 페이지", 160)
        lines += [
            f"## [{stamp}] {page.get('surface', WIKI_SURFACE)} | {title}",
            f"- status: {page.get('status', 'draft')} · verification: {page.get('verification_status', 'unverified')}",
        ]
        refs = [_display_ref(ref) for ref in (page.get("source_refs") or [])[:4]]
        if refs:
            lines.append("- sources: " + ", ".join(refs))
        lines.append("")
    if len(lines) == 2:
        lines.append("- No wiki pages yet.")
    return "\n".join(lines).strip() + "\n"


def _render_open_questions_md(pages: list[dict]) -> str:
    lines = ["# LLM Wiki Open Questions", ""]
    count = 0
    for page in pages:
        questions = page.get("openQuestions") or page.get("open_questions") or []
        for question in questions:
            count += 1
            lines.append(f"- **{_clean(page.get('title') or '위키 페이지', 120)}**: {_clean(question, 240)}")
    if not count:
        lines.append("- No open questions.")
    return "\n".join(lines).strip() + "\n"


def _render_lint_md(lint: dict) -> str:
    lines = ["# LLM Wiki Lint", "", f"ok: {bool(lint.get('ok'))}", f"issues: {lint.get('issue_count', 0)}", ""]
    issues = lint.get("issues") or []
    if not issues:
        lines.append("No blocking issues.")
    for issue in issues:
        lines.append(
            f"- `{issue.get('code')}` [{issue.get('severity')}] "
            f"{issue.get('title')}: {issue.get('message')}"
        )
    return "\n".join(lines).strip() + "\n"


def _display_ref(ref: object) -> str:
    text = _clean(ref, 200)
    if text.startswith(str(Path.home())) or text.startswith("/"):
        return Path(text).name or "local-file"
    return text


def upsert_page(page: dict) -> dict:
    page = dict(page or {})
    title = _clean(page.get("title") or "위키 페이지", 160)
    surface = _clean(page.get("surface") or WIKI_SURFACE, 60).lower() or WIKI_SURFACE
    kind = _clean(page.get("kind") or "note", 40).lower()
    if kind not in VALID_KINDS:
        kind = "note"
    source_refs = _dedupe_texts(page.get("source_refs") or [], limit=12, item_limit=120)
    status = normalize_trust_status(page.get("status") or "draft", source_refs)
    page_id = _clean(page.get("id") or _page_id(title, surface, kind), 80)
    links = _clean_links(page.get("links") or [], self_id=page_id)

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
        "artifacts": source_refs,
        "links": links,
        "messages": page.get("messages") or [],
        "decisions": _dedupe_texts(page.get("decisions") or [], limit=8, item_limit=280),
        "openQuestions": _dedupe_texts(page.get("openQuestions") or [], limit=8, item_limit=280),
        "confidence": float(page.get("confidence") or existing.get("confidence") or 0.5),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "kind": kind,
        "useCount": int(page.get("useCount") if page.get("useCount") is not None else existing.get("useCount") or 0),
        "lastUsedAt": _clean(page.get("lastUsedAt") or existing.get("lastUsedAt") or "", 80),
        "lastQuery": _clean(page.get("lastQuery") or existing.get("lastQuery") or "", 200),
        "source": {
            "surface": surface,
            "screen": surface,
            "provider": "codex-cli",
            "providerLabel": "Codex CLI",
            "writer": "codex-cli",
        },
    }
    saved = shared_memory.upsert_record(record)
    return _record_to_page(saved)


def track_page_usage(page_id: str, query: str) -> None:
    """페이지가 LLM 컨텍스트로 제공될 때 호출한다. useCount 증가, lastUsedAt/lastQuery 갱신."""
    page = get_page(page_id)
    if not page:
        return
    upsert_page({
        "id": page["id"],
        "title": page.get("title"),
        "summary": page.get("summary"),
        "body": page.get("body"),
        "surface": page.get("surface"),
        "kind": page.get("kind"),
        "status": page.get("status"),
        "tags": page.get("tags") or [],
        "source_refs": page.get("source_refs") or [],
        "links": page.get("links") or [],
        "messages": page.get("messages") or [],
        "decisions": page.get("decisions") or [],
        "openQuestions": page.get("openQuestions") or [],
        "confidence": page.get("confidence"),
        "created_at": page.get("created_at"),
        "useCount": (page.get("useCount") or 0) + 1,
        "lastUsedAt": _now(),
        "lastQuery": _clean(query, 200),
    })
    rebuild_artifacts()


def _last_used_or_created(page: dict) -> str:
    return page.get("lastUsedAt") or page.get("createdAt") or page.get("created_at") or ""


def list_unused_pages(days: int = 30) -> list[dict]:
    """지정된 일수 이상(또는 한 번도) 사용되지 않은 활성 페이지를 반환한다."""
    pages = list_pages(status="all", surface="all", limit=400)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    unused = []
    for page in pages:
        if page.get("status") == "archived":
            continue
        last_used = _last_used_or_created(page)
        if not last_used or last_used < cutoff:
            unused.append(page)
    return unused


def delete_page(page_id: str) -> bool:
    page_id = _clean(page_id, 80)
    if not page_id:
        return False
    return shared_memory.delete_record(page_id)


def _is_page_stale(page: dict, max_age_days: int = 30) -> bool:
    updated_str = page.get("updated_at") or page.get("updatedAt") or page.get("created_at") or page.get("createdAt") or ""
    if not updated_str:
        return True
    try:
        updated = datetime.fromisoformat(str(updated_str).replace("Z", "+00:00"))
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).days >= max_age_days


def list_stale_pages(max_age_days: int = 30) -> list[dict]:
    pages = list_pages(status="all", surface="all", limit=400)
    return [page for page in pages if page.get("status") != "archived" and _is_page_stale(page, max_age_days)]


def archive_stale_pages(max_age_days: int = 30, dry_run: bool = False, max_archive_days: int = 90) -> dict:
    pages = list_pages(status="all", surface="all", limit=400)
    to_archive = [page for page in pages if page.get("status") != "archived" and _is_page_stale(page, max_age_days)]
    archived_pages = [page for page in pages if page.get("status") == "archived"]
    to_delete = [page for page in archived_pages if _is_page_stale(page, max_archive_days)]
    stale_skipped = len([page for page in archived_pages if _is_page_stale(page, max_age_days)]) - len(to_delete)

    if not dry_run:
        for page in to_delete:
            delete_page(page["id"])
        for page in to_archive:
            upsert_page({
                "id": page["id"],
                "title": page.get("title"),
                "summary": page.get("summary"),
                "body": page.get("body"),
                "surface": page.get("surface"),
                "kind": page.get("kind"),
                "status": "archived",
                "tags": _dedupe_texts([*(page.get("tags") or []), "archived_reason:stale"], limit=20, item_limit=60),
                "source_refs": page.get("source_refs") or [],
                "links": page.get("links") or [],
                "messages": page.get("messages") or [],
                "decisions": page.get("decisions") or [],
                "openQuestions": page.get("openQuestions") or [],
                "confidence": page.get("confidence"),
            })
        if to_archive or to_delete:
            rebuild_artifacts()

    return {
        "archived": len(to_archive),
        "deleted": len(to_delete),
        "stale_skipped": max(0, stale_skipped),
        "total": len(pages),
        "dry_run": dry_run,
    }


def _merge_pages(source_ids: list[str], target_id: str, llm_synthesis: str) -> dict | None:
    target_id = _clean(target_id, 80)
    target = get_page(target_id)
    if not target:
        return None
    source_ids = [_clean(sid, 80) for sid in (source_ids or []) if _clean(sid, 80) and _clean(sid, 80) != target_id]
    sources = [page for sid in source_ids if (page := get_page(sid))]
    if not sources:
        return None

    body_parts = [target.get("body") or "", *[page.get("body") or "" for page in sources]]
    synthesis = _clean(llm_synthesis, 2400)
    if synthesis:
        body_parts.append(synthesis)
    merged_body = "\n\n".join(part for part in body_parts if part).strip()

    tags = _dedupe_texts([
        *(target.get("tags") or []),
        *[tag for page in sources for tag in (page.get("tags") or [])],
        *[f"merged_from:{page['id']}" for page in sources],
    ], limit=20, item_limit=60)
    source_refs = _dedupe_texts([
        *(target.get("source_refs") or []),
        *[ref for page in sources for ref in (page.get("source_refs") or [])],
    ], limit=12, item_limit=180)
    links = _clean_links([
        *(target.get("links") or []),
        *[link for page in sources for link in (page.get("links") or [])],
    ], self_id=target_id)

    merged_source_ids = [page["id"] for page in sources]
    for sid in merged_source_ids:
        delete_page(sid)

    upsert_page({
        "id": target_id,
        "title": target.get("title"),
        "summary": target.get("summary") or synthesis,
        "body": merged_body,
        "surface": target.get("surface"),
        "kind": target.get("kind"),
        "status": target.get("status"),
        "tags": tags,
        "source_refs": source_refs,
        "links": links,
        "messages": target.get("messages") or [],
        "decisions": target.get("decisions") or [],
        "openQuestions": target.get("openQuestions") or [],
        "confidence": target.get("confidence"),
    })

    return {"action": "merge", "target": target_id, "deleted": merged_source_ids}


def _split_page(source_id: str, new_titles: list[str], llm_bodies: list[str]) -> dict | None:
    source_id = _clean(source_id, 80)
    source = get_page(source_id)
    if not source:
        return None
    titles = [_clean(title, 160) for title in (new_titles or []) if _clean(title, 160)]
    if not titles:
        return None
    bodies = list(llm_bodies or [])

    new_pages = []
    for idx, title in enumerate(titles):
        body = _clean(bodies[idx] if idx < len(bodies) else source.get("body") or "", 6000)
        created = upsert_page({
            "title": title,
            "summary": body[:900],
            "body": body,
            "surface": source.get("surface"),
            "kind": source.get("kind"),
            "status": "draft",
            "tags": _dedupe_texts([*(source.get("tags") or []), f"split_from:{source_id}"], limit=20, item_limit=60),
            "source_refs": source.get("source_refs") or [],
        })
        new_pages.append(created)

    new_ids = [page["id"] for page in new_pages]
    for page in new_pages:
        other_ids = [pid for pid in new_ids if pid != page["id"]]
        upsert_page({
            "id": page["id"],
            "title": page.get("title"),
            "summary": page.get("summary"),
            "body": page.get("body"),
            "surface": page.get("surface"),
            "kind": page.get("kind"),
            "status": page.get("status"),
            "tags": page.get("tags"),
            "source_refs": page.get("source_refs"),
            "links": _clean_links([*(page.get("links") or []), *other_ids], self_id=page["id"]),
        })

    upsert_page({
        "id": source_id,
        "title": source.get("title"),
        "summary": source.get("summary"),
        "body": source.get("body"),
        "surface": source.get("surface"),
        "kind": source.get("kind"),
        "status": "archived",
        "tags": _dedupe_texts([
            *(source.get("tags") or []),
            *[f"split_into:{sid}" for sid in new_ids],
        ], limit=20, item_limit=60),
        "source_refs": source.get("source_refs") or [],
        "links": source.get("links") or [],
    })

    return {"action": "split", "source": source_id, "created": new_ids}


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


def _title_lookup_for(page_ids: set[str]) -> dict[str, str]:
    if not page_ids:
        return {}
    lookup: dict[str, str] = {}
    for row in _wiki_records():
        row_id = _clean(row.get("id"), 80)
        if row_id in page_ids:
            lookup[row_id] = _clean(row.get("title") or "위키 페이지", 160)
    return lookup


def build_context_section(*, query: str = "", surface: str = WIKI_SURFACE, limit: int = 4,
                          status: str = "all") -> str:
    pages = list_pages(query=query, surface=surface, status=status, limit=limit)
    if not pages:
        return ""
    related_ids = {
        rid
        for page in pages
        for rid in [*(page.get("links") or []), *(page.get("backlinks") or [])]
    }
    title_lookup = _title_lookup_for(related_ids)
    lines = ["[위키 지식]"]
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
        if page.get("search_provider"):
            search_line = f"- 검색: {page.get('search_provider')}"
            if page.get("search_score") is not None:
                search_line += f" (score={page.get('search_score')})"
            lines.append(search_line)
        if page.get("updated_at"):
            lines.append(f"- 갱신: {page.get('updated_at')}")
        if page.get("source_refs"):
            lines.append(f"- 출처: {', '.join(page['source_refs'][:4])}")
        lines.append(f"- 검증: {page.get('verification_status', 'unverified')}")
        for warning in page.get("trust_warnings") or []:
            lines.append(f"- 주의: {warning}")
        related_ids_for_page = _dedupe_texts(
            [*(page.get("links") or []), *(page.get("backlinks") or [])], limit=6, item_limit=80
        )
        related_titles = [title_lookup[rid] for rid in related_ids_for_page if rid in title_lookup]
        if related_titles:
            lines.append(f"- 관련: {', '.join(f'[[{t}]]' for t in related_titles)}")
        if page.get("tags"):
            lines.append(f"- 태그: {', '.join(page['tags'][:8])}")
    return "\n".join(lines).strip()

# ── 아래 함수들은 652d61d 잘림 사고로 유실됐다가 복구된 것들이다.
# agent_console/agent.py 가 auto_curate_from_chat 을 호출하는데, 호출부가
# try/except 로 감싸여 있어 사라진 동안 조용히 실패하고 있었다.

AUTO_CURATE_MIN_LENGTH = 40
AUTO_CURATE_MAX_LENGTH = 6000
AUTO_CURATE_MIN_SCORE = 5
_TRANSIENT_ACK_PATTERNS = (
    "진행해줘", "진행해봐", "진행해", "ㄱㄱ", "ok", "okay", "오케이", "좋아",
    "확인해봐", "해봐", "보여줘", "감사", "고마워", "알겠", "이해", "테스트 메세지",
)
_RULE_KEYWORDS = (
    "규칙", "기준", "조건", "정책", "가드레일", "예외", "검증", "체크",
    "원문", "본문", "raw", "body", "저장", "수집", "기억", "위키", "재사용",
    "편향", "bias", "학습", "메모리", "결정", "선택", "실패", "성공",
    "손실한도", "레버리지", "비중", "현금", "변동성", "포트폴리오", "mdd",
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


def auto_curate_from_chat(
    question: str,
    answer: str,
    *,
    surface: str = "market",
    llm: Callable[[str], str | None] | None = None,
    pack: dict | None = None,
    history: list[dict] | None = None,
) -> dict | None:
    """대화를 위키 카드로 자동 승격한다.

    재사용 가능한 규칙/결정/편향 교정만 올리고, 짧은 진행 확인/한 번성 응답은 건너뛴다.
    """
    question = _clean(question, AUTO_CURATE_MAX_LENGTH)
    answer = _clean(answer, AUTO_CURATE_MAX_LENGTH)
    surface = _clean(surface or WIKI_SURFACE, 60).lower() or WIKI_SURFACE
    if not question or not answer:
        return None
    if not _should_auto_curate(question, answer):
        return None
    if _recently_created_dedup(question, surface):
        return {"ok": False, "action": "skipped_dedup", "reason": "최근 24시간 내 유사한 질문으로 위키 페이지가 생성되어 중복을 건너뜁니다."}

    candidates = list_pages(query=question, surface=surface, limit=5)
    target = _best_candidate_page(question, surface, candidates)
    plan = None
    if llm is not None:
        try:
            prompt = _build_auto_curation_prompt(
                question=question,
                answer=answer,
                surface=surface,
                candidates=candidates,
                pack=pack or {},
                history=history or [],
            )
            plan = _parse_curation_plan(llm(prompt))
        except Exception:
            plan = None
    if not plan:
        plan = _heuristic_curation_plan(question, answer, surface=surface, target=target, candidates=candidates)
    if not plan:
        return None

    action = _clean(plan.get("action") or "create", 20).lower()
    if action not in {"create", "update", "skip", "delete", "merge", "split"}:
        action = "create"
    if action == "skip":
        return None
    plan_source = "llm" if llm is not None and plan.get("source") == "llm" else "heuristic"
    if action == "delete":
        target_id = _clean(plan.get("target_id") or (target.get("id") if target else ""), 80)
        if not target_id:
            return None
        deleted = delete_page(target_id)
        if not deleted:
            return None
        rebuild_artifacts()
        return {"ok": True, "action": "delete", "page_id": target_id, "source": plan_source}
    if action == "merge":
        target_id = _clean(plan.get("target_page_id") or "", 80)
        source_ids = [_clean(sid, 80) for sid in (plan.get("source_page_ids") or [])]
        synthesis = _clean(plan.get("body") or plan.get("summary") or "", 2400)
        merge_result = _merge_pages(source_ids, target_id, synthesis)
        if not merge_result:
            return None
        rebuild_artifacts()
        return {"ok": True, "source": plan_source, **merge_result}
    if action == "split":
        source_id = _clean(plan.get("source_page_id") or "", 80)
        new_titles = plan.get("new_titles") or []
        new_bodies = plan.get("new_bodies") or []
        split_result = _split_page(source_id, new_titles, new_bodies)
        if not split_result:
            return None
        rebuild_artifacts()
        return {"ok": True, "source": plan_source, **split_result}

    payload = _plan_to_page_payload(
        plan,
        question=question,
        answer=answer,
        surface=surface,
        target=target,
    )
    if not payload:
        return None

    saved = upsert_page(payload)
    return {
        "ok": True,
        "action": action,
        "source": "llm" if llm is not None and plan.get("source") == "llm" else "heuristic",
        "page": saved,
    }


def _should_auto_curate(question: str, answer: str) -> bool:
    text = f"{question}\n{answer}".lower()
    if len(question) < 10 or len(answer) < AUTO_CURATE_MIN_LENGTH:
        return False
    if any(pat in text for pat in _TRANSIENT_ACK_PATTERNS) and not any(k in text for k in _RULE_KEYWORDS):
        return False
    score = 0
    if len(answer) >= 180:
        score += 1
    if len(answer) >= 500:
        score += 1
    if text.count("\n") >= 2 or any(line.strip().startswith(("-", "*", "•", "1.", "2.", "3.")) for line in text.splitlines()):
        score += 2
    if any(k in text for k in _RULE_KEYWORDS):
        score += 2
    if any(k in question.lower() for k in ("정리", "기준", "규칙", "학습", "위키", "기억", "비교", "조건")):
        score += 1
    if any(k in text for k in ("예외", "검증", "재현", "실패", "성공", "원문", "본문", "저장", "수집")):
        score += 1
    return score >= AUTO_CURATE_MIN_SCORE


def _build_wiki_context_section() -> str:
    """LLM이 위키 전체 상태를 인지할 수 있도록 stats + lint 요약을 생성한다."""
    stats_data = stats()
    lint_data = lint_pages()
    status_counts = stats_data.get("status_counts", {})
    pages = list_pages(status="all", surface="all", limit=400)
    verification_counts = Counter(page.get("verification_status") for page in pages)

    lines = ["[현재 위키 상태]"]
    lines.append(f"- 전체 페이지: {stats_data.get('total', 0)}")
    active = sum(status_counts.get(s, 0) for s in ("draft", "reviewed", "stable"))
    lines.append(f"- 활성: {active}")
    lines.append(f"- Archived: {status_counts.get('archived', 0)}")
    lines.append(f"- 미검증(unverified): {verification_counts.get('unverified', 0)}")
    lines.append(f"- 검증됨(source-backed): {verification_counts.get('source-backed', 0)}")
    lines.append(f"- 미사용(30일+): {len(list_unused_pages(30))}")

    lint_issues = lint_data.get("issues", [])
    if lint_issues:
        lines.append(f"- 린트 이슈: {len(lint_issues)}개")
        for issue in lint_issues[:5]:
            lines.append(f"  - {issue.get('title', '?')}: {issue.get('code', '?')}")

    kind_counts = stats_data.get("kind_counts", {})
    if kind_counts:
        kinds = ", ".join(f"{k}: {c}" for k, c in sorted(kind_counts.items()))
        lines.append(f"- 유형: {kinds}")

    return "\n".join(lines)


def _build_auto_curation_prompt(
    *,
    question: str,
    answer: str,
    surface: str,
    candidates: list[dict],
    pack: dict,
    history: list[dict],
) -> str:
    lines = [
        "너는 stock-report AI 위키 정리기다.",
        _build_wiki_context_section(),
        "",
        "목표: 재사용 가능한 규칙, 결정, 저장/수집 원칙, 실패 교정만 하나의 위키 카드로 정리한다.",
        "짧은 진행 확인, 단발성 수다, 상태 보고, 확인 대답은 생성 금지다.",
        "반드시 JSON object만 출력한다. 마크다운, 설명문, 코드펜스는 금지한다.",
        "가능한 action 값은 create, update, skip, delete, merge, split 이다.",
        "update 를 고를 때는 target_id 를 기존 후보 페이지 id 로 지정한다.",
        "확신이 낮으면 status 는 draft, 중간이면 reviewed, 이미 안정적인 운영 규칙이면 stable 이다.",
        "필드: action, title, summary, body, kind, status, tags, source_refs, links, target_id, confidence, reason.",
        "관련 있는 기존 위키 후보가 있으면 해당 id 를 links 배열에 넣는다. 관련 없으면 links 는 빈 배열이다.",
        "action이 delete면 target_id(삭제할 기존 후보 id)와 reason만 있으면 된다.",
        "delete 판단 기준: 30일 이상 갱신 안 됨, 현재 시장 상황과 모순, 다른 페이지와 완전히 중복, 내용이 부실하거나 검증 불가능.",
        "action이 merge면 target_page_id(병합 대상), source_page_ids(흡수될 후보 id 목록), body(합성 요약), reason이 필요하다.",
        "action이 split이면 source_page_id(분할할 후보 id), new_titles(새 페이지 제목 목록), new_bodies(각 제목에 대응하는 본문 목록), reason이 필요하다.",
        f"surface: {surface}",
        "",
        "[사용자 질문]",
        question,
        "",
        "[모델 답변]",
        answer,
    ]
    if history:
        lines += ["", "[최근 대화 힌트]"]
        for row in history[-4:]:
            role = _clean(row.get("role") or "", 24)
            msg = _clean(row.get("message") or "", 180)
            if msg:
                lines.append(f"- {role}: {msg}")
    if pack.get("focus"):
        lines += ["", "[화면 초점]", *[f"- {item}" for item in pack.get("focus")[:4]]]
    if candidates:
        lines += ["", "[기존 위키 후보]"]
        for page in candidates[:5]:
            lines.append(
                f"- id={page.get('id')} | title={page.get('title')} | "
                f"status={page.get('status')} | kind={page.get('kind')} | "
                f"summary={_clean(page.get('summary') or '', 160)}"
            )
    lines += [
        "",
        "JSON 예시 (create/update/delete/merge/split):",
        '{"action":"create","title":"손실한도와 레버리지","summary":"...","body":"...","kind":"playbook","status":"reviewed","tags":["risk","portfolio"],"source_refs":["conversation:123"],"links":[],"target_id":"","confidence":0.86,"reason":"..."}',
        '{"action":"delete","target_id":"id-to-delete","reason":"..."}',
        '{"action":"merge","target_page_id":"id-to-merge-into","source_page_ids":["id-to-absorb"],"body":"...","reason":"..."}',
        '{"action":"split","source_page_id":"id-to-split","new_titles":["...","..."],"new_bodies":["...","..."],"reason":"..."}',
    ]
    return "\n".join(lines)


def _parse_curation_plan(text: str | None) -> dict | None:
    text = _clean(text or "", 8000)
    if not text:
        return None
    candidates = [text]
    code_blocks = re.findall(r"```(?:json)?\\s*(.*?)```", text, flags=re.S | re.I)
    candidates[:0] = [block.strip() for block in code_blocks if block.strip()]
    brace = re.search(r"\{.*\}", text, flags=re.S)
    if brace:
        candidates.insert(0, brace.group(0))
    for chunk in candidates:
        try:
            parsed = json.loads(chunk)
        except Exception:
            continue
        if isinstance(parsed, dict):
            parsed["source"] = "llm"
            return parsed
    return None


def _heuristic_curation_plan(
    question: str,
    answer: str,
    *,
    surface: str,
    target: dict | None = None,
    candidates: list[dict] | None = None,
) -> dict | None:
    text = f"{question}\n{answer}".lower()
    if not _should_auto_curate(question, answer):
        return None
    kind = _infer_kind_from_text(text)
    if kind == "note" and not any(k in text for k in ("규칙", "기준", "조건", "검증", "원문", "본문", "저장", "수집")):
        return None
    status = "draft"
    if any(token in text for token in ("규칙", "기준", "조건", "손실한도", "레버리지", "검증", "원문", "본문", "편향", "수집")):
        status = "reviewed"
    title = _derive_title(question, answer)
    target_id = target.get("id") if target else ""
    plan = {
        "action": "update" if target else "create",
        "title": title,
        "summary": _clean(answer[:900] or question[:900], 900),
        "body": _clean(answer, 6000),
        "kind": kind,
        "status": status,
        "tags": _auto_tags(text, surface, kind),
        "source_refs": [],
        "links": _auto_link_candidates(question, surface, candidates or [], exclude_id=target_id),
        "target_id": target_id,
        "confidence": 0.72 if status == "reviewed" else 0.58,
        "reason": "heuristic curation",
        "source": "heuristic",
    }
    return plan


def _plan_to_page_payload(
    plan: dict,
    *,
    question: str,
    answer: str,
    surface: str,
    target: dict | None = None,
) -> dict | None:
    if not isinstance(plan, dict):
        return None
    target_id = _clean(plan.get("target_id") or (target.get("id") if target else ""), 80)
    title = _clean(plan.get("title") or _derive_title(question, answer), 160)
    summary = _clean(plan.get("summary") or answer[:2400] or question[:2400], 2400)
    body = _clean(plan.get("body") or answer or summary, 6000)
    kind = _clean(plan.get("kind") or "playbook", 40).lower() or "playbook"
    if kind not in VALID_KINDS:
        kind = "note"
    status = _clean(plan.get("status") or "draft", 40).lower() or "draft"
    if status not in VALID_STATUSES:
        status = "draft"
    confidence = _num_or_default(plan.get("confidence"), 0.5)
    final_id = target_id or _page_id(title, surface, kind)
    links = _clean_links(plan.get("links") or [], self_id=final_id)
    if target:
        links = _clean_links([*(target.get("links") or []), *links], self_id=final_id)
    tags = _dedupe_texts([
        WIKI_TAG,
        surface,
        kind,
        status,
        *(plan.get("tags") or []),
    ], limit=20, item_limit=60)
    source_refs = _dedupe_texts([
        *(plan.get("source_refs") or []),
        f"conversation:{_page_id(question, surface, kind)}",
    ], limit=12, item_limit=180)
    messages = [
        {"role": "user", "text": question},
        {"role": "assistant", "text": answer},
    ]
    if target:
        messages = _merge_messages(target.get("messages") or [], messages)
        source_refs = _dedupe_texts([*(target.get("source_refs") or []), *source_refs], limit=12, item_limit=180)
        tags = _dedupe_texts([*(target.get("tags") or []), *tags], limit=20, item_limit=60)
        summary = summary or target.get("summary") or ""
        body = body or target.get("body") or summary
    if not title or not body:
        return None
    return {
        "id": final_id,
        "title": title,
        "summary": summary,
        "body": body,
        "surface": surface,
        "kind": kind,
        "status": status,
        "tags": tags,
        "source_refs": source_refs,
        "links": links,
        "messages": messages,
        "confidence": confidence,
    }


def _best_candidate_page(question: str, surface: str, candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    scored: list[tuple[int, dict]] = []
    for page in candidates:
        score = _candidate_score(page.get("raw") or {}, question, surface, "all")
        scored.append((score, page))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_page = scored[0]
    if best_score < AUTO_CURATE_MIN_SCORE:
        return None
    return best_page


def _auto_link_candidates(
    question: str,
    surface: str,
    candidates: list[dict],
    *,
    exclude_id: str = "",
    limit: int = 3,
) -> list[str]:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for page in candidates:
        page_id = _clean(page.get("id"), 80)
        if not page_id or page_id == exclude_id or page_id in seen:
            continue
        seen.add(page_id)
        score = _candidate_score(page.get("raw") or {}, question, surface, "all")
        if score >= AUTO_CURATE_MIN_SCORE:
            scored.append((score, page_id))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [page_id for _score, page_id in scored[:limit]]


def _infer_kind_from_text(text: str) -> str:
    q = str(text or "").lower()
    if any(token in q for token in ("손실", "리스크", "위험", "mdd", "최대손실")):
        return "risk"
    if any(token in q for token in ("결정", "선택", "교체", "승격", "update")):
        return "decision"
    if any(token in q for token in ("규칙", "전략", "백테스트", "방법", "시나리오")):
        return "playbook"
    if any(token in q for token in ("개념", "정의", "용어", "무엇", "왜")):
        return "concept"
    return "note"


def _recently_created_dedup(question: str, surface: str, *, hours: int = 24) -> bool:
    """최근 hours 시간 내 유사한 제목/요약의 페이지가 있으면 True 반환."""
    recent = list_pages(surface=surface, limit=20)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    question_tokens = _tokens(question[:200])
    for page in recent:
        created_str = page.get("created_at") or ""
        if not created_str:
            continue
        try:
            created = datetime.fromisoformat(str(created_str).replace("Z", "+00:00"))
        except Exception:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created < cutoff:
            continue
        title_summary = (page.get("title") or "") + " " + (page.get("summary") or "")
        page_tokens = _tokens(title_summary[:500])
        overlap = question_tokens & page_tokens
        if len(overlap) >= 5:
            return True
    return False


def _derive_title(question: str, answer: str) -> str:
    """답변에서 bullet point 기반 제목을 추출, 없으면 질문."""
    for line in _clean(answer, 300).splitlines():
        stripped = line.strip().lstrip("-•*").strip()
        if 12 <= len(stripped) <= 55:
            return stripped[:80]
    q = _clean(question, 120)
    if q:
        return q[:80]
    a = _clean(answer, 120)
    return a[:80] or "위키 페이지"


def _auto_tags(text: str, surface: str, kind: str) -> list[str]:
    tags = {WIKI_TAG, surface, kind}
    mapping = {
        "portfolio": ("포트폴리오", "비중", "손실한도", "레버리지", "현금", "mdd"),
        "market": ("시장", "지정학", "유가", "달러", "크레딧", "금리"),
        "ticker": ("종목", "티커", "실적", "밸류", "차트"),
        "paper": ("모의투자", "단기", "트레이딩", "원장", "검증"),
        "lab": ("전략", "백테스트", "rsi", "dsl", "시나리오"),
    }
    for tag, words in mapping.items():
        if any(word in text for word in words):
            tags.add(tag)
    if "위키" in text or "기억" in text:
        tags.add("wiki")
    return sorted(tags)


def _num_or_default(value: object, default: float = 0.5) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _merge_messages(existing: list[dict], new_messages: list[dict]) -> list[dict]:
    rows = []
    seen: set[tuple[str, str]] = set()
    for row in list(existing or []) + list(new_messages or []):
        if not isinstance(row, dict):
            continue
        role = _clean(row.get("role") or "", 32)
        text = _clean(row.get("text") or "", 2200)
        if not text:
            continue
        key = (role, text)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "role": role or "user",
                "text": text,
                "createdAt": _clean(row.get("createdAt") or _now(), 80),
            }
        )
    return rows[:8]
