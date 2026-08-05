"""AI 콘솔 — World Memory + 대화형 컨텍스트 + 포트폴리오 전략랩."""
from __future__ import annotations

import html
import os
import re
import sys
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_console import agent, context, storage
from agent_console import portfolio_matrix_dsl
from dashboard import cached, chat_feedback, chat_references, data, strategy_studio, wiki_browser


_SURFACES = {
    "market": "시장",
    "portfolio": "포트폴리오",
    "ticker": "종목",
    "paper": "모의투자",
    "lab": "전략랩",
}
_AUTO_CHAT = "auto"
_PIN_AUTO = "자동"
_AGENT_PROGRESS_LABELS = (
    "맥락 읽는 중",
    "질문 의도 고정 중",
    "필요 데이터 확인 중",
    "LLM 분석 요청 중",
    "답변 정리 중",
)

_CANVAS_FIELD_LABELS = {
    "buy_rsi": "매수 RSI",
    "sell_rsi": "현금화 RSI",
    "max_loss": "최대 손실한도 %",
    "hypothesis": "전략 가설",
    "allocations": "포트폴리오 비중",
}

_RSI_PAIR_RE = re.compile(r"rsi\D{0,6}(\d{1,2})\s*/\s*(\d{1,2})", re.IGNORECASE)
_RSI_BUY_RE = re.compile(r"매수\s*rsi\D{0,10}?(\d{1,2})", re.IGNORECASE)
_RSI_SELL_RE = re.compile(r"현금화\s*rsi\D{0,10}?(\d{1,2})", re.IGNORECASE)
_MAX_LOSS_RE = re.compile(r"(?:손실|손절)\D{0,10}?(\d+(?:\.\d+)?)\s*(?:%|퍼센트)")


