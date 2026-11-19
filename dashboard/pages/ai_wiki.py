"""AI 위키 — 위키의 단일 화면.

문서(탐색기·읽기/편집·백링크·대화 승격) · 인사이트(온톨로지) · 관리(분포·권고·승인 대기 큐레이션)를
한 페이지에서 고른다. AI 콘솔의 위키 참고 카드도 이 페이지로 연다.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from agent_console import wiki
from dashboard import cached, ui_actions, wiki_browser, wiki_ontology_view
from dashboard.wiki_browser import KIND_LABELS, SURFACE_LABELS, build_wiki_health_model, option_label, page_display_name

SECTIONS = ("문서", "인사이트", "관리")
# 위젯 키는 위젯 생성 뒤에 바꿀 수 없어, 다음 렌더에 열 구역을 이 키로 넘긴다.
_NEXT_SECTION_KEY = "_ai_wiki_section_next"

_RECOMMENDATION_LABELS = {
    "balance": "균형",
    "curation": "정리",
    "trust": "신뢰",
    "connectivity": "연결",
    "hygiene": "정리",
    "healthy": "양호",
    "general": "일반",
}
_ACTION_LABEL = {"delete": "삭제", "merge": "병합", "split": "분할"}


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
    proposals = _pending_curation()
    if proposals:
        st.info(f"🛠 AI 큐레이션 제안 {len(proposals)}건이 승인을 기다립니다 — **관리** 구역에서 적용하거나 거부하세요.")

    section = _section_control()
    if section == "인사이트":
        pages_all, _, _ = wiki_browser._cached_wiki_snapshot()
        wiki_ontology_view.render_section(pages_all, on_open=open_document)
    elif section == "관리":
        _management_section(proposals)
    else:
        wiki_browser.render_wiki_tab(str(st.session_state.get("agent_auto_surface") or "market"))


def open_document(page_id: str) -> None:
    """다음 렌더에서 문서 구역을 열고 page_id 를 읽기 모드로 보여 준다 (AI 콘솔 참고 카드·인사이트)."""
    if page_id:
        st.session_state["agent_wiki_selected_page_id"] = page_id
    st.session_state["wiki_editor_mode"] = "read"
    st.session_state[_NEXT_SECTION_KEY] = "문서"


def _section_control() -> str:
    """선택한 구역만 렌더한다 — st.tabs 는 모든 본문을 매번 실행한다."""
    pending = st.session_state.pop(_NEXT_SECTION_KEY, None)
    if pending in SECTIONS:
        st.session_state["_ai_wiki_section"] = pending
    selected = st.segmented_control(
        "위키 구역",
        options=list(SECTIONS),
        default=SECTIONS[0],
        key="_ai_wiki_section",
        label_visibility="collapsed",
        width="stretch",
    )
    return selected if selected in SECTIONS else SECTIONS[0]


def _management_section(proposals: list[dict]) -> None:
    _pending_curation_section(proposals)
    _pipeline_health_panel()

    pages_all, search_health, lint = wiki_browser._cached_wiki_snapshot()
    health = build_wiki_health_model(pages_all, search_health=search_health, lint=lint)

    surface_counts = health.get("surface_counts") or {}
    if surface_counts:
        st.markdown("##### 그룹 분포")
        surface_series = pd.Series({
            option_label(SURFACE_LABELS, key): count for key, count in surface_counts.items()
        }).sort_values(ascending=False)
        st.bar_chart(surface_series, height=180, horizontal=True)
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
                st.caption(option_label(_RECOMMENDATION_LABELS, rec.get("category", "general")))
                st.write(rec.get("detail", ""))


def _pipeline_health_panel() -> None:
    health = cached.wiki_pipeline_health()
    source_section = health.get("source_health") or {}
    wiki_section = health.get("wiki_health") or {}
    curation_section = health.get("curation_health") or {}
    overall = source_section.get("overall") or {}

    st.markdown("##### 파이프라인 헬스")
    if health.get("error"):
        st.warning(ui_actions.user_message(health.get("error"), "헬스 리포트를 불러오지 못했습니다."))
        return

    cols = st.columns(5)
    cols[0].metric("소스", f"{overall.get('healthy_sources', 0)}/{overall.get('expected_sources', 0)}")
    gap_names = overall.get("gap_source_names") or []
    cols[1].metric(
        "소스 공백·차단",
        f"{overall.get('gap_sources', overall.get('stale_sources', 0))}",
        help=("수집이 멈춘 소스: " + ", ".join(gap_names)) if gap_names else "수집이 멈춘 소스 없음",
    )
    cols[2].metric("출처 확인", f"{wiki_section.get('source_backed_count', 0)}")
    cols[3].metric("미검증", f"{wiki_section.get('unverified_count', 0)}")
    cols[4].metric("미연결 요약", f"{curation_section.get('source_digest_unlinked_count', 0)}")

    st.caption(
        f"오래된 문서 {wiki_section.get('stale_count', 0)} · "
        f"미사용 {wiki_section.get('unused_count', 0)} · "
        f"열린 질문 {wiki_section.get('open_question_count', 0)}"
    )
    recs = health.get("recommendations") or []
    if recs:
        st.markdown("**권장 액션**")
        for rec in recs[:3]:
            st.markdown(
                f"- [{escape(str(rec.get('category') or '기타'))}] {escape(str(rec.get('title') or '—'))}"
                f" — {escape(str(rec.get('detail') or '—'))}"
            )
    else:
        st.caption("즉시 조치가 필요한 항목이 없습니다.")


def _pending_curation() -> list[dict]:
    try:
        return wiki.list_pending_curation()
    except Exception:
        return []


def _pending_curation_section(proposals: list[dict]) -> None:
    """AI 자동 큐레이션이 제안한 삭제·병합·분할 — 사용자 승인 전엔 실행되지 않는다."""
    if not proposals:
        return
    st.markdown(f"##### 🛠 승인 대기 큐레이션 제안 {len(proposals)}건")
    st.caption("AI 가 제안한 파괴적 변경입니다. 적용 전까지 위키는 바뀌지 않습니다.")
    for prop in proposals:
        plan = prop.get("plan") or {}
        action = str(prop.get("action") or "")
        ids = [plan.get("target_id"), plan.get("target_page_id"), plan.get("source_page_id"),
               *(plan.get("source_page_ids") or [])]
        names = [page_display_name(i) for i in ids if i]
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
                wiki_browser._cached_wiki_snapshot.clear()
                st.toast("적용 완료" if result else "적용 실패 — 대상이 이미 바뀌었을 수 있습니다")
                st.rerun()
            if c2.button("거부", key=f"cur_reject_{prop['id']}", width="stretch"):
                wiki.reject_pending_curation(prop["id"])
                st.toast("제안 폐기")
                st.rerun()
