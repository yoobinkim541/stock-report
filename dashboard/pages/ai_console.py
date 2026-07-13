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

from agent_console import agent, context, storage
from dashboard import data


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
          <span>context · memory · lab</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    top = st.columns([1.2, 0.7, 0.9], vertical_alignment="bottom")
    surface = top[0].segmented_control(
        "화면 맥락",
        list(_SURFACES),
        default=st.session_state.get("agent_surface", "market"),
        format_func=lambda key: _SURFACES.get(key, key),
        key="agent_surface",
    ) or "market"
    hours = top[1].selectbox("수집 범위", [24, 72, 168, 336], index=1,
                             format_func=lambda h: f"{h}h" if h < 168 else f"{h // 24}d",
                             key="agent_hours")
    if top[2].button("메모리 적재", width="stretch", help="최근 뉴스/리포트/ML 원장을 World Memory로 적재"):
        with st.spinner("최근 컨텍스트를 World Memory에 적재 중..."):
            result = context.ingest_recent_memory(hours=int(hours))
        _context_pack.clear()
        st.toast(f"메모리 {result.get('changed', 0)}건 반영")

    pack = _safe_context(surface, int(hours))
    _context_glance(pack)

    tab_chat, tab_memory, tab_lab, tab_connectors = st.tabs(
        ["대화", "시장 기억", "포트폴리오 랩", "로컬 커넥터"])
    with tab_chat:
        _chat_tab(surface, pack)
    with tab_memory:
        _memory_tab(surface)
    with tab_lab:
        _lab_tab(surface)
    with tab_connectors:
        _connectors_tab()


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
    _ensure_chat_state()
    chat_col, rail_col = st.columns([1.48, 0.72], gap="large")

    with chat_col:
        st.markdown(
            f"<div class='codex-chat-head'><b>{_SURFACES.get(surface, surface)}</b>"
            f"<span>{pack.get('generated_at', '')}</span></div>",
            unsafe_allow_html=True,
        )
        pending = _quick_prompts(surface)
        if pending:
            _run_agent_question(pending, surface)

        for msg in st.session_state["agent_chat_messages"][-16:]:
            role = msg.get("role", "assistant")
            avatar = "🧠" if role == "assistant" else "⌁"
            with st.chat_message(role, avatar=avatar):
                st.markdown(msg.get("content", ""))
                if msg.get("meta"):
                    st.caption(msg["meta"])

        user_text = st.chat_input("AI 콘솔에 질문하기", key=f"agent_chat_input_{surface}")
        if user_text:
            _run_agent_question(user_text, surface)
            st.rerun()

    with rail_col:
        _chat_context_rail(surface, pack)


def _ensure_chat_state():
    if "agent_chat_messages" in st.session_state:
        return
    st.session_state["agent_chat_messages"] = [
        {
            "role": "assistant",
            "content": "현재 시장 자료, 모의투자 원장, World Memory를 읽고 있습니다. 질문을 던지면 이 맥락 안에서 답합니다.",
            "meta": "local context ready",
        }
    ]


def _quick_prompts(surface: str) -> str | None:
    prompts = {
        "market": [
            "오늘 시장 변화가 어디서 시작됐는지 추적해줘",
            "보유종목에 영향을 줄 이벤트만 골라줘",
            "Arca/뉴스/ML 원장이 서로 충돌하는 부분 찾아줘",
        ],
        "portfolio": [
            "현재 비중에서 먼저 줄여야 할 리스크를 봐줘",
            "현금과 레버리지 사용 조건을 다시 잡아줘",
            "최대 손실한도 기준으로 시나리오를 제안해줘",
        ],
        "paper": [
            "모의투자 성과가 좋아진 이유와 나빠진 이유를 나눠줘",
            "단기 트레이딩이 돈을 못 버는 원인을 추적해줘",
            "성공한 결정과 실패한 결정의 공통 feature를 찾아줘",
        ],
        "ticker": [
            "이 종목 추천이 성공/실패할 조건을 정리해줘",
            "뉴스와 기술 추세가 충돌하는지 봐줘",
            "20일/60일 관점의 체크포인트를 나눠줘",
        ],
        "lab": [
            "이 전략랩 가설을 검증 가능한 규칙으로 바꿔줘",
            "레버리지 전략의 손실한도 조건을 설계해줘",
            "실패 시 먼저 꺼야 할 신호를 정해줘",
        ],
    }
    cols = st.columns(3)
    for idx, text in enumerate(prompts.get(surface, prompts["market"])):
        if cols[idx].button(text, key=f"agent_quick_{surface}_{idx}", width="stretch"):
            return text
    return None


