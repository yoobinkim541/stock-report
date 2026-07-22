"""AI 콘솔 — World Memory + 대화형 컨텍스트 + 포트폴리오 전략랩.

기존 Cloudflare/Streamlit 대시보드 안에서 agent_console 코어를 직접 호출한다.
별도 Flask 포트 없이 같은 인증·사이드바·배포 경로를 사용한다.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_console import agent, context, storage, wiki
from agent_console.portfolio_matrix_dsl import rsi_cash_program, run_portfolio_matrix_dsl
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


def _context_glance(pack: dict):
    sources = pack.get("sources") or {}
    reports = pack.get("reports") or []
    models = (pack.get("models") or {}).get("items") or []
    memory = pack.get("memory") or []

    m = st.columns(4)
    m[0].metric("최근 이벤트", f"{len(sources.get('events') or [])}건")
    m[1].metric("누적 기억", f"{len(memory)}건")
    m[2].metric("모델 파일", f"{len(models)}개")
    m[3].metric("최신 리포트", reports[0].get("name", "—") if reports else "—")

    focus = pack.get("focus") or []
    if focus:
        st.caption(" · ".join(focus))

    source_counts = sources.get("source_counts") or []
    symbol_counts = sources.get("symbol_counts") or []
    if source_counts or symbol_counts:
        left, right = st.columns(2)
        left.caption("소스: " + (" · ".join(f"{name} {cnt}" for name, cnt in source_counts[:6]) or "—"))
        right.caption("심볼/태그: " + (" · ".join(f"{name} {cnt}" for name, cnt in symbol_counts[:8]) or "—"))


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
            _run_agent_question_auto(pending)

        for msg in st.session_state[chat_key][-16:]:
            role_raw = str(msg.get("role", "assistant")).strip().lower()
            role = "user" if role_raw in {"user", "human"} else "assistant"
            with st.chat_message(role):
                st.markdown(msg.get("content", ""))
                if msg.get("meta"):
                    st.caption(msg["meta"])

        user_text = st.chat_input("무엇이든 질문하기 — 포트폴리오·종목·시장·모의투자·전략",
                                  key="agent_chat_input_auto")
        if user_text:
            _run_agent_question_auto(user_text)
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


def _quick_prompts() -> str | None:
    """도메인을 가로지르는 추천 질문 4개 — 눌러도 되고, 그냥 아래에 입력해도 된다."""
    prompts = [
        "오늘 시장 변화가 어디서 시작됐는지 추적해줘",
        "내 포트폴리오에서 먼저 줄여야 할 리스크 봐줘",
        "모의투자 성과가 좋아진 이유와 나빠진 이유를 나눠줘",
        "보유종목에 영향을 줄 이벤트만 골라줘",
    ]
    cols = st.columns(4)
    for idx, text in enumerate(prompts):
        if cols[idx].button(text, key=f"agent_quick_auto_{idx}", width="stretch"):
            return text
    return None


def _run_agent_question_auto(question: str):
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
    _run_agent_question(question, surface, chat_key=_chat_key(_AUTO_CHAT))


def _run_agent_question(question: str, surface: str, chat_key: str | None = None):
    question = str(question or "").strip()
    if not question:
        return
    if chat_key is None:
        _ensure_chat_state(surface)
        chat_key = _chat_key(surface)
    else:
        _ensure_chat_state(_AUTO_CHAT)
    st.session_state[chat_key].append({"role": "user", "content": question})
    with st.spinner("컨텍스트 읽는 중..."):
        result = agent.answer(question, surface)
    if result.get("ok"):
        ctx = result.get("context") or {}
        meta = f"맥락 {_SURFACES.get(surface, surface)}"
        if ctx:
            meta += f" · events {ctx.get('event_count', 0)} · memory {ctx.get('memory_count', 0)}"
        engine = str(ctx.get("engine") or "")
        if engine:
            # 어떤 엔진이 답했는지 정직 표기 — local-rules = LLM 미개입 규칙 답변
            meta += f" · 엔진 {'⚙️ 규칙' if engine == 'local-rules' else '🤖 ' + engine}"
        if ctx.get("context_error"):
            meta = f"{meta} · context fallback" if meta else "context fallback"
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": result.get("answer", ""),
            "meta": meta,
        })
    else:
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": result.get("error", "답변 생성 실패"),
            "meta": "error",
        })


def _chat_context_rail(surface: str, pack: dict):
    st.markdown("##### Context")
    sources = pack.get("sources") or {}
    events = sources.get("events") or []
    memory = pack.get("memory") or []
    models = (pack.get("models") or {}).get("items") or []
    reports = pack.get("reports") or []

    st.markdown(
        f"""
        <div class="codex-rail-card">
          <div><span>맥락 (자동)</span><b>{_SURFACES.get(surface, surface)}</b></div>
          <div><span>events</span><b>{len(events)}</b></div>
          <div><span>memory</span><b>{len(memory)}</b></div>
          <div><span>models</span><b>{len(models)}</b></div>
        </div>
        """,
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

    rows = context.world_memory_rows(limit=120)
    if not rows:
        st.info("아직 저장된 시장 기억이 없습니다. 상단의 메모리 적재를 먼저 실행해 보세요.")
        return
    df = pd.DataFrame([{
        "시각": r.get("observed_at"),
        "출처": r.get("source"),
        "종류": r.get("kind"),
        "제목": r.get("title"),
        "심볼": ", ".join(r.get("symbols") or []),
        "중요도": r.get("impact"),
    } for r in rows])
    st.dataframe(df, hide_index=True, width="stretch", height=360)


def _wiki_tab(surface: str, pack: dict):
    st.markdown("##### 위키")
    st.caption("대화와 메모를 카드로 승격해 챗봇이 다시 읽는 지식층입니다.")

    stats = wiki.stats()
    cols = st.columns(4)
    cols[0].metric("페이지", f"{stats.get('total', 0)}")
    cols[1].metric("초안", f"{stats.get('status_counts', {}).get('draft', 0)}")
    cols[2].metric("검토", f"{stats.get('status_counts', {}).get('reviewed', 0)}")
    latest = stats.get("latest") or {}
    cols[3].metric("최근", latest.get("title", "—")[:20] if latest else "—")

    qcol, scol = st.columns([1.1, 0.9], gap="large")
    with qcol:
        query = st.text_input("위키 검색", key="agent_wiki_query", placeholder="손실한도, 레버리지, AI ETF, 시장 신호...")
        pages = wiki.list_pages(query=query, surface=surface, limit=12)
        if not pages:
            st.info("아직 위키 카드가 없습니다. 아래에서 현재 대화를 위키로 승격해 보세요.")
        else:
            for page in pages:
                with st.container(border=True):
                    st.markdown(
                        f"**{page.get('title', '위키')}**  \n"
                        f"{page.get('surface', 'wiki')} · {page.get('kind', 'note')} · {page.get('status', 'draft')}"
                    )
                    if page.get("summary"):
                        st.caption(page["summary"][:240])
                    if page.get("tags"):
                        st.caption(" · ".join(page["tags"][:6]))
                    btn1, btn2 = st.columns(2)
                    if btn1.button("불러오기", key=f"wiki_load_{page.get('id')}", width="stretch"):
                        st.session_state["agent_wiki_selected_page_id"] = page.get("id")
                        st.toast("위키 페이지를 불러왔습니다.")
                    if btn2.button("삭제", key=f"wiki_drop_{page.get('id')}", width="stretch"):
                        if wiki.delete_page(page.get("id")):
                            _context_pack.clear()
                            st.toast("위키 페이지 삭제 완료")
                            st.rerun()

    with scol:
        selected = wiki.get_page(st.session_state.get("agent_wiki_selected_page_id", "")) or {}
        st.markdown("##### 편집")
        with st.form("agent_wiki_editor", clear_on_submit=False):
            title = st.text_input("제목", value=selected.get("title") or st.session_state.get("agent_wiki_title", ""))
            kind = st.selectbox("종류", ["playbook", "decision", "risk", "concept", "note"],
                                index=["playbook", "decision", "risk", "concept", "note"].index(selected.get("kind", "playbook"))
                                if selected.get("kind", "playbook") in ["playbook", "decision", "risk", "concept", "note"] else 0)
            status = st.selectbox("상태", ["draft", "reviewed", "stable", "archived"],
                                  index=["draft", "reviewed", "stable", "archived"].index(selected.get("status", "draft"))
                                  if selected.get("status", "draft") in ["draft", "reviewed", "stable", "archived"] else 0)
            tags = st.text_input("태그", value=", ".join(selected.get("tags", [])))
            summary = st.text_area("요약", value=selected.get("summary", ""), height=120)
            body = st.text_area("본문", value=selected.get("body", ""), height=180)
            source_refs = st.text_input("source refs", value=", ".join(selected.get("source_refs", [])))
            if st.form_submit_button("저장", type="primary", width="stretch"):
                saved = wiki.upsert_page(
                    {
                        "id": selected.get("id"),
                        "title": title,
                        "surface": surface,
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
                _context_pack.clear()
                st.success("위키 페이지를 저장했습니다.")
                st.rerun()

        st.markdown("##### 현재 대화 승격")
        chat_rows = st.session_state.get(_chat_key(_AUTO_CHAT), [])
        exchange = _last_chat_exchange(chat_rows)
        if exchange:
            st.markdown(f"**Q.** {exchange['question']}")
            st.markdown(f"**A.** {exchange['answer']}")
            capture_col, reset_col = st.columns(2)
            if capture_col.button("이 대화를 위키로", type="primary", width="stretch"):
                saved = wiki.capture_from_chat(
                    exchange["question"],
                    exchange["answer"],
                    surface=surface,
                    title=exchange["question"],
                    status="draft",
                    kind="playbook",
                    tags=["conversation", surface],
                    source_refs=[f"conversation:{exchange['id']}"],
                )
                st.session_state["agent_wiki_selected_page_id"] = saved.get("id")
                _context_pack.clear()
                st.toast("대화를 위키로 저장했습니다.")
                st.rerun()
            if reset_col.button("선택 해제", width="stretch"):
                st.session_state.pop("agent_wiki_selected_page_id", None)
                st.rerun()
        else:
            st.caption("현재 대화 기록이 없어 승격할 항목이 없습니다.")

        with st.expander("위키가 챗봇에 들어가는 방식", expanded=False):
            section = wiki.build_context_section(query=query or selected.get("title", ""), surface=surface, limit=4)
            if section:
                st.code(section, language="text")
            else:
                st.caption("아직 노출할 위키 지식이 없습니다.")


def _last_chat_exchange(rows: list[dict]) -> dict | None:
    pending: dict | None = None
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


def _lab_tab(surface: str):
    st.markdown("##### 전략 캔버스")
    st.markdown(
        """
        <div class="widget-flow">
          <div><b>W-001</b><span>포트폴리오 입력</span></div>
          <i></i>
          <div><b>W-009</b><span>RSI 현금화 규칙</span></div>
          <i></i>
          <div><b>W-010</b><span>Buy & Hold 비교</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _ensure_canvas_defaults()
    alloc_text = st.session_state.get("strategy_canvas_alloc_text", "")
    allocs = _normalize_allocations(_parse_allocations(alloc_text))
    market_symbols = [a["symbol"] for a in allocs if a.get("symbol") and a.get("symbol") != "CASH"]
    signal_options = market_symbols or ["QQQ"]

    setup_cols = st.columns([1, 1, 1, 1], gap="small")
    period = setup_cols[0].selectbox("기간", ["3mo", "6mo", "1y", "2y"], index=2,
                                     format_func=lambda x: {"3mo": "3개월", "6mo": "6개월",
                                                            "1y": "1년", "2y": "2년"}[x],
                                     key="strategy_canvas_period")
    signal_symbol = setup_cols[1].selectbox("신호 기준", signal_options,
                                            index=0, key="strategy_canvas_signal")
    buy_rsi = setup_cols[2].number_input("매수 RSI", min_value=1, max_value=99, value=30,
                                         step=1, key="strategy_canvas_buy_rsi")
    sell_rsi = setup_cols[3].number_input("현금화 RSI", min_value=1, max_value=99, value=70,
                                          step=1, key="strategy_canvas_sell_rsi")

    top_left, top_right = st.columns([0.96, 1.04], gap="large")
    with top_left:
        st.markdown(
            """
            <div class="widget-card-head">
              <span>W-001 · 입력</span>
              <b>포트폴리오 구성</b>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.text_area(
            "비중",
            key="strategy_canvas_alloc_text",
            help="한 줄에 `티커 비중 메모` 형식",
            height=152,
        )
        if allocs:
            st.dataframe(
                pd.DataFrame([{
                    "자산": a["symbol"],
                    "비중": f"{a['weight_pct']:.1f}%",
                    "메모": a.get("note", ""),
                } for a in allocs]),
                hide_index=True,
                width="stretch",
                height=190,
            )
        else:
            st.warning("비중을 `QQQ 50 핵심` 형식으로 입력해 주세요.")

    with top_right:
        st.markdown(
            """
            <div class="widget-card-head">
              <span>W-009 · 함수 위젯</span>
              <b>RSI 매수·현금화 규칙</b>
            </div>
            """,
            unsafe_allow_html=True,
        )
        rule_cols = st.columns(3)
        rule_cols[0].metric("매수", f"RSI ≤ {buy_rsi}")
        rule_cols[1].metric("현금화", f"RSI ≥ {sell_rsi}")
        rule_cols[2].metric("체결", "다음 날")
        st.markdown(
            f"""
            <div class="rule-matrix">
              <div><span>source</span><b>{signal_symbol}</b></div>
              <div><span>lookback</span><b>14일</b></div>
              <div><span>cost</span><b>0 bps</b></div>
              <div><span>mode</span><b>정보형 백테스트</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("FinanceAgentGUI의 portfolio-matrix-dsl 방식처럼 신호를 target_weight 행렬로 해석하고 다음 거래일부터 노출을 바꿉니다.")

    result_col, side_col = st.columns([1.48, 0.72], gap="large")
    with result_col:
        st.markdown(
            """
            <div class="widget-card-head">
              <span>W-010 · 백테스트 비교</span>
              <b>Buy & Hold vs RSI 현금화 전략</b>
            </div>
            """,
            unsafe_allow_html=True,
        )
        run = st.button("캔버스 실행", type="primary", width="stretch",
                        disabled=not bool(allocs), key="strategy_canvas_run")
        if run:
            with st.spinner("시세를 불러와 캔버스를 실행 중..."):
                st.session_state["strategy_canvas_result"] = _strategy_canvas_backtest(
                    allocs, period=period, signal_symbol=signal_symbol,
                    buy_rsi=int(buy_rsi), sell_rsi=int(sell_rsi),
                )

        result = st.session_state.get("strategy_canvas_result")
        if result and result.get("ok"):
            st.plotly_chart(_strategy_canvas_chart(result["equity"]), width="stretch",
                            config={"displayModeBar": False})
            st.dataframe(result["metrics"], hide_index=True, width="stretch")
            st.caption(result.get("note", ""))
            trades = result.get("trades") or []
            if trades:
                with st.expander(f"DSL 체결 로그 {len(trades)}건", expanded=False):
                    st.dataframe(pd.DataFrame(trades), hide_index=True, width="stretch", height=180)
        elif result and not result.get("ok"):
            st.warning(result.get("error", "캔버스 실행 실패"))
        else:
            st.info("캔버스 실행을 누르면 현재 비중과 RSI 규칙으로 1일봉 비교 차트를 만듭니다.")

    with side_col:
        _canvas_saved_scenarios(surface, alloc_text, allocs, buy_rsi, sell_rsi, signal_symbol)


def _ensure_canvas_defaults():
    if "strategy_canvas_alloc_text" in st.session_state:
        return
    st.session_state["strategy_canvas_alloc_text"] = _default_canvas_allocations()


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
    return [{**r, "weight_pct": r["weight_pct"] / total * 100.0} for r in rows]


def _canvas_saved_scenarios(surface: str, alloc_text: str, allocs: list[dict],
                            buy_rsi: int, sell_rsi: int, signal_symbol: str):
    st.markdown("##### Scenario")
    with st.form("agent_scenario_form"):
        name = st.text_input("시나리오 이름", value="RSI 현금화 캔버스")
        max_loss = st.number_input("최대 손실한도 %", min_value=0.0, max_value=100.0,
                                   value=8.0, step=0.5)
        desc = st.text_area("전략 가설", height=86,
                            placeholder="어떤 시장 맥락에서 이 규칙이 유리한지 적어주세요.")
        if st.form_submit_button("시나리오 저장", type="primary", width="stretch"):
            total = sum(float(x.get("weight_pct") or 0) for x in allocs)
            scenario = storage.save_scenario({
                "name": name,
                "description": desc,
                "allocations": allocs,
                "rules": {
                    "max_loss_pct": max_loss,
                    "text": f"{signal_symbol} RSI <= {buy_rsi} 매수, RSI >= {sell_rsi} 현금화",
                    "functionSpec": {
                        "language": "portfolio-matrix-dsl",
                        "executionMode": "matrix-dsl",
                        "program": rsi_cash_program(int(buy_rsi), int(sell_rsi)),
                    },
                    "live_orders": False,
                    "actual_asset_link": False,
                },
                "assumptions": {
                    "surface": surface,
                    "total_weight_pct": round(total, 2),
                    "raw_allocations": alloc_text,
                },
                "metrics": {"saved_from": "strategy_canvas", "allocation_count": len(allocs)},
            })
            st.toast(f"{scenario['name']} 저장 완료 · 합계 {total:.1f}%")

    scenarios = storage.list_scenarios(limit=12)
    if not scenarios:
        st.caption("저장된 시나리오 없음")
        return
    for scenario in scenarios[:5]:
        st.markdown(
            f"<div class='scenario-row'><b>{scenario.get('name', '시나리오')}</b>"
            f"<span>{scenario.get('updated_at', '')}</span></div>",
            unsafe_allow_html=True,
        )


def _strategy_canvas_backtest(allocations: list[dict], *, period: str, signal_symbol: str,
                              buy_rsi: int, sell_rsi: int) -> dict:
    if buy_rsi >= sell_rsi:
        return {"ok": False, "error": "매수 RSI는 현금화 RSI보다 낮아야 합니다."}

    allocs = _normalize_allocations(allocations)
    weights = {row["symbol"]: row["weight_pct"] / 100.0 for row in allocs}
    market_symbols = [symbol for symbol in weights if symbol != "CASH"]
    if not market_symbols:
        return {"ok": False, "error": "백테스트할 시장 자산이 없습니다."}

    fetch_symbols = tuple(sorted(set(market_symbols + [signal_symbol])))
    close = _canvas_prices(fetch_symbols, period)
    if close.empty:
        return {"ok": False, "error": "시세를 불러오지 못했습니다."}
    missing = [symbol for symbol in market_symbols if symbol not in close.columns]
    available_symbols = [symbol for symbol in market_symbols if symbol in close.columns]
    if signal_symbol not in close.columns:
        return {"ok": False, "error": f"{signal_symbol} 신호 기준 시세가 없습니다."}
    if not available_symbols:
        return {"ok": False, "error": "사용 가능한 포트폴리오 자산 시세가 없습니다."}

    run = run_portfolio_matrix_dsl(
        close,
        weights,
        signal_symbol=signal_symbol,
        program=rsi_cash_program(int(buy_rsi), int(sell_rsi)),
        label="RSI 현금화",
    )
    if not run.ok:
        return {"ok": False, "error": run.error or "DSL 백테스트 실패"}

    note = f"{period} · {run.note}"
    if missing:
        note += " · 제외: " + ", ".join(missing)
    return {
        "ok": True,
        "equity": run.equity,
        "metrics": run.metrics,
        "note": note,
        "trades": run.trades,
        "matrix": run.matrix[:2000],
        "functionSpec": {
            "language": "portfolio-matrix-dsl",
            "executionMode": "matrix-dsl",
            "program": rsi_cash_program(int(buy_rsi), int(sell_rsi)),
        },
    }


@st.cache_data(ttl=3600, show_spinner=False)
def _canvas_prices(symbols: tuple[str, ...], period: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame()
    try:
        raw = yf.download(list(symbols), period=period, interval="1d", auto_adjust=True,
                          progress=False, threads=False)
    except Exception:
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        first = raw.columns.get_level_values(0)
        if "Close" in first:
            close = raw["Close"].copy()
        elif "Adj Close" in first:
            close = raw["Adj Close"].copy()
        else:
            return pd.DataFrame()
    else:
        col = "Close" if "Close" in raw.columns else "Adj Close" if "Adj Close" in raw.columns else None
        if not col:
            return pd.DataFrame()
        close = raw[[col]].rename(columns={col: symbols[0]}).copy()
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    close.columns = [str(col).upper() for col in close.columns]
    close.index = pd.to_datetime(close.index)
    return close.sort_index().dropna(how="all")


def _strategy_canvas_chart(equity: pd.DataFrame):
    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity["date"], y=equity["Buy & Hold"], mode="lines",
        name="Buy & Hold", line={"color": "#059669", "width": 2.4},
    ))
    fig.add_trace(go.Scatter(
        x=equity["date"], y=equity["RSI 현금화"], mode="lines",
        name="RSI 현금화", line={"color": "#7c3aed", "width": 2.2},
    ))
    fig.update_layout(
        height=360,
        margin={"l": 8, "r": 8, "t": 10, "b": 8},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.06, "x": 0.02},
        yaxis_title="시작값 100",
        xaxis_title=None,
    )
    return fig


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
            status = {"enabled": True, "proxy": proxy, "reachable": False, "error": str(exc)}
        st.metric("터널", "UP" if status.get("reachable") else "DOWN",
                  help=status.get("proxy") or proxy)
        if status.get("error"):
            st.caption(f"상태: {status['error']}")
        pages = st.number_input("조회 페이지", min_value=1, max_value=5, value=2, step=1,
                                key="arca_proxy_pages")
        if st.button("Arca 프록시 수집", type="primary", width="stretch",
                     help="127.0.0.1:1080 SOCKS 터널로 아카라이브 주식채널을 조회합니다. "
                          "Cloudflare challenge는 우회하지 않고 실패로 표시합니다."):
            with st.spinner("Arca를 SOCKS 터널로 조회 중..."):
                result = context.ingest_arca_proxy(max_pages=int(pages), proxy=proxy)
            _context_pack.clear()
            if result.get("ok"):
                st.success(f"수집 {result.get('fetched', 0)}건 · 캐시 {result.get('written', 0)}건 · "
                           f"메모리 {result.get('changed', 0)}건")
                for row in result.get("events", [])[:6]:
                    st.markdown(f"- [{row.get('category', 'arca')}] {row.get('title')} — {row.get('url')}")
            else:
                st.warning(f"Arca 수집 실패: {result.get('error') or 'unknown'}")
        st.caption("자동 CAPTCHA/Cloudflare 우회 없음 · 성공한 공개 글만 World Memory에 저장")
    with c2:
        st.markdown("**Toss — 로컬 스냅샷 예정**")
        st.markdown("- 읽기 전용 자산 스냅샷\n- 주문/매매 연결 없음\n- 보유·현금·거래내역 요약만 Context Layer에 반영")
    st.code(
        "Arca: 서버 SOCKS 127.0.0.1:1080 -> source-cache + World Memory\n"
        "Toss: 노트북 local snapshot -> ~/reports/local-connectors/*.json",
        language="text",
    )


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


