from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from agent_console import wiki


from dashboard import cached, ui_actions
from dashboard.wiki_browser import (
    KIND_LABELS,
    KIND_OPTIONS,
    STATUS_LABELS,
    SURFACE_LABELS,
    SURFACE_OPTIONS,
    build_wiki_health_model,
    display_source_ref,
    merge_event_caption,
    option_label,
    page_display_name,
    promotion_guardrail,
    render_wiki_status_metrics,
)

STATUS_OPTIONS = ["all", "draft", "reviewed", "stable", "archived"]


def _safe_wiki_stats() -> dict:
    stats_fn = getattr(cached, "wiki_stats", None)
    if callable(stats_fn):
        try:
            return stats_fn()
        except Exception:
            pass
    pages = []
    try:
        pages = cached.wiki_pages(query="", surface="all", status="all", limit=10000)
    except Exception:
        pages = []
    status_counts = {"draft": 0, "reviewed": 0, "stable": 0, "archived": 0}
    kind_counts: dict[str, int] = {}
    surface_counts: dict[str, int] = {}
    latest: dict = {}
    for page in pages:
        status = str(page.get("status") or "draft")
        status_counts[status] = status_counts.get(status, 0) + 1
        kind = str(page.get("kind") or "note")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        surface = str(page.get("surface") or "wiki")
        surface_counts[surface] = surface_counts.get(surface, 0) + 1
        if not latest or str(page.get("updated_at") or page.get("created_at") or "") > str(latest.get("updated_at") or latest.get("created_at") or ""):
            latest = page
    return {
        "total": len(pages),
        "status_counts": status_counts,
        "kind_counts": kind_counts,
        "surface_counts": surface_counts,
        "latest": latest,
    }