def render():
    _inject_codex_css()
    st.markdown(
        """
        <div class="codex-console-title">
          <div>
            <div class="codex-kicker">stock-report agent</div>
            <h1>AI 콘솔</h1>
          </div>
          <span>그냥 질문하세요. 맥락은 자동으로 잡고, 답변 아래에 참고 위키·출처·검토 경로를 남깁니다.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    surface = _current_surface()
    hours = int(st.session_state.get("agent_hours", 72))
    pack = _safe_context(surface, hours)
    _context_glance(pack)

    tab_chat, tab_memory, tab_wiki, tab_lab, tab_connectors = st.tabs(
        ["대화", "시장 기억", "AI 위키", "전략 캔버스", "로컬 커넥터"]
    )
    with tab_chat:
        _chat_tab(surface, pack)
    with tab_memory:
        _memory_tab(surface)
    with tab_wiki:
        _wiki_tab(surface, pack)
    with tab_lab:
        _lab_tab(surface, pack)
    with tab_connectors:
        _connectors_tab()


def _current_surface() -> str:
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
            "context_error": str(exc),
        }


def _esc(value: object) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _context_glance_items(pack: dict) -> list[dict]:
    sources = pack.get("sources") or {}
    reports = pack.get("reports") or []
    models = (pack.get("models") or {}).get("items") or []
    memory = pack.get("memory") or []
    return [
        {"label": "최근 이벤트", "value": f"{len(sources.get('events') or [])}건"},
        {"label": "누적 기억", "value": f"{len(memory)}건"},
        {"label": "모델 파일", "value": f"{len(models)}개"},
        {"label": "최신 리포트", "value": reports[0].get("name", "—") if reports else "—"},
    ]


def _context_glance(pack: dict):
    sources = pack.get("sources") or {}
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
    source_counts = sources.get("source_counts") or []
    symbol_counts = sources.get("symbol_counts") or []
    if source_counts or symbol_counts:
        st.caption(
            "소스 " + (" · ".join(f"{name} {cnt}" for name, cnt in source_counts[:5]) or "—")
            + "  /  심볼 " + (" · ".join(f"{name} {cnt}" for name, cnt in symbol_counts[:6]) or "—")
        )


def _count_value(rows, *, cap: int | None = None) -> str:
    count = len(rows or [])
    suffix = "+" if cap is not None and count >= cap else ""
    return f"{count}개{suffix}"


def _rail_status_items(surface: str, pack: dict) -> list[dict]:
    sources = pack.get("sources") or {}
    snapshot = pack.get("market_snapshot") or {}
    detail = str(st.session_state.get("agent_auto_detail") or _SURFACES.get(surface, surface))
    engine = str(st.session_state.get("agent_last_engine") or "대기")
    return [
        {"label": "맥락", "value": _SURFACES.get(surface, surface)},
        {"label": "세부", "value": detail},
        {"label": "엔진", "value": engine},
        {"label": "events", "value": _count_value(sources.get("events") or [], cap=40)},
        {"label": "memory", "value": _count_value(pack.get("memory") or [], cap=50)},
        {"label": "models", "value": _count_value((pack.get("models") or {}).get("items") or [])},
        {"label": "quotes", "value": _count_value(snapshot.get("quotes") or [])},
        {"label": "실시간", "value": str(snapshot.get("status") or "unavailable")},
    ]


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


def _chat_tab(surface: str, pack: dict):
    _ensure_chat_state(_AUTO_CHAT)
    chat_key = _chat_key(_AUTO_CHAT)
    chat_col, rail_col = st.columns([1.48, 0.72], gap="large")

    with chat_col:
        pin = st.session_state.get("agent_surface_pin", _PIN_AUTO)
        mode_label = "맥락 자동" if pin == _PIN_AUTO else f"맥락 고정 · {_SURFACES.get(pin, pin)}"
        st.markdown(
            f"<div class='codex-chat-head'><b>{_esc(mode_label)}"
            f"<span class='codex-chip'>{_esc(_SURFACES.get(surface, surface))}</span></b>"
            f"<span>{_esc(pack.get('generated_at', ''))}</span></div>",
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
                if role == "assistant":
                    if msg.get("references"):
                        _render_reference_panel(msg["references"], key_prefix=f"{chat_key}_{idx}")
                    if msg.get("trace"):
                        _render_trace_panel(msg["trace"], key_prefix=f"{chat_key}_{idx}")
                    _render_feedback_controls(msg, surface, key_prefix=f"{chat_key}_{idx}")

        user_text = st.chat_input(
            "무엇이든 질문하기 — 포트폴리오·종목·시장·모의투자·전략",
            key="agent_chat_input_auto",
        )
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
    st.session_state[key] = [{
        "role": "assistant",
        "content": "그냥 질문하시면 됩니다. 질문 내용에 따라 시장·포트폴리오·종목·모의투자·전략 맥락을 자동으로 잡아 답합니다.",
        "meta": "local context ready · 맥락 자동",
    }]


def _quick_prompt_texts() -> tuple[str, ...]:
    return (
        "오늘 시장 변화가 어디서 시작됐는지 추적해줘",
        "내 포트폴리오에서 먼저 줄여야 할 리스크 봐줘",
        "최근 대화에서 위키로 남길 판단을 정리해줘",
    )


def _quick_prompts() -> str | None:
    prompts = _quick_prompt_texts()
    cols = st.columns(len(prompts))
    for idx, text in enumerate(prompts):
        if cols[idx].button(text, key=f"agent_quick_auto_{idx}", width="stretch"):
            return text
    return None


def _answer_agent_fast(question: str, surface: str) -> dict:
    try:
        return agent.answer(question, surface, async_postprocess=True)
    except TypeError as exc:
        if "async_postprocess" not in str(exc):
            raise
        return agent.answer(question, surface)


def _safe_status_update(status, **kwargs) -> None:
    update = getattr(status, "update", None)
    if callable(update):
        try:
            update(**kwargs)
        except Exception:
            pass


def _answer_with_progress(question: str, surface: str) -> dict:
    status_factory = getattr(st, "status", None)
    if callable(status_factory):
        status_context = status_factory(_AGENT_PROGRESS_LABELS[0], expanded=True)
        with status_context as status:
            updater = status or status_context
            for label in _AGENT_PROGRESS_LABELS[1:]:
                _safe_status_update(updater, label=label, state="running", expanded=True)
            result = _answer_agent_fast(question, surface)
            post = ((result.get("context") or {}).get("postprocess") or {}).get("wiki_autocurate")
            done_label = "답변 표시 완료"
            if post == "queued":
                done_label = "답변 표시 완료 · 위키 정리는 뒤에서 진행"
            _safe_status_update(updater, label=done_label, state="complete", expanded=False)
            return result
    with st.spinner("답변 생성 중"):
        return _answer_agent_fast(question, surface)


def _run_agent_question_auto(question: str, pack: dict | None = None):
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
    if pack is None:
        pack = _safe_context(surface, int(st.session_state.get("agent_hours", 72)))
    _run_agent_question(question, surface, pack, chat_key=_chat_key(_AUTO_CHAT))


def _run_agent_question(question: str, surface: str, pack: dict | None = None, chat_key: str | None = None):
    question = str(question or "").strip()
    if not question:
        return
    if pack is None:
        pack = _safe_context(surface, int(st.session_state.get("agent_hours", 72)))
    if chat_key is None:
        _ensure_chat_state(surface)
        chat_key = _chat_key(surface)
    else:
        _ensure_chat_state(_AUTO_CHAT)

    st.session_state[chat_key].append({"role": "user", "content": question})
    result = _answer_with_progress(question, surface)
    if result.get("ok"):
        ctx = result.get("context") or {}
        answer = result.get("answer", "")
        references = chat_references.build_answer_references(question, surface, pack, answer)
        trace = chat_feedback.build_trace(question, surface, pack, result, references)
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
            meta += f" · 엔진 {'규칙' if engine == 'local-rules' else engine}"
        post = (ctx.get("postprocess") or {}).get("wiki_autocurate")
        if post:
            meta += f" · 후처리 {post}"
        if ref_counts["wiki"] or ref_counts["sources"]:
            meta += f" · 참고 위키 {ref_counts['wiki']} · 출처 {ref_counts['sources']}"
        if ctx.get("context_error"):
            meta = f"{meta} · context fallback" if meta else "context fallback"
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": answer,
            "meta": meta,
            "question": question,
            "references": references,
            "trace": trace,
        })
    else:
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": result.get("error", "답변 생성 실패"),
            "meta": "error",
            "question": question,
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


def _render_trace_panel(trace: dict, key_prefix: str) -> None:
    steps = list((trace or {}).get("steps") or [])
    if not steps:
        return
    with st.expander("검토 경로", expanded=False):
        st.caption("내부 추론 전문이 아니라, 답변을 만들 때 확인한 공개 가능한 단계 요약입니다.")
        for step in steps:
            st.markdown(
                f"<div class='codex-trace-row'><span>{_esc(step.get('label'))}</span><b>{_esc(step.get('value'))}</b></div>",
                unsafe_allow_html=True,
            )


def _render_feedback_controls(msg: dict, surface: str, key_prefix: str) -> None:
    if msg.get("role") != "assistant" or not msg.get("question"):
        return
    state_key = f"{key_prefix}_feedback_saved"
    if st.session_state.get(state_key):
        st.caption(f"피드백 저장됨: {st.session_state[state_key]}")
        return
    cols = st.columns(3)
    options = (("helpful", "도움됨"), ("neutral", "애매함"), ("not_helpful", "별로"))
    for col, (rating, label) in zip(cols, options):
        if col.button(label, key=f"{key_prefix}_fb_{rating}", width="stretch"):
            chat_feedback.record_answer_feedback(
                question=msg.get("question", ""),
                answer=msg.get("content", ""),
                surface=surface,
                rating=rating,
                references=msg.get("references") or {},
                trace=msg.get("trace") or {},
            )
            st.session_state[state_key] = label
            st.toast("피드백을 저장했습니다.")
            st.rerun()


def _chat_context_rail(surface: str, pack: dict):
    st.markdown("##### Context")
    sources = pack.get("sources") or {}
    events = sources.get("events") or []
    memory = pack.get("memory") or []
    reports = pack.get("reports") or []
    status_items = _rail_status_items(surface, pack)
    st.markdown(
        '<div class="codex-rail-card">'
        + "".join(f"<div><span>{_esc(item['label'])}</span><b>{_esc(item['value'])}</b></div>" for item in status_items)
        + "</div>",
        unsafe_allow_html=True,
    )

    with st.expander("설정", expanded=False):
        st.selectbox(
            "맥락 고정",
            [_PIN_AUTO, *list(_SURFACES)],
            format_func=lambda k: k if k == _PIN_AUTO else _SURFACES.get(k, k),
            key="agent_surface_pin",
        )
        st.selectbox(
            "수집 범위",
            [24, 72, 168, 336],
            index=1,
            format_func=lambda h: f"{h}h" if h < 168 else f"{h // 24}d",
            key="agent_hours",
        )
        if st.button("메모리 적재", width="stretch"):
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
    for item in events[:7]:
        title = item.get("title") or item.get("summary") or "제목 없음"
        st.markdown(
            f"<div class='codex-feed-item'><b>{_esc(item.get('source', 'source'))}</b><span>{_esc(title)}</span></div>",
            unsafe_allow_html=True,
        )
    if not events:
        st.caption("최근 이벤트 없음")

    st.markdown("##### Memory")
    for item in memory[:5]:
        st.markdown(
            f"<div class='codex-feed-item'><b>{_esc(item.get('kind', 'memory'))}</b><span>{_esc(item.get('title', '제목 없음'))}</span></div>",
            unsafe_allow_html=True,
        )
    if not memory:
        st.caption("World Memory 비어 있음")
    if reports:
        st.caption(f"latest report: {reports[0].get('name')}")
    if pack.get("context_error"):
        st.warning(f"컨텍스트 일부를 불러오지 못했습니다: {pack['context_error']}")


def _memory_tab(surface: str):
    st.markdown("##### World Memory")
    st.caption("뉴스·텔레그램·리포트·대화가 쌓이는 단일 기억층입니다.")
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
        st.info("검색 결과가 없습니다." if search.strip() else "아직 저장된 시장 기억이 없습니다.")
        return
    df = pd.DataFrame([{
        "시각": r.get("observed_at"),
        "출처": r.get("source"),
        "종류": r.get("kind"),
        "제목": r.get("title"),
        "심볼": ", ".join(r.get("symbols") or []),
        "중요도": r.get("impact"),
    } for r in rows])
    event = st.dataframe(df, hide_index=True, width="stretch", height=360, on_select="rerun", selection_mode="single-row", key="_wm_tbl")
    _memory_detail(event, rows)


def _memory_detail(event, rows):
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
    if url.startswith("http://") or url.startswith("https://"):
        link_html = f'<div style="margin-top:10px"><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">원문 보기</a></div>'
    st.markdown(
        f"<div class='codex-detail'><b>{_esc(r.get('title') or '제목 없음')}</b>"
        f"<span>{_esc(r.get('observed_at') or '')} · {_esc(r.get('source') or '')} · {_esc(symbols)} · {_esc(r.get('impact') or '')}</span>"
        f"<div>{body_html}</div>{link_html}</div>",
        unsafe_allow_html=True,
    )


def _wiki_tab(surface: str, pack: dict):
    _wiki_pipeline_health_panel()
    st.divider()
    return wiki_browser.render_wiki_tab(surface, pack)


def _wiki_pipeline_health_panel():
    health = cached.wiki_pipeline_health()
    source_section = health.get("source_health") or {}
    wiki_section = health.get("wiki_health") or {}
    curation_section = health.get("curation_health") or {}
    overall = source_section.get("overall") or {}

    st.markdown("##### 파이프라인 헬스")
    if health.get("error"):
        st.warning(f"헬스 리포트 로드 실패: {health['error']}")
        return

    cols = st.columns(5)
    cols[0].metric("소스", f"{overall.get('healthy_sources', 0)}/{overall.get('expected_sources', 0)}")
    cols[1].metric("소스 공백", f"{overall.get('stale_sources', 0)}")
    cols[2].metric("source-backed", f"{wiki_section.get('source_backed_count', 0)}")
    cols[3].metric("미검증", f"{wiki_section.get('unverified_count', 0)}")
    cols[4].metric("미연결 digest", f"{curation_section.get('source_digest_unlinked_count', 0)}")

    st.caption(
        f"stale pages {wiki_section.get('stale_count', 0)} · "
        f"unused {wiki_section.get('unused_count', 0)} · "
        f"open Q {wiki_section.get('open_question_count', 0)}"
    )
    recs = health.get("recommendations") or []
    if recs:
        st.markdown("**권장 액션**")
        for rec in recs[:3]:
            st.markdown(
                f"- [{_esc(rec.get('category', 'unknown'))}] {_esc(rec.get('title', '—'))} — {_esc(rec.get('detail', '—'))}"
            )
    else:
        st.caption("즉시 조치가 필요한 항목이 없습니다.")


def _lab_tab(surface: str, pack: dict):
    _consume_canvas_pending()
    strategy_studio.render_strategy_lab("ai_console", pack, mode="lab")
    st.divider()
    st.markdown("##### 기존 전략 캔버스")
    st.caption("비중과 규칙을 저장해 챗봇/위키가 다시 읽는 전략 가설로 남깁니다.")
    default_text = _default_canvas_allocations()
    alloc_text = st.text_area("포트폴리오 비중", value=st.session_state.get("strategy_canvas_alloc_text", default_text), height=150, key="strategy_canvas_alloc_text")
    allocs = _normalize_allocations(_parse_allocations(alloc_text))
    if allocs:
        st.dataframe(pd.DataFrame(allocs), hide_index=True, width="stretch", height=220)
    else:
        st.info("한 줄에 `티커 비중 메모` 형식으로 입력해 주세요. 예: QQQ 45 핵심 성장")

    c1, c2, c3 = st.columns(3)
    buy_rsi = c1.number_input(
        "매수 RSI", min_value=1, max_value=99,
        value=int(st.session_state.get("strategy_canvas_buy_rsi", 30)),
        step=1, key="strategy_canvas_buy_rsi",
    )
    sell_rsi = c2.number_input(
        "현금화 RSI", min_value=1, max_value=99,
        value=int(st.session_state.get("strategy_canvas_sell_rsi", 70)),
        step=1, key="strategy_canvas_sell_rsi",
    )
    max_loss = c3.number_input(
        "최대 손실한도 %", min_value=0.0, max_value=100.0,
        value=float(st.session_state.get("strategy_canvas_max_loss", 8.0)),
        step=0.5, key="strategy_canvas_max_loss",
    )
    hypothesis = st.text_area(
        "전략 가설", height=90,
        value=st.session_state.get("strategy_canvas_hypothesis", ""),
        placeholder="어떤 시장에서 이 전략이 유리하고, 어떤 조건에서 꺼야 하는지 적어주세요.",
        key="strategy_canvas_hypothesis",
    )
    if st.button("시나리오 저장", type="primary", width="stretch", disabled=not bool(allocs)):
        scenario = storage.save_scenario({
            "name": "AI 콘솔 전략 캔버스",
            "description": hypothesis,
            "allocations": allocs,
            "rules": {
                "max_loss_pct": max_loss,
                "text": f"RSI <= {int(buy_rsi)} 매수, RSI >= {int(sell_rsi)} 현금화",
                "live_orders": False,
                "actual_asset_link": False,
            },
            "assumptions": {"surface": surface, "raw_allocations": alloc_text},
            "metrics": {"saved_from": "ai_console_strategy_canvas", "allocation_count": len(allocs)},
        })
        st.toast(f"{scenario.get('name', '시나리오')} 저장 완료")

    scenarios = storage.list_scenarios(limit=8)
    if scenarios:
        st.markdown("##### 저장된 시나리오")
        for scenario in scenarios:
            st.markdown(
                f"<div class='scenario-row'><b>{_esc(scenario.get('name', '시나리오'))}</b><span>{_esc(scenario.get('updated_at', ''))}</span></div>",
                unsafe_allow_html=True,
            )


def _consume_canvas_pending() -> None:
    for field in ("buy_rsi", "sell_rsi", "max_loss", "hypothesis", "alloc_text"):
        key = f"strategy_canvas_{field}"
        pending_key = f"{key}_pending"
        if pending_key in st.session_state:
            st.session_state[key] = st.session_state.pop(pending_key)


def _heuristic_canvas_patch(question: str, current: dict, answer_text: str) -> dict:
    q = str(question or "")
    patch: dict = {}

    pair = _RSI_PAIR_RE.search(q)
    if pair:
        buy, sell = int(pair.group(1)), int(pair.group(2))
        if 1 <= buy <= 99:
            patch["buy_rsi"] = buy
        if 1 <= sell <= 99:
            patch["sell_rsi"] = sell
    else:
        buy_match = _RSI_BUY_RE.search(q)
        if buy_match:
            buy = int(buy_match.group(1))
            if 1 <= buy <= 99:
                patch["buy_rsi"] = buy
        sell_match = _RSI_SELL_RE.search(q)
        if sell_match:
            sell = int(sell_match.group(1))
            if 1 <= sell <= 99:
                patch["sell_rsi"] = sell

    loss_match = _MAX_LOSS_RE.search(q)
    if loss_match:
        loss = float(loss_match.group(1))
        if 0 <= loss <= 100:
            patch["max_loss"] = loss

    if "가설" in q and any(word in q for word in ("바꿔", "바꾸", "적어", "써줘", "써 줘", "수정")):
        summary = str(answer_text or "").strip().split("\n")[0][:80]
        if summary:
            patch["hypothesis"] = summary

    return patch


def _default_canvas_allocations() -> str:
    try:
        holdings = data.load_holdings()
    except Exception:
        holdings = []
    rows = []
    for row in sorted(holdings or [], key=lambda r: float(r.get("weight") or 0), reverse=True)[:7]:
        ticker = str(row.get("ticker") or "").upper().strip()
        weight = data._try_float(row.get("weight"))
        if not ticker or weight is None:
            continue
        note = str(row.get("name") or "").strip()
        rows.append(f"{ticker} {weight:.1f} {note}".rstrip())
    if rows:
        return "\n".join(rows)
    return "QQQ 45 핵심 성장\nTLT 20 금리 방어\nGLD 10 꼬리위험\nCASH 25 기회 대기"


def _parse_allocations(text: str) -> list[dict]:
    rows = []
    for line in str(text or "").splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        weight = data._try_float(parts[1])
        if weight is None:
            continue
        rows.append({"symbol": parts[0].upper(), "weight_pct": weight, "note": " ".join(parts[2:])})
    return rows


def _normalize_allocations(allocations: list[dict]) -> list[dict]:
    rows = []
    for row in allocations or []:
        symbol = str(row.get("symbol") or "").upper().strip()
        weight = data._try_float(row.get("weight_pct"))
        if not symbol or weight is None or weight < 0:
            continue
        rows.append({"symbol": symbol, "weight_pct": float(weight), "note": row.get("note", "")})
    total = sum(r["weight_pct"] for r in rows)
    if total <= 0:
        return []
    return [{**r, "weight_pct": round(r["weight_pct"] / total * 100.0, 2)} for r in rows]


def _canvas_prices(symbols: list[str], period: str) -> pd.DataFrame:
    frames = {}
    for symbol in symbols:
        ticker = str(symbol or "").upper().strip()
        if not ticker or ticker == "CASH":
            continue
        try:
            frame = cached.ohlc(ticker, period=period)
        except Exception:
            frame = pd.DataFrame()
        if frame is None or frame.empty:
            continue
        close = frame["Close"] if "Close" in frame.columns else frame.iloc[:, -1]
        frames[ticker] = pd.to_numeric(close, errors="coerce")
    return pd.DataFrame(frames).dropna(how="all")


def _strategy_canvas_backtest(
    allocations: list[dict],
    *,
    period: str,
    signal_symbol: str,
    buy_rsi: int,
    sell_rsi: int,
) -> dict:
    rows = _normalize_allocations(allocations)
    weights = {row["symbol"]: float(row["weight_pct"]) / 100.0 for row in rows}
    signal = str(signal_symbol or (rows[0]["symbol"] if rows else "")).upper().strip()
    symbols = list(dict.fromkeys([*weights, signal]))
    close = _canvas_prices(symbols, period)
    program = portfolio_matrix_dsl.rsi_cash_program(buy_rsi, sell_rsi)
    run = portfolio_matrix_dsl.run_portfolio_matrix_dsl(
        close,
        weights,
        signal_symbol=signal,
        program=program,
        label="RSI 현금화",
    )
    return {
        "ok": run.ok,
        "functionSpec": {"language": portfolio_matrix_dsl.MATRIX_DSL_LANGUAGE, "program": program},
        "equity": run.equity,
        "metrics": run.metrics,
        "trades": run.trades,
        "matrix": run.matrix,
        "note": run.note,
        "error": run.error,
    }


def _connectors_tab():
    st.markdown("##### 로컬 커넥터")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Arca — 서버 SOCKS 터널**")
        proxy = os.getenv("STOCK_COLLECTOR_ARCA_PROXY", "socks5://127.0.0.1:1080")
        try:
            from reports import source_collector
            status = source_collector.arca_proxy_status(proxy)
        except Exception as exc:
            status = {"reachable": False, "proxy": proxy, "error": str(exc)}
        st.metric("터널", "UP" if status.get("reachable") else "DOWN", help=status.get("proxy") or proxy)
        if status.get("error"):
            st.caption(f"상태: {status['error']}")
        pages = st.number_input("조회 페이지", min_value=1, max_value=5, value=2, step=1, key="arca_proxy_pages")
        if st.button("Arca 프록시 수집", type="primary", width="stretch"):
            with st.spinner("Arca를 SOCKS 터널로 조회 중..."):
                result = context.ingest_arca_proxy(max_pages=int(pages), proxy=proxy)
            _context_pack.clear()
            if result.get("ok"):
                st.success(f"수집 {result.get('fetched', 0)}건 · 캐시 {result.get('written', 0)}건 · 메모리 {result.get('changed', 0)}건")
            else:
                st.warning(f"Arca 수집 실패: {result.get('error') or 'unknown'}")
    with c2:
        st.markdown("**Toss — 로컬 스냅샷 예정**")
        st.markdown("- 읽기 전용 자산 스냅샷\n- 주문/매매 연결 없음\n- 보유·현금·거래내역 요약만 Context Layer에 반영")
    st.code(
        "Arca: 서버 SOCKS 127.0.0.1:1080 -> source-cache + World Memory\n"
        "Toss: 노트북 local snapshot -> ~/reports/local-connectors/*.json",
        language="text",
    )


def _inject_codex_css():
    st.markdown(
        """
        <style>
        .codex-console-title{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:0 0 12px;border-bottom:1px solid rgba(148,163,184,.14);margin-bottom:12px}
        .codex-console-title h1{margin:0;font-size:1.55rem;line-height:1.15;letter-spacing:0}
        .codex-console-title span,.codex-kicker{color:rgba(148,163,184,.92);font-size:.82rem}.codex-kicker{text-transform:uppercase;letter-spacing:.08em;margin-bottom:2px}
        .codex-glance{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:0 0 8px}.codex-glance div{border:1px solid rgba(148,163,184,.14);border-radius:8px;background:rgba(15,23,42,.24);padding:8px 10px;min-height:52px}.codex-glance span{display:block;color:rgba(148,163,184,.78);font-size:.72rem;margin-bottom:3px}.codex-glance b{display:block;color:rgba(226,232,240,.96);font-size:.9rem;font-weight:680;line-height:1.25;overflow-wrap:anywhere}
        .codex-chat-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 10px;border:1px solid rgba(148,163,184,.14);border-radius:8px;background:rgba(15,23,42,.26);margin-bottom:10px}.codex-chat-head span{color:rgba(148,163,184,.86);font-size:.78rem;overflow-wrap:anywhere}.codex-chip{display:inline-block;margin-left:8px;padding:2px 9px;border:1px solid rgba(56,189,248,.35);border-radius:999px;background:rgba(56,189,248,.12);color:rgba(125,211,252,.96)!important;font-size:.72rem!important;font-weight:600;vertical-align:middle;white-space:nowrap}
        div[data-testid="stChatMessage"]{border:1px solid rgba(148,163,184,.13);border-radius:8px;background:rgba(15,23,42,.20);padding:9px 11px;margin-bottom:8px}div[data-testid="stChatInput"] textarea{border-radius:8px}
        .codex-rail-card{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:2px 0 10px}.codex-rail-card div{border:1px solid rgba(148,163,184,.13);border-radius:8px;background:rgba(15,23,42,.22);padding:8px 9px;min-height:52px}.codex-rail-card span{display:block;color:rgba(148,163,184,.82);font-size:.74rem;margin-bottom:3px}.codex-rail-card b{display:block;font-size:.94rem;overflow-wrap:anywhere}
        .codex-feed-item,.scenario-row,.codex-detail{border:1px solid rgba(148,163,184,.16);border-radius:8px;background:rgba(2,6,23,.22);padding:8px 9px;margin-bottom:7px}.codex-feed-item b,.scenario-row b,.codex-detail b{display:block;color:rgba(226,232,240,.96);font-size:.82rem;margin-bottom:2px}.codex-feed-item span,.scenario-row span,.codex-detail span{display:block;color:rgba(148,163,184,.86);font-size:.76rem;line-height:1.35}.codex-detail div{margin-top:8px;white-space:pre-wrap;font-size:.9rem;line-height:1.55}
        .codex-trace-row{display:flex;justify-content:space-between;gap:12px;border:1px solid rgba(148,163,184,.13);border-radius:7px;background:rgba(15,23,42,.22);padding:7px 9px;margin-bottom:6px}.codex-trace-row span{color:rgba(148,163,184,.82);font-size:.76rem}.codex-trace-row b{color:rgba(226,232,240,.94);font-size:.82rem;text-align:right;overflow-wrap:anywhere}
        @media(max-width:980px){.codex-glance{grid-template-columns:repeat(2,minmax(0,1fr))}}
        </style>
        """,
        unsafe_allow_html=True,
    )
