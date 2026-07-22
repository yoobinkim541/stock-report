from __future__ import annotations

import pandas as pd
import streamlit as st

from agent_console import wiki


SURFACE_OPTIONS = ["all", "market", "portfolio", "ticker", "paper", "lab", "wiki"]
STATUS_OPTIONS = ["all", "draft", "reviewed", "stable", "archived"]
KIND_OPTIONS = ["all", "note", "playbook", "decision", "risk", "concept"]


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

    stats = wiki.stats()
    cols = st.columns(4)
    cols[0].metric("페이지", f"{stats.get('total', 0)}")
    cols[1].metric("초안", f"{stats.get('status_counts', {}).get('draft', 0)}")
    cols[2].metric("검토", f"{stats.get('status_counts', {}).get('reviewed', 0)}")
    latest = stats.get("latest") or {}
    cols[3].metric("최근", (latest.get("title", "—") or "—")[:20])

    query = st.text_input("검색", key="wiki_query", placeholder="손실한도, 레버리지, SOL, 중동, 크레딧...")
    f1, f2, f3 = st.columns([1, 1, 1])
    surface = f1.selectbox("surface", SURFACE_OPTIONS, index=0, key="wiki_surface_filter")
    status = f2.selectbox("status", STATUS_OPTIONS, index=0, key="wiki_status_filter")
    kind_filter = f3.selectbox("kind", KIND_OPTIONS, index=0, key="wiki_kind_filter")

    pages = wiki.list_pages(query=query, surface=surface, status=status, limit=60)
    if kind_filter != "all":
        pages = [page for page in pages if page.get("kind") == kind_filter]

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
                c1, c2 = st.columns(2)
                if c1.button("현재 편집값으로 저장", type="primary", width="stretch"):
                    st.session_state["wiki_save_trigger"] = selected["id"]
                if c2.button("삭제", width="stretch"):
                    if wiki.delete_page(selected["id"]):
                        st.toast("위키 페이지 삭제 완료")
                        st.rerun()

    with right:
        st.markdown("##### 편집기")
        selected_page = wiki.get_page(st.session_state.get("wiki_selected_page_id", ""))
        default_page = selected_page or _blank_page(query=query, surface=surface)
        with st.form("wiki_editor", clear_on_submit=False):
            title = st.text_input("제목", value=default_page.get("title", ""))
            editor_surface = st.selectbox(
                "surface",
                SURFACE_OPTIONS[1:],
                index=max(0, SURFACE_OPTIONS[1:].index(default_page.get("surface", "market"))
                      if default_page.get("surface", "market") in SURFACE_OPTIONS[1:] else 0),
                key="wiki_editor_surface",
            )
            kind = st.selectbox(
                "kind",
                KIND_OPTIONS[1:],
                index=max(0, KIND_OPTIONS[1:].index(default_page.get("kind", "note"))
                      if default_page.get("kind", "note") in KIND_OPTIONS[1:] else 0),
                key="wiki_editor_kind",
            )
            editor_status = st.selectbox(
                "status",
                ["draft", "reviewed", "stable", "archived"],
                index=max(0, ["draft", "reviewed", "stable", "archived"].index(default_page.get("status", "draft"))
                      if default_page.get("status", "draft") in ["draft", "reviewed", "stable", "archived"] else 0),
                key="wiki_editor_status",
            )
            tags = st.text_input("tags", value=", ".join(default_page.get("tags", [])))
            summary = st.text_area("요약", value=default_page.get("summary", ""), height=130)
            body = st.text_area("본문", value=default_page.get("body", ""), height=220)
            source_refs = st.text_input("source refs", value=", ".join(default_page.get("source_refs", [])))
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
                        "source_refs": [item.strip() for item in source_refs.replace(";", ",").split(",") if item.strip()],
                        "confidence": default_page.get("confidence", 0.7),
                    }
                )
                st.session_state["wiki_selected_page_id"] = saved.get("id")
                st.success("위키 페이지를 저장했습니다.")
                st.rerun()

        st.markdown("##### 최근 대화에서 승격")
        st.caption("이 페이지는 호환용입니다. 최신 콘솔에서는 대화 탭의 위키 브라우저를 사용합니다.")

        with st.expander("위키가 챗봇에 들어가는 방식", expanded=False):
            section = wiki.build_context_section(query=query or default_page.get("title", ""), surface=surface, limit=4)
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


def _select_page(pages: list[dict]) -> dict | None:
    labels = [
        f"{page.get('title', '위키')} · {page.get('status', 'draft')} · {page.get('surface', 'wiki')}"
        for page in pages
    ]
    current_id = st.session_state.get("wiki_selected_page_id", "")
    default_index = 0
    for idx, page in enumerate(pages):
        if page.get("id") == current_id:
            default_index = idx
            break
    selected_label = st.selectbox("페이지 선택", labels, index=default_index, key="wiki_page_picker")
    return pages[labels.index(selected_label)] if selected_label in labels else pages[default_index]


def _page_card(page: dict):
    st.markdown(
        f"""
        <div style="border:1px solid rgba(148,163,184,.18);border-radius:8px;background:rgba(15,23,42,.28);padding:12px 13px;margin:10px 0 14px;">
          <div>
            <span style="display:block;color:rgba(148,163,184,.82);font-size:.74rem;margin-bottom:2px;">{page.get('surface', 'wiki')} · {page.get('kind', 'note')} · {page.get('status', 'draft')}</span>
            <b style="display:block;color:rgba(241,245,249,.98);font-size:1.02rem;margin-bottom:8px;">{page.get('title', '위키 페이지')}</b>
          </div>
          <p style="margin:0 0 10px;color:rgba(226,232,240,.94);line-height:1.5;">{page.get('summary', '')}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if page.get("body"):
        st.markdown("##### 본문")
        st.write(page["body"])
    meta = pd.DataFrame([
        {"필드": "생성", "값": page.get("created_at", "")},
        {"필드": "수정", "값": page.get("updated_at", "")},
        {"필드": "신뢰도", "값": f"{float(page.get('confidence', 0.5)):.2f}"},
        {"필드": "source refs", "값": ", ".join(page.get("source_refs", [])) or "—"},
    ])
    st.dataframe(meta, hide_index=True, width="stretch")