def render():
    st.markdown(
        """
        <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:2px 0 10px;border-bottom:1px solid rgba(148,163,184,.18);margin-bottom:14px;">
          <div>
            <div style="color:rgba(148,163,184,.92);font-size:.82rem;text-transform:uppercase;letter-spacing:.08em;margin-bottom:2px;">shared-memory knowledge base</div>
            <h1 style="margin:0;font-size:1.55rem;line-height:1.15;letter-spacing:0;">AI 위키</h1>
          </div>
          <span style="color:rgba(148,163,184,.92);font-size:.82rem;">대화와 메모를 승격해 챗봇이 다시 읽는 정리층입니다.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    stats = _safe_wiki_stats()
    render_wiki_status_metrics(stats)

    try:
        pages_all = cached.wiki_pages(query="", surface="all", status="all", limit=10000)
    except Exception:
        pages_all = []
    try:
        search_health = cached.wiki_search_health()
    except Exception:
        search_health = {"provider": "fallback", "fallback_available": True, "qmd": {}}
    try:
        lint = cached.wiki_lint_pages()
    except Exception:
        lint = {"issue_count": 0, "issues": []}
    health = build_wiki_health_model(pages_all, search_health=search_health, lint=lint)

    surface_counts = health.get("surface_counts") or {}
    if surface_counts:
        st.markdown("##### 그룹 분포")
        surface_series = pd.Series({
            option_label(SURFACE_LABELS, key): count for key, count in surface_counts.items()
        }).sort_values(ascending=False)
        st.bar_chart(surface_series, height=180)
    kind_counts = health.get("kind_counts") or {}
    if kind_counts:
        top_kinds = ", ".join(
            f"{option_label(KIND_LABELS, k)}: {v}"
            for k, v in sorted(kind_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
        )
        st.caption(f"종류 분포 · {top_kinds}")

    recommendations = health.get("recommendations") or []
    if recommendations:
        st.markdown("##### 개선 권고")
        for rec in recommendations[:5]:
            with st.container(border=True):
                st.markdown(f"**{rec.get('title', '권고')}**")
                st.caption(option_label({
                    "balance": "균형",
                    "curation": "정리",
                    "trust": "신뢰",
                    "connectivity": "연결",
                    "hygiene": "정리",
                    "healthy": "양호",
                    "general": "일반",
                }, rec.get("category", "general")))
                st.write(rec.get("detail", ""))

    # 폼 제출형 검색 — 종전엔 text_input 이 폼 밖이라 글자 하나 칠 때마다 전체 rerun + 위키 조회
    with st.form("wiki_search_form", border=False):
        query = st.text_input("검색", key="wiki_query", placeholder="손실한도, 레버리지, SOL, 중동, 크레딧...")
        f1, f2, f3, f4 = st.columns([1, 1, 1, 0.6])
        surface = f1.selectbox(
            "화면", SURFACE_OPTIONS, index=0, key="wiki_surface_filter",
            format_func=lambda value: option_label(SURFACE_LABELS, value),
        )
        status = f2.selectbox(
            "상태", STATUS_OPTIONS, index=0, key="wiki_status_filter",
            format_func=lambda value: option_label(STATUS_LABELS, value),
        )
        kind_filter = f3.selectbox(
            "종류", KIND_OPTIONS, index=0, key="wiki_kind_filter",
            format_func=lambda value: option_label(KIND_LABELS, value),
        )
        f4.form_submit_button("검색", width="stretch")

    pages = cached.wiki_pages(query=query, surface=surface, status=status, limit=60)
    wanted = str(st.session_state.get("wiki_selected_page_id") or "")
    if wanted and not any(page.get("id") == wanted for page in pages):
        extra = cached.wiki_page(wanted)
        if extra and extra.get("id"):
            pages = [extra, *pages]
    if kind_filter != "all":
        pages = [page for page in pages if page.get("kind") == kind_filter]
    sync_id = str(st.session_state.get("_wiki_picker_sync") or "")
    if sync_id and not any(str(page.get("id") or "") == sync_id for page in pages):
        st.session_state.pop("_wiki_picker_sync", None)
        st.toast("문서를 열지 못했습니다.")
    _pending_curation_section()

    left, right = st.columns([1.05, 0.95], gap="large")
    with left:
        st.markdown("##### 위키 페이지")
        if not pages:
            st.info("아직 저장된 위키 페이지가 없습니다.")
        else:
            selected = _select_page(pages)
            if selected:
                st.session_state["wiki_selected_page_id"] = selected["id"]
                _page_card(selected)
                if ui_actions.confirm_delete(selected["id"], button_key="ai_wiki_delete", label="삭제"):
                    if wiki.delete_page(selected["id"]):
                        cached.clear_wiki_caches()
                        st.session_state.pop("wiki_selected_page_id", None)
                        st.toast("위키 페이지 삭제 완료")
                        st.rerun()
                    st.toast("삭제하지 못했습니다.")

    with right:
        st.markdown("##### 편집기")
        selected_page = cached.wiki_page(st.session_state.get("wiki_selected_page_id", ""))
        default_page = selected_page or _blank_page(query=query, surface=surface)
        with st.form("wiki_editor", clear_on_submit=False):
            title = st.text_input("제목", value=default_page.get("title", ""))
            editor_surface = st.selectbox(
                "화면",
                SURFACE_OPTIONS[1:],
                index=max(0, SURFACE_OPTIONS[1:].index(default_page.get("surface", "market"))
                      if default_page.get("surface", "market") in SURFACE_OPTIONS[1:] else 0),
                key="wiki_editor_surface",
                format_func=lambda value: option_label(SURFACE_LABELS, value),
            )
            kind = st.selectbox(
                "종류",
                KIND_OPTIONS[1:],
                index=max(0, KIND_OPTIONS[1:].index(default_page.get("kind", "note"))
                      if default_page.get("kind", "note") in KIND_OPTIONS[1:] else 0),
                key="wiki_editor_kind",
                format_func=lambda value: option_label(KIND_LABELS, value),
            )
            editor_status = st.selectbox(
                "상태",
                ["draft", "reviewed", "stable", "archived"],
                index=max(0, ["draft", "reviewed", "stable", "archived"].index(default_page.get("status", "draft"))
                      if default_page.get("status", "draft") in ["draft", "reviewed", "stable", "archived"] else 0),
                key="wiki_editor_status",
                format_func=lambda value: option_label(STATUS_LABELS, value),
            )
            tags = st.text_input("태그", value=", ".join(default_page.get("tags", [])))
            summary = st.text_area("요약", value=default_page.get("summary", ""), height=130)
            body = st.text_area("본문", value=default_page.get("body", ""), height=220)
            source_refs = st.text_input("출처", value=", ".join(default_page.get("source_refs", [])))
            parsed_refs = [item.strip() for item in source_refs.replace(";", ",").split(",") if item.strip()]
            guardrail = promotion_guardrail(editor_status, parsed_refs)
            if not guardrail.get("allowed"):
                st.warning(
                    f"승격 불가: {guardrail.get('message')} "
                    f"저장 시 {option_label(STATUS_LABELS, guardrail.get('downgraded_to'))}로 낮아집니다."
                )
            if st.form_submit_button("위키 저장", type="primary", width="stretch"):
                saved = wiki.upsert_page(
                    {
                        "id": default_page.get("id"),
                        "title": title,
                        "surface": editor_surface,
                        "kind": kind,
                        "status": editor_status,
                        "tags": [item.strip() for item in tags.replace(";", ",").split(",") if item.strip()],
                        "summary": summary,
                        "body": body,
                        "source_refs": parsed_refs,
                        "confidence": default_page.get("confidence", 0.7),
                    }
                )
                cached.clear_wiki_caches()
                st.session_state["wiki_selected_page_id"] = saved.get("id")
                if saved.get("id"):
                    st.session_state["_wiki_picker_sync"] = saved.get("id")
                if saved.get("status") != editor_status:
                    st.warning(f"상태는 {option_label(STATUS_LABELS, saved.get('status'))}로 저장됐습니다.")
                st.success("위키 페이지를 저장했습니다.")
                st.rerun()

        st.markdown("##### 최근 대화에서 승격")
        st.caption("이 페이지는 호환용입니다. 최신 콘솔에서는 대화 탭의 위키 브라우저를 사용합니다.")

        with st.expander("위키가 챗봇에 들어가는 방식", expanded=False):
            section = cached.wiki_context(query=query or default_page.get("title", ""), surface=surface, limit=4)
            if section:
                st.code(section, language="text")
            else:
                st.caption("아직 노출할 위키 지식이 없습니다.")


def _blank_page(*, query: str = "", surface: str = "market") -> dict:
    return {
        "id": "",
        "title": st.session_state.get("wiki_draft_title", query[:80] or "새 위키 페이지"),
        "surface": surface if surface != "all" else "market",
        "kind": "playbook",
        "status": "draft",
        "tags": ["wiki", surface] if surface != "all" else ["wiki", "market"],
        "summary": st.session_state.get("wiki_draft_summary", ""),
        "body": st.session_state.get("wiki_draft_summary", ""),
        "source_refs": [],
        "confidence": 0.7,
    }


def wiki_page_label(page: dict) -> str:
    return (
        f"{page.get('title', '위키')} · {page.get('status', 'draft')} · "
        f"{page.get('surface', 'wiki')}"
    )


def resolve_wiki_picker(
    pages: list[dict],
    *,
    selected_id: str,
    picker_label: str | None,
    sync_id: str | None,
) -> tuple[dict | None, str | None]:
    """선택 상자가 보여줄 페이지.

    분할 문서 읽기·새 저장은 `sync_id` 로 연다. 이걸 먼저 적용하지 않으면
    선택 상자에 남은 이전 라벨이 `index` 를 이기고, 방금 연 문서가 이전 문서로 덮인다.
    반환의 두 번째 값은 위젯을 만들기 전에 선택 상자에 써 넣을 라벨이다.
    """
    if not pages:
        return None, None
    labels = [wiki_page_label(page) for page in pages]
    by_id = {str(page.get("id") or ""): idx for idx, page in enumerate(pages)}
    if sync_id and sync_id in by_id:
        idx = by_id[sync_id]
        return pages[idx], labels[idx]
    if picker_label in labels:
        return pages[labels.index(picker_label)], None
    if selected_id in by_id:
        return pages[by_id[selected_id]], None
    return pages[0], None


def _select_page(pages: list[dict]) -> dict | None:
    labels = [wiki_page_label(page) for page in pages]
    sync_id = st.session_state.pop("_wiki_picker_sync", None)
    page, forced = resolve_wiki_picker(
        pages,
        selected_id=str(st.session_state.get("wiki_selected_page_id") or ""),
        picker_label=st.session_state.get("wiki_page_picker"),
        sync_id=str(sync_id) if sync_id else None,
    )
    if forced:
        st.session_state["wiki_page_picker"] = forced
    default_index = labels.index(forced) if forced and forced in labels else 0
    if not forced:
        current_id = str(st.session_state.get("wiki_selected_page_id") or "")
        for idx, item in enumerate(pages):
            if item.get("id") == current_id:
                default_index = idx
                break
    selected_label = st.selectbox("페이지 선택", labels, index=default_index, key="wiki_page_picker")
    if selected_label in labels:
        return pages[labels.index(selected_label)]
    return page or pages[default_index]


def _page_card(page: dict):
    title = escape(str(page.get("title") or "위키 페이지"))
    status = escape(option_label(STATUS_LABELS, page.get("status") or "draft"))
    surface = escape(option_label(SURFACE_LABELS, page.get("surface") or "wiki"))
    kind = escape(option_label(KIND_LABELS, page.get("kind") or "note"))
    summary = escape(str(page.get("summary") or ""))
    st.markdown(
        f"""
        <div style="border:1px solid rgba(148,163,184,.18);border-radius:8px;background:rgba(15,23,42,.28);padding:12px 13px;margin:10px 0 14px;">
          <div>
            <span style="display:block;color:rgba(148,163,184,.82);font-size:.74rem;margin-bottom:2px;">{surface} · {kind} · {status}</span>
            <b style="display:block;color:rgba(241,245,249,.98);font-size:1.02rem;margin-bottom:8px;">{title}</b>
          </div>
          <p style="margin:0 0 10px;color:rgba(226,232,240,.94);line-height:1.5;">{summary}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if page.get("body"):
        st.markdown("##### 본문")
        st.write(page["body"])
    if page.get("merge_history") or page.get("merged_into"):
        st.markdown("##### 병합/아카이브 기록")
        if page.get("merged_into"):
            st.caption(f"이 문서는 {page_display_name(page['merged_into'])}에 병합되어 보관 중입니다.")
        for event in page.get("merge_history") or []:
            st.caption(merge_event_caption(event))
            if event.get("reason"):
                st.write(event["reason"])
    split_children = wiki.split_children_for_page(page)
    if split_children:
        st.markdown("##### 분할된 세부 문서")
        for idx, child in enumerate(split_children[:6]):
            with st.container(border=True):
                st.markdown(f"**{child.get('title', '세부 문서')}**")
                st.caption(f"{option_label(SURFACE_LABELS, child.get('surface', 'wiki'))} · {option_label(KIND_LABELS, child.get('kind', 'note'))} · {option_label(STATUS_LABELS, child.get('status', 'draft'))}")
                if child.get("summary"):
                    st.caption(str(child["summary"])[:180])
                child_id = str(child.get("id") or "")
                if st.button("읽기", key=f"ai_wiki_split_child_{child_id or idx}", width="stretch"):
                    if not child_id:
                        st.toast("문서를 열지 못했습니다.")
                    else:
                        st.session_state["wiki_selected_page_id"] = child_id
                        st.session_state["_wiki_picker_sync"] = child_id
                        st.rerun()
    meta = pd.DataFrame([
        {"필드": "생성", "값": page.get("created_at", "")},
        {"필드": "수정", "값": page.get("updated_at", "")},
        {"필드": "신뢰도", "값": f"{float(page.get('confidence', 0.5)):.2f}"},
        {"필드": "출처", "값": ", ".join(display_source_ref(item) for item in page.get("source_refs", [])) or "-"},
    ])
    st.dataframe(meta, hide_index=True, width="stretch")


_ACTION_LABEL = {"delete": "삭제", "merge": "병합", "split": "분할"}


def _pending_curation_section() -> None:
    """AI 자동 큐레이션이 제안한 삭제·병합·분할 — 사용자 승인 전엔 실행되지 않는다."""
    try:
        proposals = wiki.list_pending_curation()
    except Exception:
        proposals = []
    if not proposals:
        return
    with st.expander(f"🛠 승인 대기 큐레이션 제안 {len(proposals)}건", expanded=True):
        st.caption("AI 가 제안한 파괴적 변경입니다. 적용 전까지 위키는 바뀌지 않습니다.")
        for prop in proposals:
            plan = prop.get("plan") or {}
            action = str(prop.get("action") or "")
            ids = [plan.get("target_id"), plan.get("target_page_id"), plan.get("source_page_id"),
                   *(plan.get("source_page_ids") or [])]
            ids = [i for i in ids if i]
            names = [page_display_name(i) for i in ids]
            with st.container(border=True):
                st.markdown(f"**{_ACTION_LABEL.get(action, '변경')}** · {', '.join(names) or '대상 없음'}")
                if plan.get("reason"):
                    st.caption(f"사유: {plan['reason']}")
                if prop.get("question"):
                    st.caption(f"출처 질문: {prop['question'][:120]}")
                c1, c2 = st.columns(2)
                if c1.button("적용", key=f"cur_apply_{prop['id']}", width="stretch"):
                    result = wiki.apply_pending_curation(prop["id"])
                    cached.clear_wiki_caches()
                    st.toast("적용 완료" if result else "적용 실패 — 대상이 이미 바뀌었을 수 있습니다")
                    st.rerun()
                if c2.button("거부", key=f"cur_reject_{prop['id']}", width="stretch"):
                    wiki.reject_pending_curation(prop["id"])
                    st.toast("제안 폐기")
                    st.rerun()
