from __future__ import annotations

from typing import Any
import re

from agent_console import wiki


def _text_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[0-9A-Za-z가-힣_.$+-]{2,}", str(text or "").lower())
        if token not in {"그리고", "그러면", "어떻게", "지금", "the", "and", "for", "with", "about", "this"}
    }


def _compact_text(text: object, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _score_reference_text(question_tokens: set[str], *parts: object) -> int:
    haystack = " ".join(_compact_text(part, 600).lower() for part in parts if part is not None)
    score = 0
    for token in question_tokens:
        if token and token in haystack:
            score += 3 if len(token) > 3 else 1
    return score


def build_answer_references(
    question: str,
    surface: str,
    pack: dict[str, Any],
    answer: str = "",
    *,
    wiki_limit: int = 4,
    source_limit: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """질문·답변·컨텍스트를 바탕으로 AI 콘솔 하단에 붙일 참고문헌을 만든다."""
    resolved_question = str(question or "").strip()
    question_tokens = _text_tokens(f"{resolved_question}\n{answer}")
    sources = pack.get("sources") or {}
    events = list(sources.get("events") or [])
    reports = list(pack.get("reports") or [])

    try:
        wiki_pages = wiki.list_pages(query=resolved_question or question, surface=surface, status="all", limit=wiki_limit)
    except Exception:
        wiki_pages = []

    wiki_refs: list[dict[str, Any]] = []
    for page in wiki_pages:
        wiki_refs.append({
            "kind": "wiki",
            "id": page.get("id"),
            "title": page.get("title") or "위키 페이지",
            "summary": _compact_text(page.get("summary") or page.get("body") or "", 220),
            "surface": page.get("surface") or surface,
            "status": page.get("status") or "draft",
            "page_kind": page.get("kind") or "note",
            "source_refs": list(page.get("source_refs") or []),
            "verification_status": page.get("verification_status") or "unverified",
            "reason": "질문과 위키 키워드가 겹치는 참고 페이지",
        })

    ranked_events: list[tuple[int, dict[str, Any]]] = []
    for row in events:
        score = _score_reference_text(
            question_tokens,
            row.get("title"),
            row.get("summary"),
            row.get("body"),
            row.get("source"),
            row.get("url"),
        )
        if score > 0:
            ranked_events.append((score, row))
    if not ranked_events:
        ranked_events = [(0, row) for row in events[:source_limit]]
    ranked_events.sort(
        key=lambda item: (
            item[0],
            str(item[1].get("published_at") or item[1].get("collected_at") or ""),
        ),
        reverse=True,
    )

    source_refs: list[dict[str, Any]] = []
    for _score, row in ranked_events[:source_limit]:
        url = str(row.get("url") or row.get("source_url") or row.get("link") or "").strip()
        source_refs.append({
            "kind": "source",
            "source": row.get("source") or "source",
            "title": row.get("title") or row.get("summary") or "원문 출처",
            "summary": _compact_text(row.get("summary") or row.get("body") or row.get("text") or "", 260),
            "url": url if url.startswith(("http://", "https://")) else "",
            "published_at": row.get("published_at") or row.get("collected_at") or "",
            "reason": "질문과 키워드가 겹치는 최근 수집 출처",
            "metadata": {
                "id": row.get("id"),
                "path": row.get("path"),
            },
        })

    for report in reports[: max(1, source_limit // 2)]:
        source_refs.append({
            "kind": "report",
            "source": "stock-report:report",
            "title": report.get("title") or report.get("name") or "리포트",
            "summary": _compact_text(report.get("summary") or "", 260),
            "url": "",
            "published_at": report.get("mtime") or "",
            "reason": "최신 리포트 요약",
            "metadata": {
                "path": report.get("path"),
                "name": report.get("name"),
            },
        })

    return {
        "wiki": wiki_refs,
        "sources": source_refs,
    }