def _inject_codex_css():
    st.markdown(
        """
        <style>
        .codex-console-title {
          display:flex;
          align-items:flex-end;
          justify-content:space-between;
          gap:16px;
          padding:2px 0 10px;
          border-bottom:1px solid rgba(148,163,184,.18);
          margin-bottom:14px;
        }
        .codex-console-title h1 {
          margin:0;
          font-size:1.55rem;
          line-height:1.15;
          letter-spacing:0;
        }
        .codex-console-title span,
        .codex-kicker {
          color:rgba(148,163,184,.92);
          font-size:.82rem;
        }
        .codex-kicker {
          text-transform:uppercase;
          letter-spacing:.08em;
          margin-bottom:2px;
        }
        .codex-chat-head {
          display:flex;
          align-items:center;
          justify-content:space-between;
          gap:12px;
          padding:9px 11px;
          border:1px solid rgba(148,163,184,.18);
          border-radius:8px;
          background:rgba(15,23,42,.38);
          margin-bottom:10px;
        }
        .codex-chat-head span {
          color:rgba(148,163,184,.86);
          font-size:.78rem;
          overflow-wrap:anywhere;
        }
        .codex-chip {
          display:inline-block;
          margin-left:8px;
          padding:2px 9px;
          border:1px solid rgba(56,189,248,.35);
          border-radius:999px;
          background:rgba(56,189,248,.12);
          color:rgba(125,211,252,.96) !important;
          font-size:.72rem !important;
          font-weight:600;
          vertical-align:middle;
          white-space:nowrap;
        }
        div[data-testid="stChatMessage"] {
          border:1px solid rgba(148,163,184,.18);
          border-radius:8px;
          background:rgba(15,23,42,.28);
          padding:10px 12px;
          margin-bottom:9px;
        }
        div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
          background:rgba(30,41,59,.38);
        }
        div[data-testid="stChatInput"] textarea {
          border-radius:8px;
        }
        .codex-rail-card {
          display:grid;
          grid-template-columns:repeat(2,minmax(0,1fr));
          gap:8px;
          margin:2px 0 10px;
        }
        .codex-rail-card div {
          border:1px solid rgba(148,163,184,.18);
          border-radius:8px;
          background:rgba(15,23,42,.32);
          padding:9px 10px;
          min-height:58px;
        }
        .codex-rail-card span {
          display:block;
          color:rgba(148,163,184,.82);
          font-size:.74rem;
          margin-bottom:3px;
        }
        .codex-rail-card b {
          display:block;
          font-size:.94rem;
          overflow-wrap:anywhere;
        }
        .codex-feed-item {
          border:1px solid rgba(148,163,184,.16);
          border-radius:8px;
          background:rgba(2,6,23,.22);
          padding:8px 9px;
          margin-bottom:7px;
        }
        .codex-feed-item b {
          display:block;
          color:rgba(203,213,225,.96);
          font-size:.78rem;
          margin-bottom:2px;
        }
        .codex-feed-item span {
          display:block;
          color:rgba(226,232,240,.9);
          font-size:.82rem;
          line-height:1.35;
          overflow-wrap:anywhere;
        }
        .widget-flow {
          display:grid;
          grid-template-columns:minmax(0,1fr) 22px minmax(0,1fr) 22px minmax(0,1fr);
          align-items:center;
          gap:8px;
          padding:10px;
          margin:2px 0 14px;
          border:1px solid rgba(16,185,129,.18);
          border-radius:8px;
          background:rgba(6,78,59,.10);
        }
        .widget-flow div {
          display:flex;
          align-items:center;
          justify-content:space-between;
          gap:10px;
          padding:9px 11px;
          border:1px solid rgba(148,163,184,.18);
          border-radius:7px;
          background:rgba(15,23,42,.42);
          min-height:42px;
        }
        .widget-flow b {
          color:rgba(110,231,183,.98);
          font-size:.78rem;
          white-space:nowrap;
        }
        .widget-flow span {
          color:rgba(226,232,240,.94);
          font-size:.86rem;
          text-align:right;
        }
        .widget-flow i {
          display:block;
          height:1px;
          background:linear-gradient(90deg,rgba(16,185,129,.20),rgba(16,185,129,.82));
          position:relative;
        }
        .widget-flow i:after {
          content:"";
          position:absolute;
          right:-1px;
          top:-3px;
          width:7px;
          height:7px;
          border-top:1px solid rgba(16,185,129,.9);
          border-right:1px solid rgba(16,185,129,.9);
          transform:rotate(45deg);
        }
        .widget-card-head {
          padding:10px 11px;
          margin:2px 0 10px;
          border:1px solid rgba(148,163,184,.18);
          border-radius:8px;
          background:rgba(15,23,42,.34);
        }
        .widget-card-head span {
          display:block;
          color:rgba(148,163,184,.88);
          font-size:.75rem;
          margin-bottom:2px;
        }
        .widget-card-head b {
          display:block;
          color:rgba(241,245,249,.98);
          font-size:.98rem;
        }
        .rule-matrix {
          display:grid;
          grid-template-columns:repeat(2,minmax(0,1fr));
          gap:8px;
          margin:10px 0;
        }
        .rule-matrix div {
          border:1px solid rgba(148,163,184,.16);
          border-radius:7px;
          padding:9px 10px;
          background:rgba(2,6,23,.24);
          min-height:58px;
        }
        .rule-matrix span {
          display:block;
          color:rgba(148,163,184,.82);
          font-size:.73rem;
          margin-bottom:3px;
        }
        .rule-matrix b {
          display:block;
          color:rgba(226,232,240,.96);
          font-size:.9rem;
          overflow-wrap:anywhere;
        }
        .scenario-row {
          display:flex;
          align-items:center;
          justify-content:space-between;
          gap:10px;
          border:1px solid rgba(148,163,184,.16);
          border-radius:8px;
          background:rgba(2,6,23,.22);
          padding:8px 9px;
          margin-bottom:7px;
        }
        .scenario-row b {
          color:rgba(226,232,240,.96);
          font-size:.82rem;
          overflow-wrap:anywhere;
        }
        .scenario-row span {
          color:rgba(148,163,184,.78);
          font-size:.72rem;
          white-space:nowrap;
        }
        @media (max-width: 760px) {
          .widget-flow {
            grid-template-columns:1fr;
          }
          .widget-flow i {
            height:14px;
            width:1px;
            margin:0 auto;
            background:linear-gradient(180deg,rgba(16,185,129,.20),rgba(16,185,129,.82));
          }
          .widget-flow i:after {
            right:-3px;
            top:auto;
            bottom:-1px;
            transform:rotate(135deg);
          }
          .widget-flow span {
            text-align:left;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
