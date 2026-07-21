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
    storage.ensure_schema()
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
    try:
        shared_memory.append_record(record)
    except Exception:
        pass
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
        plan = _heuristic_curation_plan(question, answer, surface=surface, target=target)
    if not plan:
        return None

    action = _clean(plan.get("action") or "create", 20).lower()
    if action not in {"create", "update", "skip"}:
        action = "create"
    if action == "skip":
        return None

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
        "목표: 재사용 가능한 규칙, 결정, 저장/수집 원칙, 실패 교정만 하나의 위키 카드로 정리한다.",
        "짧은 진행 확인, 단발성 수다, 상태 보고, 확인 대답은 생성 금지다.",
        "반드시 JSON object만 출력한다. 마크다운, 설명문, 코드펜스는 금지한다.",
        "가능한 action 값은 create, update, skip 이다.",
        "update 를 고를 때는 target_id 를 기존 후보 페이지 id 로 지정한다.",
        "확신이 낮으면 status 는 draft, 중간이면 reviewed, 이미 안정적인 운영 규칙이면 stable 이다.",
        "필드: action, title, summary, body, kind, status, tags, source_refs, target_id, confidence, reason.",
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
        "JSON 예시:",
        '{"action":"create","title":"손실한도와 레버리지","summary":"...","body":"...","kind":"playbook","status":"reviewed","tags":["risk","portfolio"],"source_refs":["conversation:123"],"target_id":"","confidence":0.86,"reason":"..."}',
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
    plan = {
        "action": "update" if target else "create",
        "title": title,
        "summary": _clean(answer[:900] or question[:900], 900),
        "body": _clean(answer, 6000),
        "kind": kind,
        "status": status,
        "tags": _auto_tags(text, surface, kind),
        "source_refs": [],
        "target_id": target.get("id") if target else "",
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
        "id": target_id or _page_id(title, surface, kind),
        "title": title,
        "summary": summary,
        "body": body,
        "surface": surface,
        "kind": kind,
        "status": status,
        "tags": tags,
        "source_refs": source_refs,
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


def _derive_title(question: str, answer: str) -> str:
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