def _run_agent_question(question: str, surface: str):
    question = str(question or "").strip()
    if not question:
        return
    st.session_state["agent_chat_messages"].append({"role": "user", "content": question})
    with st.spinner("컨텍스트 읽는 중..."):
        result = agent.answer(question, surface)
    if result.get("ok"):
        ctx = result.get("context") or {}
        meta = (f"events {ctx.get('event_count', 0)} · memory {ctx.get('memory_count', 0)}"
                if ctx else "")
        st.session_state["agent_chat_messages"].append({
            "role": "assistant",
            "content": result.get("answer", ""),
            "meta": meta,
        })
    else:
        st.session_state["agent_chat_messages"].append({
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
          <div><span>surface</span><b>{_SURFACES.get(surface, surface)}</b></div>
          <div><span>events</span><b>{len(events)}</b></div>
          <div><span>memory</span><b>{len(memory)}</b></div>
          <div><span>models</span><b>{len(models)}</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    if c1.button("프롬프트", key=f"agent_prompt_{surface}", width="stretch"):
        st.session_state["agent_show_prompt"] = not st.session_state.get("agent_show_prompt", False)
    if c2.button("초기화", key=f"agent_clear_{surface}", width="stretch"):
        st.session_state.pop("agent_chat_messages", None)
        _ensure_chat_state()
        st.rerun()

    if st.session_state.get("agent_show_prompt"):
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


def _memory_tab(surface: str):
    st.markdown("##### World Memory")
    with st.form("agent_memory_add", clear_on_submit=True):
        c1, c2 = st.columns([1.2, 0.8])
        title = c1.text_input("제목")
        symbols = c2.text_input("심볼/태그", placeholder="QQQ, NVDA, oil")
        body = st.text_area("관찰 내용", height=100)
        submitted = st.form_submit_button("수동 기억 추가", type="primary")
        if submitted:
            payload = {
                "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "dashboard:manual",
                "kind": "market_note",
                "title": title or body[:80] or "수동 메모",
                "body": body,
                "symbols": [x.strip().upper() for x in symbols.replace(",", " ").split() if x.strip()],
                "impact": "context",
                "confidence": 0.65,
                "metadata": {"surface": surface},
            }
            storage.upsert_memory_events([payload])
            _context_pack.clear()
            st.toast("시장 기억 추가 완료")

    rows = storage.list_memory_events(limit=120)
    if not rows:
        st.info("아직 저장된 시장 기억이 없습니다. 상단의 메모리 적재를 먼저 실행해 보세요.")
        return
    df = pd.DataFrame([{
        "시각": r.get("observed_at"),
        "출처": r.get("source"),
        "종류": r.get("kind"),
        "제목": r.get("title"),
        "심볼": ", ".join(r.get("symbols") or []),
        "영향": r.get("impact"),
    } for r in rows])
    st.dataframe(df, hide_index=True, width="stretch", height=360)


def _lab_tab(surface: str):
    st.markdown("##### 포트폴리오 전략랩")
    with st.form("agent_scenario_form"):
        c1, c2 = st.columns([1.4, 0.6])
        name = c1.text_input("시나리오 이름", value="AI 맥락 기반 테스트")
        max_loss = c2.number_input("최대 손실한도 %", min_value=0.0, max_value=100.0, value=8.0, step=0.5)
        desc = st.text_area("전략 가설", placeholder="어떤 시장 맥락에서 어떤 비중 조합을 테스트할지 적어주세요.")
        alloc_text = st.text_area(
            "비중",
            value="QQQ 45 핵심 성장\nTLT 20 금리 방어\nGLD 10 꼬리위험\nCASH 25 기회 대기",
            help="한 줄에 `티커 비중 메모` 형식",
            height=130,
        )
        rules = st.text_area("운용 규칙", placeholder="예: VIX 25 이상이면 레버리지 신규 진입 중단", height=90)
        if st.form_submit_button("시나리오 저장", type="primary"):
            allocations = _parse_allocations(alloc_text)
            total = sum(float(x.get("weight_pct") or 0) for x in allocations)
            scenario = storage.save_scenario({
                "name": name,
                "description": desc,
                "allocations": allocations,
                "rules": {
                    "max_loss_pct": max_loss,
                    "text": rules,
                    "live_orders": False,
                    "actual_asset_link": False,
                },
                "assumptions": {"surface": surface, "total_weight_pct": round(total, 2)},
                "metrics": {"saved_from": "streamlit_dashboard", "allocation_count": len(allocations)},
            })
            st.toast(f"{scenario['name']} 저장 완료 · 합계 {total:.1f}%")

    scenarios = storage.list_scenarios(limit=50)
    if not scenarios:
        st.caption("저장된 시나리오 없음")
        return
    for scenario in scenarios[:12]:
        with st.expander(f"{scenario['name']} · {scenario.get('updated_at', '')}", expanded=False):
            st.write(scenario.get("description") or "설명 없음")
            st.caption(f"손실한도 {scenario.get('rules', {}).get('max_loss_pct', '—')}% · "
                       f"비중합계 {scenario.get('assumptions', {}).get('total_weight_pct', '—')}%")
            allocs = scenario.get("allocations") or []
            if allocs:
                st.dataframe(pd.DataFrame(allocs), hide_index=True, width="stretch")


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
        </style>
        """,
        unsafe_allow_html=True,
    )
