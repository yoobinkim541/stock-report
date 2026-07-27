"""AI 콘솔 — World Memory + 대화형 컨텍스트 + 포트폴리오 전략랩.

기존 Cloudflare/Streamlit 대시보드 안에서 agent_console 코어를 직접 호출한다.
별도 Flask 포트 없이 같은 인증·사이드바·배포 경로를 사용한다.
"""
from __future__ import annotations

import html
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_console import agent, context, storage, wiki
from agent_console.portfolio_matrix_dsl import rsi_cash_program, run_portfolio_matrix_dsl
from dashboard import chat_references
from dashboard import data
from dashboard import wiki_browser


_SURFACES = {
    "market": "시장",
    "portfolio": "포트폴리오",
    "ticker": "종목",
    "paper": "모의투자",
    "lab": "전략랩",
}


def render():
    _inject_codex_css()
    st.markdown(
        """
        <div class="codex-console-title">
          <div>
            <div class="codex-kicker">stock-report agent</div>
            <h1>AI 콘솔</h1>
          </div>
          <span>그냥 질문하세요 — 맥락(시장·포트폴리오·종목·모의투자·전략)은 자동으로 잡습니다</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 맥락은 질문에서 자동 추론 — 마지막 추론값이 컨텍스트 글랜스/레일의 기준
    surface = _current_surface()
    hours = int(st.session_state.get("agent_hours", 72))

    pack = _safe_context(surface, hours)
    _context_glance(pack)

    tab_chat, tab_memory, tab_wiki, tab_lab, tab_connectors = st.tabs(
        ["대화", "시장 기억", "AI 위키", "전략 캔버스", "로컬 커넥터"])
    with tab_chat:
        _chat_tab(surface, pack)
    with tab_memory:
        _memory_tab(surface)
    with tab_wiki:
        _wiki_tab(surface, pack)
    with tab_lab:
        _lab_tab(surface)
    with tab_connectors:
        _connectors_tab()


_AUTO_CHAT = "auto"          # 단일 대화 스레드 키 (맥락은 메시지 단위로 자동 라우팅)
_PIN_AUTO = "자동"


def _current_surface() -> str:
    """현재 기준 맥락 — 수동 고정(pin)이 있으면 그것, 없으면 마지막 자동 추론값."""
    pin = st.session_state.get("agent_surface_pin", _PIN_AUTO)
    if pin in _SURFACES:
        return pin
    return st.session_state.get("agent_auto_surface", "market")


@st.cache_data(ttl=60, show_spinner=False)
def _context_pack(surface: str, hours: int) -> dict:
    return context.context_pack(surface, hours=hours)


def _safe_context(surface: str, hours: int) -> dict:
    try:
        return _context_pack(surface, hours)
    except Exception as exc:
        return {
            "ok": False,
            "surface": surface,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": {"events": [], "source_counts": [], "symbol_counts": []},
            "reports": [],
            "ml_activity": [],
            "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": [str(exc)]},
            "paper": {"errors": [str(exc)]},
            "models": {"items": []},
            "memory": [],
            "focus": context.focus_for_surface(surface),
        }


def _esc(value: object) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _context_glance_items(pack: dict) -> tuple[dict[str, str], ...]:
    sources = pack.get("sources") or {}
    reports = pack.get("reports") or []
    models = (pack.get("models") or {}).get("items") or []
    memory = pack.get("memory") or []
    return (
        {"label": "최근 이벤트", "value": f"{len(sources.get('events') or [])}건"},
        {"label": "누적 기억", "value": f"{len(memory)}건"},
        {"label": "모델 파일", "value": f"{len(models)}개"},
        {"label": "최신 리포트", "value": reports[0].get("name", "—") if reports else "—"},
    )


def _infer_context_detail(question: str, surface: str) -> str:
    q = str(question or "").lower().replace(" ", "")
    if surface == "market":
        if any(word in q for word in ("한국증시", "국내증시", "한국시장", "국내시장", "코스피", "코스닥", "kospi", "kosdaq")):
            return "한국증시"
        if any(word in q for word in ("미국증시", "미국시장", "나스닥", "s&p", "sp500", "다우", "qqq", "spy")):
            return "미국증시"
        if any(word in q for word in ("유가", "달러", "환율", "금리", "vix", "크레딧", "hyg", "lqd")):
            return "크로스에셋"
        return "전역시장"
    return _SURFACES.get(surface, surface)


def _count_label(count: int, cap: int | None = None, unit: str = "개") -> str:
    suffix = "+" if cap is not None and count >= cap else ""
    return f"{count}{unit}{suffix}"


def _rail_status_items(surface: str, pack: dict) -> list[dict[str, str]]:
    sources = pack.get("sources") or {}
    events = sources.get("events") or []
    memory = pack.get("memory") or []
    models = (pack.get("models") or {}).get("items") or []
    snapshot = pack.get("market_snapshot") or {}
    quote_count = len(snapshot.get("quotes") or [])
    detail = str(st.session_state.get("agent_auto_detail") or _SURFACES.get(surface, surface))
    engine = str(st.session_state.get("agent_last_engine") or "대기")
    items = [
        {"label": "맥락", "value": _SURFACES.get(surface, surface)},
        {"label": "세부", "value": detail},
        {"label": "엔진", "value": engine},
        {"label": "events", "value": _count_label(len(events), 40, "개")},
        {"label": "memory", "value": _count_label(len(memory), 50, "개")},
        {"label": "models", "value": _count_label(len(models), None, "개")},
    ]
    if snapshot:
        items.append({"label": "실시간", "value": str(snapshot.get("status") or "unavailable")})
        items.append({"label": "quotes", "value": _count_label(quote_count, None, "개")})
    return items


def _context_glance(pack: dict):
    items = _context_glance_items(pack)
    st.markdown(
        "<div class='codex-glance'>"
        + "".join(
            f"<div><span>{_esc(item['label'])}</span><b>{_esc(item['value'])}</b></div>"
            for item in items
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    focus = pack.get("focus") or []
    if focus:
        st.caption(" · ".join(focus[:4]))

    sources = pack.get("sources") or {}
    source_counts = sources.get("source_counts") or []
    symbol_counts = sources.get("symbol_counts") or []
    if source_counts or symbol_counts:
        st.caption(
            "소스 " + (" · ".join(f"{name} {cnt}" for name, cnt in source_counts[:5]) or "—")
            + "  /  심볼 " + (" · ".join(f"{name} {cnt}" for name, cnt in symbol_counts[:6]) or "—")
        )


def _chat_tab(surface: str, pack: dict):
    _ensure_chat_state(_AUTO_CHAT)
    chat_key = _chat_key(_AUTO_CHAT)
    chat_col, rail_col = st.columns([1.48, 0.72], gap="large")

    with chat_col:
        pin = st.session_state.get("agent_surface_pin", _PIN_AUTO)
        mode_label = ("맥락 자동" if pin == _PIN_AUTO
                      else f"맥락 고정 · {_SURFACES.get(pin, pin)}")
        st.markdown(
            f"<div class='codex-chat-head'><b>{mode_label}"
            f"<span class='codex-chip'>{_SURFACES.get(surface, surface)}</span></b>"
            f"<span>{pack.get('generated_at', '')}</span></div>",
            unsafe_allow_html=True,
        )
        pending = _quick_prompts()
        if pending:
            _run_agent_question_auto(pending, pack)

        for idx, msg in enumerate(st.session_state[chat_key][-16:]):
            role_raw = str(msg.get("role", "assistant")).strip().lower()
            role = "user" if role_raw in {"user", "human"} else "assistant"
            with st.chat_message(role):
                st.markdown(msg.get("content", ""))
                if msg.get("meta"):
                    st.caption(msg["meta"])
                if role == "assistant" and msg.get("references"):
                    _render_reference_panel(msg["references"], key_prefix=f"{chat_key}_{idx}")

        user_text = st.chat_input("무엇이든 질문하기 — 포트폴리오·종목·시장·모의투자·전략",
                                  key="agent_chat_input_auto")
        if user_text:
            _run_agent_question_auto(user_text, pack)
            st.rerun()

    with rail_col:
        _chat_context_rail(surface, pack)


def _chat_key(surface: str) -> str:
    return f"agent_chat_messages_{str(surface or 'market').strip().lower()}"


def _prompt_key(surface: str) -> str:
    return f"agent_show_prompt_{str(surface or 'market').strip().lower()}"


def _ensure_chat_state(surface: str):
    key = _chat_key(surface)
    if key in st.session_state:
        return
    st.session_state[key] = [
        {
            "role": "assistant",
            "content": "그냥 질문하시면 됩니다. 질문 내용에 따라 시장·포트폴리오·종목·모의투자·전략 맥락을 자동으로 잡아 "
                       "시장 자료·모의투자 원장·World Memory 안에서 답합니다.",
            "meta": "local context ready · 맥락 자동",
        }
    ]


_AGENT_PROGRESS_LABELS = (
    "맥락 읽는 중",
    "질문 의도 고정 중",
    "필요 데이터 확인 중",
    "LLM 분석 요청 중",
    "답변 정리 중",
)


def _quick_prompt_texts() -> tuple[str, ...]:
    return (
        "오늘 시장 변화가 어디서 시작됐는지 추적해줘",
        "내 포트폴리오에서 먼저 줄여야 할 리스크 봐줘",
        "최근 대화에서 위키로 남길 판단을 정리해줘",
    )


def _chat_progress_labels() -> tuple[str, ...]:
    return _AGENT_PROGRESS_LABELS


def _answer_agent_fast(question: str, surface: str) -> dict:
    try:
        return agent.answer(question, surface, async_postprocess=True)
    except TypeError as exc:
        if "async_postprocess" not in str(exc):
            raise
        return agent.answer(question, surface)


def _safe_status_update(status, **kwargs) -> None:
    update = getattr(status, "update", None)
    if not callable(update):
        return
    try:
        update(**kwargs)
    except Exception:
        pass


def _answer_with_progress(question: str, surface: str) -> dict:
    labels = _chat_progress_labels()
    status_factory = getattr(st, "status", None)
    if callable(status_factory):
        status_context = status_factory(labels[0], expanded=True)
        with status_context as status:
            updater = status or status_context
            for label in labels[1:]:
                _safe_status_update(updater, label=label, state="running", expanded=True)
            result = _answer_agent_fast(question, surface)
            post = ((result.get("context") or {}).get("postprocess") or {}).get("wiki_autocurate")
            done_label = "답변 표시 완료"
            if post == "queued":
                done_label = "답변 표시 완료 · 위키 정리는 뒤에서 진행"
            _safe_status_update(updater, label=done_label, state="complete", expanded=False)
            return result
    with st.spinner(labels[-2] if len(labels) >= 2 else "답변 생성 중"):
        return _answer_agent_fast(question, surface)


def _quick_prompts() -> str | None:
    """도메인을 가로지르는 추천 질문 — 눌러도 되고, 그냥 아래에 입력해도 된다."""
    prompts = _quick_prompt_texts()
    cols = st.columns(len(prompts))
    for idx, text in enumerate(prompts):
        if cols[idx].button(text, key=f"agent_quick_auto_{idx}", width="stretch"):
            return text
    return None


def _run_agent_question_auto(question: str, pack: dict):
    """단일 스레드 UX — 질문에서 맥락을 추론(또는 pin)해 실행하고 추론값을 기억한다."""
    question = str(question or "").strip()
    if not question:
        return
    pin = st.session_state.get("agent_surface_pin", _PIN_AUTO)
    if pin in _SURFACES:
        surface = pin
    else:
        prev = st.session_state.get("agent_auto_surface", "market")
        surface = agent.infer_surface(question, default=prev)
    st.session_state["agent_auto_surface"] = surface
    st.session_state["agent_auto_detail"] = _infer_context_detail(question, surface)
    _run_agent_question(question, surface, pack, chat_key=_chat_key(_AUTO_CHAT))


def _run_agent_question(question: str, surface: str, pack: dict, chat_key: str | None = None):
    question = str(question or "").strip()
    if not question:
        return
    if chat_key is None:
        _ensure_chat_state(surface)
        chat_key = _chat_key(surface)
    else:
        _ensure_chat_state(_AUTO_CHAT)
    st.session_state[chat_key].append({"role": "user", "content": question})
    result = _answer_with_progress(question, surface)
    if result.get("ok"):
        ctx = result.get("context") or {}
        references = chat_references.build_answer_references(
            question,
            surface,
            pack,
            result.get("answer", ""),
        )
        ref_counts = {
            "wiki": len(references.get("wiki") or []),
            "sources": len(references.get("sources") or []),
        }
        meta = f"맥락 {_SURFACES.get(surface, surface)}"
        if ctx:
            meta += f" · events {ctx.get('event_count', 0)} · memory {ctx.get('memory_count', 0)}"
            if ctx.get("market_quote_count") is not None:
                meta += f" · quotes {ctx.get('market_quote_count', 0)}"
        engine = str(ctx.get("engine") or "")
        if engine:
            st.session_state["agent_last_engine"] = "규칙" if engine == "local-rules" else engine
            # 어떤 엔진이 답했는지 정직 표기 — local-rules = LLM 미개입 규칙 답변
            meta += f" · 엔진 {'⚙️ 규칙' if engine == 'local-rules' else '🤖 ' + engine}"
        post = (ctx.get("postprocess") or {}).get("wiki_autocurate")
        if post:
            meta += f" · 후처리 {post}"
        if ref_counts["wiki"] or ref_counts["sources"]:
            meta += f" · 참고 위키 {ref_counts['wiki']} · 출처 {ref_counts['sources']}"
        if ctx.get("context_error"):
            meta = f"{meta} · context fallback" if meta else "context fallback"
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": result.get("answer", ""),
            "meta": meta,
            "references": references,
        })
    else:
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": result.get("error", "답변 생성 실패"),
            "meta": "error",
        })


def _open_wiki_reference(page_id: str | None) -> None:
    page_id = str(page_id or "").strip()
    if page_id:
        st.session_state["agent_wiki_selected_page_id"] = page_id
    wiki_page = st.session_state.get("_wiki_page")
    if wiki_page is not None:
        st.switch_page(wiki_page)


def _render_reference_panel(references: dict, key_prefix: str) -> None:
    wiki_refs = list(references.get("wiki") or [])
    source_refs = list(references.get("sources") or [])
    if not wiki_refs and not source_refs:
        return

    with st.expander(f"참고 자료 · 위키 {len(wiki_refs)} · 출처 {len(source_refs)}", expanded=False):
        if wiki_refs:
            st.markdown("##### 참고 위키")
            for idx, ref in enumerate(wiki_refs):
                with st.container(border=True):
                    st.markdown(f"**{_esc(ref.get('title') or '위키 페이지')}**")
                    st.caption(
                        f"{_esc(ref.get('surface') or 'wiki')} · {_esc(ref.get('page_kind') or 'note')} · "
                        f"{_esc(ref.get('status') or 'draft')} · {_esc(ref.get('verification_status') or 'unverified')}"
                    )
                    if ref.get("summary"):
                        st.write(ref["summary"])
                    if ref.get("source_refs"):
                        st.caption("원문 단서: " + " · ".join(str(item) for item in ref.get("source_refs")[:4]))
                    if st.button("AI 위키에서 열기", key=f"{key_prefix}_wiki_{idx}_{ref.get('id')}", width="stretch"):
                        _open_wiki_reference(ref.get("id"))

        if source_refs:
            st.markdown("##### 원문 출처")
            for idx, ref in enumerate(source_refs):
                with st.container(border=True):
                    st.markdown(f"**{_esc(ref.get('title') or '원문 출처')}**")
                    meta_bits = [ref.get("source"), ref.get("published_at")]
                    st.caption(" · ".join(str(item) for item in meta_bits if item))
                    if ref.get("summary"):
                        st.write(ref["summary"])
                    url = str(ref.get("url") or "").strip()
                    path = ((ref.get("metadata") or {}).get("path") or "")
                    if url:
                        st.markdown(f"[원문 열기]({url})")
                    elif path:
                        st.caption(f"파일 경로: {path}")
                    else:
                        st.caption(ref.get("reason") or "출처 정보")


def _chat_context_rail(surface: str, pack: dict):
    st.markdown("##### Context")
    sources = pack.get("sources") or {}
    events = sources.get("events") or []
    memory = pack.get("memory") or []
    reports = pack.get("reports") or []
    status_items = _rail_status_items(surface, pack)

    st.markdown(
        '<div class="codex-rail-card">'
        + "".join(
            f"<div><span>{_esc(item['label'])}</span><b>{_esc(item['value'])}</b></div>"
            for item in status_items
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    with st.expander("⚙️ 설정", expanded=False):
        st.selectbox("맥락 고정", [_PIN_AUTO, *list(_SURFACES)],
                     format_func=lambda k: k if k == _PIN_AUTO else _SURFACES.get(k, k),
                     key="agent_surface_pin",
                     help="기본은 자동 — 질문 내용으로 맥락을 추론합니다. 특정 맥락에 고정하고 싶을 때만 바꾸세요.")
        st.selectbox("수집 범위", [24, 72, 168, 336], index=1,
                     format_func=lambda h: f"{h}h" if h < 168 else f"{h // 24}d",
                     key="agent_hours")
        if st.button("메모리 적재", width="stretch",
                     help="최근 뉴스/리포트/ML 원장을 World Memory로 적재"):
            with st.spinner("최근 컨텍스트를 World Memory에 적재 중..."):
                result = context.ingest_recent_memory(hours=int(st.session_state.get("agent_hours", 72)))
            _context_pack.clear()
            st.toast(f"메모리 {result.get('changed', 0)}건 반영")

    c1, c2 = st.columns(2)
    prompt_key = _prompt_key(surface)
    if c1.button("프롬프트", key=f"agent_prompt_{surface}", width="stretch"):
        st.session_state[prompt_key] = not st.session_state.get(prompt_key, False)
    if c2.button("초기화", key="agent_clear_auto", width="stretch"):
        st.session_state.pop(_chat_key(_AUTO_CHAT), None)
        _ensure_chat_state(_AUTO_CHAT)
        st.rerun()

    if st.session_state.get(prompt_key):
        st.code(agent.build_context_prompt(surface), language="text")

    st.markdown("##### Events")
    if events:
        for item in events[:7]:
            title = item.get("title") or item.get("summary") or "제목 없음"
            st.markdown(f"<div class='codex-feed-item'><b>{item.get('source', 'source')}</b><span>{title}</span></div>",
                        unsafe_allow_html=True)
    else:
        st.caption("최근 이벤트 없음")

    st.markdown("##### Memory")
    if memory:
        for item in memory[:5]:
            st.markdown(f"<div class='codex-feed-item'><b>{item.get('kind', 'memory')}</b>"
                        f"<span>{item.get('title', '제목 없음')}</span></div>",
                        unsafe_allow_html=True)
    else:
        st.caption("World Memory 비어 있음")

    if reports:
        st.caption(f"latest report: {reports[0].get('name')}")
    paper = pack.get("paper") or {}
    if paper.get("errors"):
        st.caption("paper: " + " · ".join(paper["errors"]))
    if pack.get("context_error"):
        st.warning(f"컨텍스트 일부를 불러오지 못했습니다: {pack['context_error']}")


def _memory_tab(surface: str):
    st.markdown("##### World Memory")
    st.caption("단일 월드 메모리 — 뉴스 크론·텔레그램 /ask·종목분석 🧭 카드와 같은 축적을 읽고 씁니다.")
    with st.form("agent_memory_add", clear_on_submit=True):
        c1, c2 = st.columns([1.2, 0.8])
        title = c1.text_input("제목")
        symbols = c2.text_input("심볼/태그", placeholder="QQQ, NVDA, oil")
        body = st.text_area("관찰 내용", height=100)
        submitted = st.form_submit_button("수동 기억 추가", type="primary")
        if submitted:
            context.log_world_issue(
                title or body[:80] or "수동 메모",
                category="메모",
                importance="high",
                tickers=[x.strip().upper() for x in symbols.replace(",", " ").split() if x.strip()],
                body=body,
                source=f"dashboard:manual:{surface}",
            )
            _context_pack.clear()
            st.toast("시장 기억 추가 완료")

    search = st.text_input("검색", key="_wm_search", placeholder="제목·본문·티커로 검색")
    rows = context.world_memory_rows(limit=120, query=search.strip())
    if not rows:
        st.info("검색 결과가 없습니다." if search.strip() else
                "아직 저장된 시장 기억이 없습니다. 상단의 메모리 적재를 먼저 실행해 보세요.")
        return
    df = pd.DataFrame([{
        "시각": r.get("observed_at"),
        "출처": r.get("source"),
        "종류": r.get("kind"),
        "제목": r.get("title"),
        "심볼": ", ".join(r.get("symbols") or []),
        "중요도": r.get("impact"),
    } for r in rows])
    event = st.dataframe(df, hide_index=True, width="stretch", height=360,
                          on_select="rerun", selection_mode="single-row", key="_wm_tbl")
    _memory_detail(event, rows)


def _memory_detail(event, rows):
    """선택 행 상세 카드 — 본문(body) 전문 표시. research.py `_screener_detail`과 동일 패턴."""
    from dashboard import theme
    try:
        sel = event.selection.rows
    except Exception:
        sel = []
    if not sel or sel[0] >= len(rows):
        return
    r = rows[sel[0]]
    symbols = ", ".join(r.get("symbols") or []) or "—"
    body_html = html.escape(r.get("body") or "").replace("\n", "<br>") or "본문 없음"
    url = str(r.get("url") or "")
    link_html = ""
    if url.startswith("http://") or url.startswith("https://"):   # href 삽입 전 스킴 검증(안전)
        link_html = (f'<div style="margin-top:10px">'
                    f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
                    f'style="color:{theme.BLUE};font-size:0.85rem">원문 보기 →</a></div>')
    st.markdown(
        f'<div style="background:{theme.PANEL};border:1px solid {theme.BORDER};'
        f'border-left:4px solid {theme.BLUE};border-radius:12px;padding:12px 16px;margin-top:8px">'
        f'<div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap">'
        f'<b style="font-size:1.05rem">{html.escape(r.get("title") or "제목 없음")}</b>'
        f'<span style="color:{theme.MUTED};font-size:0.78rem">{r.get("observed_at") or ""} · '
        f'{r.get("source") or ""} · {symbols} · {r.get("impact") or ""}</span></div>'
        f'<div style="margin-top:8px;white-space:pre-wrap;font-size:0.9rem">{body_html}</div>'
        f'{link_html}</div>',
        unsafe_allow_html=True)


def _wiki_tab(surface: str, pack: dict):
    return wiki_browser.render_wiki_tab(surface, pack)
