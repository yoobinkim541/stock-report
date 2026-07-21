from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

WIKI_SURFACE = "wiki"
VALID_STATUSES = ("draft", "reviewed", "stable", "archived")


@dataclass(frozen=True)
class WikiPage:
    id: str
    title: str
    summary: str = ""
    body: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    status: str = "draft"
    surface: str = WIKI_SURFACE
    kind: str = "note"
    confidence: float = 0.5
    created_at: str = ""
    updated_at: str = ""
    source_refs: tuple[str, ...] = field(default_factory=tuple)
    source: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "tags": list(self.tags),
            "status": self.status,
            "surface": self.surface,
            "kind": self.kind,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_refs": list(self.source_refs),
            "source": dict(self.source),
        }


def _clean(value: object, limit: int = 2200) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", _clean(text, 120).lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "wiki"


def _dedupe_texts(values: Iterable[object], *, limit: int = 12, item_limit: int = 80) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        text = _clean(raw, item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _tokens(text: str) -> set[str]:
    text = _clean(text, 800).lower()
    return {
        token
        for token in re.findall(r"[0-9a-zA-Z가-힣_.$+-]{2,}", text)
        if token not in {"그리고", "그러면", "어떻게", "지금", "the", "and", "for", "with", "about"}
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def _surface_from_record(record: dict[str, Any]) -> str:
    source = record.get("source") or {}
    surface = _clean(source.get("surface") or source.get("screen") or "", 60).lower()
    if surface:
        return surface
    for tag in record.get("tags") or []:
        clean = _clean(tag, 60).lower()
        if clean.startswith("surface:"):
            return clean.split(":", 1)[1].strip() or WIKI_SURFACE
    return WIKI_SURFACE


def _kind_from_record(record: dict[str, Any]) -> str:
    artifacts = record.get("artifacts") or []
    for item in artifacts:
        clean = _clean(item, 80).lower()
        if clean.startswith("kind:"):
            candidate = clean.split(":", 1)[1].strip()
            if candidate:
                return candidate
    return _clean(record.get("kind") or "note", 40).lower() or "note"


def _is_wiki_record(record: dict[str, Any]) -> bool:
    tags = [_clean(tag, 60).lower() for tag in (record.get("tags") or [])]
    if "wiki" in tags:
        return True
    source = record.get("source") or {}
    surface = _clean(source.get("surface") or source.get("screen") or "", 60).lower()
    return surface == WIKI_SURFACE


def _record_to_page(record: dict[str, Any]) -> dict[str, Any]:
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
        "id": record.get("id") or _page_id(record.get("title") or "위키 페이지", record.get("surface") or WIKI_SURFACE, record.get("kind") or "note"),
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


def _normalize_page(page: dict[str, Any] | WikiPage) -> dict[str, Any]:
    if isinstance(page, WikiPage):
        return page.as_dict()
    if not isinstance(page, dict):
        return _record_to_page({"title": page})
    if {"title", "summary", "body"}.intersection(page):
        tags = _dedupe_texts(page.get("tags") or [], limit=20, item_limit=60)
        source_refs = _dedupe_texts(page.get("source_refs") or [], limit=12, item_limit=120)
        normalized = {
            "id": page.get("id") or _page_id(page.get("title") or "위키 페이지", page.get("surface") or WIKI_SURFACE, page.get("kind") or "note"),
            "title": _clean(page.get("title") or "위키 페이지", 160),
            "slug": _slugify(page.get("title") or "위키 페이지"),
            "summary": _clean(page.get("summary") or "", 2400),
            "body": _clean(page.get("body") or "", 6000),
            "tags": tags,
            "status": _clean(page.get("status") or _status_from_tags(tags), 40),
            "surface": _clean(page.get("surface") or WIKI_SURFACE, 60).lower() or WIKI_SURFACE,
            "kind": _clean(page.get("kind") or "note", 40).lower() or "note",
            "confidence": float(page.get("confidence") or 0.5),
            "created_at": page.get("created_at") or page.get("createdAt") or "",
            "updated_at": page.get("updated_at") or page.get("updatedAt") or page.get("createdAt") or "",
            "source": dict(page.get("source") or {}),
            "source_refs": source_refs,
            "decisions": _dedupe_texts(page.get("decisions") or [], limit=8, item_limit=280),
            "openQuestions": _dedupe_texts(page.get("openQuestions") or [], limit=8, item_limit=280),
            "messages": list(page.get("messages") or []),
            "snippet": _clean(page.get("summary") or page.get("body") or "", 260),
            "raw": dict(page),
        }
        return normalized
    return _record_to_page(dict(page))


def _normalize_pages(pages: Iterable[dict[str, Any] | WikiPage]) -> list[dict[str, Any]]:
    return [_normalize_page(page) for page in pages or []]


def _matches_surface(page: dict[str, Any], surface: str) -> bool:
    return surface == "all" or page.get("surface") == surface.lower()


def _matches_status(page: dict[str, Any], status: str) -> bool:
    return status == "all" or page.get("status") == status.lower()


def _matches_query(page: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(page.get("title") or ""),
            str(page.get("summary") or ""),
            str(page.get("body") or ""),
            " ".join(page.get("tags") or []),
            " ".join(page.get("decisions") or []),
            " ".join(page.get("openQuestions") or []),
            " ".join((msg or {}).get("text") or "" for msg in page.get("messages") or []),
        ]
    ).lower()
    return all(token in haystack for token in _tokens(query))


def _visible_pages(pages: list[dict[str, Any]], *, query: str = "", surface: str = "all", status: str = "all") -> list[dict[str, Any]]:
    visible = [page for page in pages if _matches_surface(page, surface) and _matches_status(page, status) and _matches_query(page, query)]
    visible.sort(key=lambda page: (page.get("updated_at") or page.get("created_at") or "", page.get("title") or ""), reverse=True)
    return visible


def _source_weight(page: dict[str, Any], counter: Counter[str]) -> int:
    weight = 0
    for ref in page.get("source_refs") or []:
        weight += counter.get(str(ref), 0)
    return weight


def _related_score(selected: dict[str, Any], candidate: dict[str, Any], *, counter: Counter[str]) -> tuple[int, int, str]:
    selected_tags = {str(tag).lower() for tag in selected.get("tags") or []}
    candidate_tags = {str(tag).lower() for tag in candidate.get("tags") or []}
    source_hits = len(set(selected.get("source_refs") or []) & set(candidate.get("source_refs") or []))
    tag_hits = len(selected_tags & candidate_tags)
    source_weight = _source_weight(candidate, counter)
    recency = str(candidate.get("updated_at") or candidate.get("created_at") or "")
    return (tag_hits * 4 + source_hits * 6 + source_weight, tag_hits + source_hits, recency)


def related_pages(selected_page: dict[str, Any] | WikiPage | None, pages: Iterable[dict[str, Any] | WikiPage], *, limit: int = 6) -> list[dict[str, Any]]:
    if not selected_page:
        return []
    selected = _normalize_page(selected_page)
    corpus = _normalize_pages(pages)
    if not corpus:
        return []
    counter = Counter()
    for page in corpus:
        for ref in page.get("source_refs") or []:
            counter[str(ref)] += 1
    scored: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    for page in corpus:
        if page.get("id") == selected.get("id"):
            continue
        score = _related_score(selected, page, counter=counter)
        if score[0] <= 0:
            continue
        scored.append((score, page))
    scored.sort(key=lambda item: (item[0][0], item[0][1], item[0][2], item[1].get("updated_at") or ""), reverse=True)
    return [page for _score, page in scored[: max(1, int(limit or 6))]]


def select_page_id(pages: Iterable[dict[str, Any] | WikiPage], *, selected_page_id: str = "", query: str = "", surface: str = "all", status: str = "all") -> str | None:
    normalized = _normalize_pages(pages)
    visible = _visible_pages(normalized, query=query, surface=surface, status=status)
    if not visible:
        return None
    selected_page_id = _clean(selected_page_id, 80)
    if selected_page_id:
        for page in visible:
            if page.get("id") == selected_page_id:
                return selected_page_id
    return str(visible[0].get("id")) if visible else None


def build_browser_model(pages: Iterable[dict[str, Any] | WikiPage], *, selected_page_id: str = "", query: str = "", surface: str = "all", status: str = "all") -> dict[str, Any]:
    normalized = _normalize_pages(pages)
    visible = _visible_pages(normalized, query=query, surface=surface, status=status)
    selected_id = select_page_id(normalized, selected_page_id=selected_page_id, query=query, surface=surface, status=status)
    selected = next((page for page in visible if page.get("id") == selected_id), None)
    if selected is None and selected_id:
        selected = next((page for page in normalized if page.get("id") == selected_id), None)
    related = related_pages(selected, normalized, limit=6) if selected else []
    return {
        "query": query,
        "surface": surface,
        "status": status,
        "visible": visible,
        "visible_count": len(visible),
        "selected_id": selected_id,
        "selected": selected,
        "related": related,
        "generated_at": _now(),
    }


def filter_pages(pages: Iterable[dict[str, Any] | WikiPage], *, query: str = "", surface: str = "all", status: str = "all") -> list[dict[str, Any]]:
    return build_browser_model(pages, query=query, surface=surface, status=status)["visible"]


def pick_selected_page(pages: Iterable[dict[str, Any] | WikiPage], *, selected_page_id: str = "", query: str = "", surface: str = "all", status: str = "all") -> str | None:
    return select_page_id(pages, selected_page_id=selected_page_id, query=query, surface=surface, status=status)


def _last_chat_exchange(rows: list[dict[str, Any]]) -> dict[str, str] | None:
    pending: dict[str, Any] | None = None
    for row in rows:
        role = str(row.get("role") or "").strip().lower()
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        if role == "user":
            pending = row
            continue
        if role == "assistant" and pending:
            return {
                "id": f"{id(pending)}-{id(row)}",
                "question": str(pending.get("content") or "").strip(),
                "answer": text,
            }
    return None


def _extract_selected_page_id(event: Any) -> str:
    if not event:
        return ""
    selection = None
    if isinstance(event, dict):
        selection = event.get("selection") or event.get("points") or event
    else:
        selection = getattr(event, "selection", None) or getattr(event, "points", None) or event
    points = []
    if isinstance(selection, dict):
        points = selection.get("points") or []
    elif isinstance(selection, Iterable):
        points = list(selection)
    for point in points:
        try:
            customdata = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
            if customdata:
                return str(customdata[0] if isinstance(customdata, (list, tuple)) else customdata)
        except Exception:
            continue
    return ""


def render_wiki_tab(surface: str, pack: dict[str, Any] | None = None) -> None:
    import pandas as pd
    import streamlit as st

    from agent_console import wiki
    from dashboard import wiki_mesh

    st.markdown("##### AI 위키")
    st.caption("대화와 메모를 카드로 승격해 챗봇이 다시 읽는 지식층입니다.")

    stats = wiki.stats()
    cols = st.columns(4)
    cols[0].metric("페이지", f"{stats.get('total', 0)}")
    cols[1].metric("초안", f"{stats.get('status_counts', {}).get('draft', 0)}")
    cols[2].metric("검토", f"{stats.get('status_counts', {}).get('reviewed', 0)}")
    latest = stats.get("latest") or {}
    cols[3].metric("최근", latest.get("title", "—")[:20] if latest else "—")

    pages_all = wiki.list_pages(query="", surface="all", status="all", limit=400)
    if not pages_all:
        st.info("아직 위키 카드가 없습니다. 아래에서 현재 대화를 위키로 승격해 보세요.")
        return

    surfaces = ["all", *sorted({str(page.get("surface") or WIKI_SURFACE) for page in pages_all})]
    statuses = ["all", *VALID_STATUSES]
    f1, f2, f3 = st.columns([1.15, 0.75, 0.75], gap="small")
    query = f1.text_input("위키 검색", key="agent_wiki_query", placeholder="손실한도, 레버리지, AI ETF, 시장 신호...")
    current_surface = str(surface or "all")
    surface_index = surfaces.index(current_surface) if current_surface in surfaces else 0
    surface_filter = f2.selectbox(
        "표면",
        surfaces,
        index=surface_index,
        key="agent_wiki_surface_filter",
    )
    status_filter = f3.selectbox(
        "상태",
        statuses,
        index=0,
        key="agent_wiki_status_filter",
    )

    graph_selected = wiki_mesh.render_wiki_mesh(
        pages_all,
        selected_page_id=st.session_state.get("agent_wiki_selected_page_id", ""),
        query=query,
        surface=surface_filter,
        status=status_filter,
        depth=int(st.session_state.get("agent_wiki_graph_depth", 2)),
        max_nodes=96,
        key="agent_wiki_graph",
    )
    if graph_selected:
        st.session_state["agent_wiki_selected_page_id"] = graph_selected

    browser = build_browser_model(
        pages_all,
        selected_page_id=st.session_state.get("agent_wiki_selected_page_id", ""),
        query=query,
        surface=surface_filter,
        status=status_filter,
    )
    if browser.get("selected_id"):
        st.session_state["agent_wiki_selected_page_id"] = browser["selected_id"]

    left, center, right = st.columns([0.92, 1.18, 0.9], gap="large")
    with left:
        st.markdown("##### 문서 브라우저")
        st.caption(f"{browser.get('visible_count', 0)}개 표시")
        visible = browser.get("visible") or []
        if visible:
            for page in visible:
                with st.container(border=True):
                    st.markdown(f"**{page.get('title', '위키')}**")
                    st.caption(f"{page.get('surface', 'wiki')} · {page.get('kind', 'note')} · {page.get('status', 'draft')}")
                    if page.get("summary"):
                        st.caption(str(page["summary"])[:180])
                    if page.get("tags"):
                        st.caption(" · ".join(page["tags"][:5]))
                    btn1, btn2 = st.columns(2)
                    if btn1.button("불러오기", key=f"wiki_load_{page.get('id')}", width="stretch"):
                        st.session_state["agent_wiki_selected_page_id"] = page.get("id")
                        st.rerun()
                    if btn2.button("삭제", key=f"wiki_drop_{page.get('id')}", width="stretch"):
                        if wiki.delete_page(page.get("id")):
                            st.session_state.pop("agent_wiki_selected_page_id", None)
                            st.success("위키 페이지 삭제 완료")
                            st.rerun()
        else:
            st.info("조건에 맞는 페이지가 없습니다.")

    selected = browser.get("selected") or {}
    related = browser.get("related") or []
    with center:
        st.markdown("##### 페이지 미리보기")
        if selected:
            with st.container(border=True):
                st.markdown(f"**{selected.get('title', '위키')}**")
                st.caption(
                    f"{selected.get('surface', 'wiki')} · {selected.get('kind', 'note')} · {selected.get('status', 'draft')}"
                    f" · {selected.get('updated_at') or selected.get('created_at') or '—'}"
                )
                if selected.get("summary"):
                    st.write(selected["summary"])
                if selected.get("body"):
                    st.markdown(selected["body"])
                if selected.get("source_refs"):
                    st.markdown("###### 원문 근거")
                    st.code("\n".join(selected["source_refs"]), language="text")
                if selected.get("tags"):
                    st.markdown("###### 태그")
                    st.caption(" · ".join(selected["tags"]))
                if selected.get("source"):
                    st.markdown("###### 메타데이터")
                    st.json(selected["source"])

            st.markdown("##### 편집")
            with st.form("agent_wiki_editor", clear_on_submit=False):
                title = st.text_input("제목", value=selected.get("title") or st.session_state.get("agent_wiki_title", ""))
                kind_options = ["playbook", "decision", "risk", "concept", "note"]
                kind = st.selectbox(
                    "종류",
                    kind_options,
                    index=kind_options.index(selected.get("kind", "playbook"))
                    if selected.get("kind", "playbook") in kind_options else 0,
                )
                status = st.selectbox(
                    "상태",
                    list(VALID_STATUSES),
                    index=list(VALID_STATUSES).index(selected.get("status", "draft"))
                    if selected.get("status", "draft") in VALID_STATUSES else 0,
                )
                tags = st.text_input("태그", value=", ".join(selected.get("tags", [])))
                summary = st.text_area("요약", value=selected.get("summary", ""), height=120)
                body = st.text_area("본문", value=selected.get("body", ""), height=180)
                source_refs = st.text_input("source refs", value=", ".join(selected.get("source_refs", [])))
                if st.form_submit_button("저장", type="primary", width="stretch"):
                    saved = wiki.upsert_page(
                        {
                            "id": selected.get("id"),
                            "title": title,
                            "surface": surface_filter if surface_filter != "all" else surface,
                            "kind": kind,
                            "status": status,
                            "tags": [item.strip() for item in tags.replace(";", ",").split(",") if item.strip()],
                            "summary": summary,
                            "body": body,
                            "source_refs": [item.strip() for item in source_refs.replace(";", ",").split(",") if item.strip()],
                            "confidence": selected.get("confidence", 0.7),
                        }
                    )
                    st.session_state["agent_wiki_selected_page_id"] = saved.get("id")
                    st.success("위키 페이지를 저장했습니다.")
                    st.rerun()
        else:
            st.info("선택된 위키 페이지가 없습니다.")

    with right:
        st.markdown("##### 관련 문서")
        if related:
            for page in related[:6]:
                with st.container(border=True):
                    st.markdown(f"**{page.get('title', '위키')}**")
                    st.caption(f"{page.get('surface', 'wiki')} · {page.get('kind', 'note')} · {page.get('status', 'draft')}")
                    if page.get("summary"):
                        st.caption(str(page["summary"])[:180])
                    if page.get("tags"):
                        st.caption(" · ".join(page["tags"][:5]))
        else:
            st.caption("관련 문서가 없습니다.")

        st.markdown("##### 현재 대화 승격")
        chat_rows = []
        if pack and isinstance(pack, dict):
            chat_rows = pack.get("chat_rows") or []
        if not chat_rows:
            from streamlit import session_state as _session_state
            chat_rows = _session_state.get("agent_chat_messages_auto", [])
        exchange = _last_chat_exchange(list(chat_rows or []))
        if exchange:
            st.markdown(f"**Q.** {exchange['question']}")
            st.markdown(f"**A.** {exchange['answer']}")
            capture_col, reset_col = st.columns(2)
            if capture_col.button("이 대화를 위키로", type="primary", width="stretch", key=f"wiki_capture_{exchange['id']}"):
                saved = wiki.capture_from_chat(
                    exchange["question"],
                    exchange["answer"],
                    surface=surface_filter if surface_filter != "all" else surface,
                    title=exchange["question"],
                    status="draft",
                    kind="playbook",
                    tags=["conversation", surface_filter if surface_filter != "all" else surface],
                    source_refs=[f"conversation:{exchange['id']}"],
                )
                st.session_state["agent_wiki_selected_page_id"] = saved.get("id")
                st.toast("대화를 위키로 저장했습니다.")
                st.rerun()
            if reset_col.button("선택 해제", width="stretch"):
                st.session_state.pop("agent_wiki_selected_page_id", None)
                st.rerun()
        else:
            st.caption("현재 대화 기록이 없어 승격할 항목이 없습니다.")

        with st.expander("위키가 챗봇에 들어가는 방식", expanded=False):
            section = wiki.build_context_section(query=query or selected.get("title", ""), surface=surface_filter if surface_filter != "all" else surface, limit=4)
            if section:
                st.code(section, language="text")
            else:
                st.caption("아직 노출할 위키 지식이 없습니다.")