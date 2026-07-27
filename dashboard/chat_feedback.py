"""Answer trace and feedback helpers for the AI console.

This module intentionally stores only user-visible trace summaries. It does not
record hidden reasoning or chain-of-thought text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_console import shared_memory, wiki

_RATING_LABELS = {
    "helpful": "도움됨",
    "neutral": "애매함",
    "not_helpful": "별로",
}


def _clean(value: object, limit: int = 1200) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def build_trace(
    question: str,
    surface: str,
    pack: dict[str, Any] | None,
    result: dict[str, Any] | None,
    references: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Build a compact ReAct-style trace safe to show in the UI."""
    pack = pack or {}
    result = result or {}
    references = references or {}
    ctx = result.get("context") or {}
    sources = pack.get("sources") or {}
    events = sources.get("events") or []
    memory = pack.get("memory") or []
    detail = ctx.get("detail") or pack.get("detail") or ""
    postprocess = (ctx.get("postprocess") or {}).get("wiki_autocurate") or "none"
    engine = str(ctx.get("engine") or "local-rules")
    wiki_count = len(references.get("wiki") or [])
    source_count = len(references.get("sources") or [])
    event_count = int(ctx.get("event_count") if ctx.get("event_count") is not None else len(events))
    memory_count = int(ctx.get("memory_count") if ctx.get("memory_count") is not None else len(memory))

    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "surface": _clean(surface, 80),
        "detail": _clean(detail or surface, 120),
        "engine": engine,
        "question_preview": _clean(question, 180),
        "steps": [
            {"label": "질문 분류", "value": _clean(surface, 80)},
            {"label": "컨텍스트 확인", "value": f"events {event_count} · memory {memory_count}"},
            {"label": "근거 선택", "value": f"wiki {wiki_count} · source {source_count}"},
            {"label": "생성 엔진", "value": "규칙" if engine == "local-rules" else engine},
            {"label": "위키 후처리", "value": _clean(postprocess, 80)},
        ],
    }


def record_answer_feedback(
    *,
    question: str,
    answer: str,
    surface: str,
    rating: str,
    reason: str = "",
    references: dict[str, list[dict[str, Any]]] | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist chat answer feedback and update referenced wiki page counters."""
    rating = rating if rating in _RATING_LABELS else "neutral"
    references = references or {}
    trace = trace or {}
    wiki_refs = [ref for ref in references.get("wiki") or [] if isinstance(ref, dict)]
    source_refs = [ref for ref in references.get("sources") or [] if isinstance(ref, dict)]
    artifacts = []
    for ref in wiki_refs:
        page_id = _clean(ref.get("id"), 100)
        if page_id:
            artifacts.append(f"wiki:{page_id}")
    for ref in source_refs[:6]:
        url = _clean(ref.get("url") or (ref.get("metadata") or {}).get("path"), 240)
        if url:
            artifacts.append(url)

    try:
        record = shared_memory.append_record({
            "provider": "codex-cli",
            "screen": surface,
            "title": f"AI 답변 피드백: {_RATING_LABELS[rating]}",
            "summary": _clean(reason or answer, 1200),
            "tags": ["feedback", "chat_feedback", surface, rating],
            "artifacts": artifacts,
            "messages": [
                {"role": "user", "text": _clean(question, 1000)},
                {"role": "assistant", "text": _clean(answer, 2000)},
            ],
            "source": {
                "surface": surface,
                "screen": "ai-console-chat",
                "provider": "codex-cli",
                "writer": "codex-cli",
            },
            "contextPacket": {
                "rating": rating,
                "trace": trace,
                "referenceCounts": {
                    "wiki": len(wiki_refs),
                    "sources": len(source_refs),
                },
            },
        })
    except Exception:
        record = None

    feedback_fn = getattr(wiki, "_store_page_feedback", None)
    if callable(feedback_fn):
        wiki_rating = rating if rating in {"helpful", "not_helpful"} else "neutral"
        for ref in wiki_refs:
            page_id = _clean(ref.get("id"), 100)
            if not page_id:
                continue
            try:
                feedback_fn(page_id, wiki_rating)
            except Exception:
                continue
    return record
