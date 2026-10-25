"""agent_console — storage·context·shared memory·예측시장 테스트."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from agent_console_support import _isolate

def test_storage_memory_and_scenario(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage

    changed = storage.upsert_memory_events(
        [
            {
                "observed_at": "2026-07-13T00:00:00+00:00",
                "source": "test",
                "kind": "market_note",
                "title": "VIX 안정",
                "body": "VIX가 낮아 단기 레버리지 후보를 관찰한다.",
                "symbols": ["QQQ", "TQQQ"],
                "impact": "watch",
                "confidence": 0.7,
            }
        ]
    )
    assert changed == 1
    rows = storage.list_memory_events()
    assert rows[0]["title"] == "VIX 안정"
    assert rows[0]["symbols"] == ["QQQ", "TQQQ"]

    scenario = storage.save_scenario(
        {
            "name": "테스트 전략",
            "allocations": [{"symbol": "QQQ", "weight_pct": 70}, {"symbol": "CASH", "weight_pct": 30}],
            "rules": {"max_loss_pct": 8},
        }
    )
    assert scenario["name"] == "테스트 전략"
    assert storage.list_scenarios()[0]["rules"]["max_loss_pct"] == 8


def test_shared_memory_writes_defer_expensive_summary_refresh(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import shared_memory

    scheduled = []
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: scheduled.append(True), raising=False)
    monkeypatch.setattr(
        shared_memory,
        "refresh_context_memory_summary",
        lambda: (_ for _ in ()).throw(AssertionError("write path must not synchronously rebuild the external memory layer")),
    )

    shared_memory.upsert_record({"id": "wiki-write", "title": "빠른 저장", "tags": ["wiki"]})

    assert scheduled == [True]


def test_storage_conversation_filters_by_surface(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage

    storage.add_conversation("user", "시장 질문", "market")
    storage.add_conversation("assistant", "시장 답변", "market")
    storage.add_conversation("user", "포트폴리오 질문", "portfolio")
    storage.add_conversation("assistant", "포트폴리오 답변", "portfolio")

    market = storage.list_conversation(limit=10, context_surface="market")
    portfolio = storage.list_conversation(limit=10, context_surface="portfolio")
    all_rows = storage.list_conversation(limit=10)

    assert [row["message"] for row in market] == ["시장 질문", "시장 답변"]
    assert [row["message"] for row in portfolio] == ["포트폴리오 질문", "포트폴리오 답변"]
    assert len(all_rows) == 4


def test_context_pack_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context

    pack = context.context_pack("market")
    assert pack["ok"] is True
    assert pack["surface"] == "market"
    assert "sources" in pack
    assert "memory" in pack
    assert pack["shared_memory"]["ok"] is True


def test_context_pack_exposes_replayable_strategy_profile_health(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context

    monkeypatch.setattr(context, "recent_source_events", lambda hours=72, limit=60: [])
    monkeypatch.setattr(context.realtime_market, "build_market_snapshot", lambda: {"status": "unavailable"})
    monkeypatch.setattr(
        context,
        "strategy_profile_health",
        lambda: {
            "kr_intraday": {
                "status": "pause",
                "reason": "stale_intraday_bar",
                "age_seconds": 330.0,
            }
        },
    )

    pack = context.context_pack("market")

    assert pack["strategy_profile_health"]["kr_intraday"]["status"] == "pause"
    assert pack["strategy_profile_health"]["kr_intraday"]["reason"] == "stale_intraday_bar"


def test_context_pack_exposes_prediction_market_summary(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context

    row = {
        "source": "polymarket",
        "title": "Will the Fed cut rates in September?: Yes 63.0%",
        "url": "https://polymarket.com/event/fed-decision-september",
        "collected_at": "2026-07-28T10:00:00+09:00",
        "metrics": {
            "yes_probability": 0.63,
            "volume": 640000.0,
            "liquidity": 18000.0,
            "open_interest": 70000.0,
            "end_date": "2026-09-16T00:00:00Z",
        },
        "classification": {"topic": "Fed", "kind": "prediction_market"},
    }
    monkeypatch.setattr(context, "recent_source_events", lambda hours=72, limit=60: [row])

    pack = context.context_pack("market")

    assert pack["prediction_markets"]["count"] == 1
    item = pack["prediction_markets"]["items"][0]
    assert item["title"].startswith("Will the Fed cut")
    assert item["yes_probability"] == 0.63
    assert item["topic"] == "Fed"
    assert item["source"] == "polymarket"


def test_prediction_market_context_hides_cached_rows_when_source_is_blocked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context

    monkeypatch.setattr(context, "_source_availability", lambda source: {
        "availability": "blocked",
        "availability_reason": "HTTP 451: regional legal restriction",
    } if source == "polymarket" else {})
    state = context.prediction_market_state([{
        "source": "polymarket",
        "title": "Cached probability",
        "collected_at": "2026-08-20T11:00:00+09:00",
        "metrics": {"yes_probability": 0.63},
    }])

    assert state["ok"] is False
    assert state["availability"] == "blocked"
    assert "HTTP 451" in state["error"]
    assert state["count"] == 0
    assert state["items"] == []


def test_prediction_market_context_hides_cached_rows_when_source_is_stale(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import context

    monkeypatch.setattr(context, "_source_availability", lambda source: {
        "availability": "stale",
        "availability_reason": "40h old",
    } if source == "polymarket" else {})
    state = context.prediction_market_state([{
        "source": "polymarket",
        "title": "Old cached probability",
        "metrics": {"yes_probability": 0.9, "volume": 100000},
    }])

    assert state["providers"]["polymarket"]["availability"] == "stale"
    assert state["providers"]["polymarket"]["count"] == 0
    assert state["availability"] == "unavailable"


def test_prediction_market_context_keeps_kalshi_when_polymarket_is_blocked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context

    monkeypatch.setattr(context, "_source_availability", lambda source: {
        "availability": "blocked",
        "availability_reason": "HTTP 451: regional legal restriction",
    } if source == "polymarket" else {"last_success": "2026-08-21T14:00:00+00:00"})
    events = [
        {
            "source": "polymarket",
            "title": "Cached Polymarket probability",
            "metrics": {"yes_probability": 0.63},
        },
        {
            "source": "kalshi",
            "title": "Fresh Kalshi probability",
            "observed_at": "2026-08-21T14:00:00+00:00",
            "metrics": {"yes_probability": 0.57, "market_ticker": "KXFED-CUT", "volume": 100000},
        },
    ]

    state = context.prediction_market_state(events)

    assert state["ok"] is True
    assert state["availability"] == "degraded"
    assert state["count"] == 1
    assert state["items"][0]["source"] == "kalshi"
    assert state["providers"]["polymarket"]["availability"] == "blocked"
    assert state["providers"]["kalshi"]["count"] == 1


def test_prediction_market_context_keeps_provider_probabilities_separate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context

    monkeypatch.setattr(context, "_source_availability", lambda _source: {})
    state = context.prediction_market_state([
        {"source": "polymarket", "title": "Fed cut PM", "metrics": {"yes_probability": 0.63, "volume": 200}},
        {"source": "kalshi", "title": "Fed cut Kalshi", "metrics": {"yes_probability": 0.51, "volume": 100}},
    ])

    assert set(state["providers"]) == {"polymarket", "kalshi"}
    assert [item["yes_probability"] for item in state["items"]] == [0.63, 0.51]
    assert "yes_probability" not in state
    assert "average_probability" not in state


def test_context_paper_state_defaults_to_offline(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_CONSOLE_LIVE_PAPER", raising=False)

    from agent_console import context

    monkeypatch.setattr(
        context,
        "_offline_paper_state",
        lambda: {"kr": {"surface": "offline"}, "us": None, "combined": None, "errors": []},
    )

    assert context.paper_state()["kr"]["surface"] == "offline"


def test_shared_memory_context_contract(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import shared_memory

    record = shared_memory.append_chat_exchange(
        "나는 레버리지는 손실한도 안에서만 쓰고 싶어",
        "최대 손실한도를 먼저 정하고 그 안에서 QLD/TQQQ 후보를 비교하겠습니다.",
        "portfolio",
    )
    packet = shared_memory.build_context_packet(
        {"screen": "portfolio", "query": "레버리지 손실한도", "provider": "codex-cli"}
    )
    section = shared_memory.build_context_section({"screen": "portfolio", "query": "레버리지"})

    assert record is not None
    assert packet["schemaVersion"] == "finance-agent-gui.shared-memory.v1"
    assert "contextMemorySummary" in packet
    assert packet["memories"][0]["title"].startswith("나는 레버리지는")
    assert "[컨텍스트 메모리]" in section
    assert "레버리지" in section


def test_context_packet_does_not_refresh_expensive_summary_synchronously(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import shared_memory

    monkeypatch.setattr(
        shared_memory,
        "refresh_context_memory_summary",
        lambda: (_ for _ in ()).throw(AssertionError("context reads must use the cached snapshot")),
    )

    shared_memory.append_chat_exchange("레버리지 손실한도", "손실한도 안에서만 사용", "portfolio")
    packet = shared_memory.build_context_packet({"query": "레버리지"})

    assert packet["ok"] is True
    assert "레버리지" in packet["contextMemorySummary"]


def test_source_backed_wiki_page_can_be_reviewed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "FOMC 원문 기반 금리 판단",
        "summary": "FOMC 원문과 시장 반응을 대조한 판단입니다.",
        "body": "원문 release 와 가격 반응을 함께 봅니다.",
        "surface": "market",
        "kind": "decision",
        "status": "reviewed",
        "tags": ["macro"],
        "source_refs": ["source:saveticker:2026-07-23", "https://example.com/fomc"],
    })

    assert page["status"] == "reviewed"
    assert page["verification_status"] == "source-backed"
    assert page["trust_warnings"] == []


def test_read_only_shared_memory_inspection_does_not_initialize_store(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import shared_memory

    shared_memory.ensure_store()
    shared_memory._paths()["events"].write_text(
        '{"id":"wiki-read","createdAt":"2026-08-21T00:00:00+00:00","tags":["wiki"]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(shared_memory, "ensure_store", lambda: (_ for _ in ()).throw(AssertionError("write attempted")))

    rows = shared_memory.inspect_records()
    all_rows = shared_memory.all_records()

    assert rows[0]["id"] == "wiki-read"
    assert all_rows[0]["id"] == "wiki-read"


def test_chat_search_is_reserved_for_freshness_sensitive_questions(monkeypatch):
    from agent_console import agent

    captured = []
    monkeypatch.setattr(
        agent,
        "_try_llm_prompt",
        lambda prompt, **kwargs: captured.append(kwargs) or "답변",
    )
    pack = {"surface": "market", "sources": {"events": []}, "memory": []}

    agent._try_llm_chat("왜 이렇게 답했어?", pack)
    assert captured[-1]["search"] is False
    assert captured[-1]["reasoning_effort"] == "medium"

    agent._try_llm_chat("지금 최신 뉴스와 주가를 확인해줘", pack)
    assert captured[-1]["search"] is True
    assert captured[-1]["reasoning_effort"] == "high"


def test_looks_like_chitchat_matches_short_greetings_only():
    """투자 컨텍스트가 필요 없는 짧은 인사말/잡담만 True. 판단이 애매하면 항상
    False(기존처럼 풀 컨텍스트) 로 안전하게 기운다 — 오탐(진짜 투자 질문을 잡담으로
    잘못 판단)이 미탐보다 훨씬 나쁘다."""
    from agent_console import agent

    assert agent._looks_like_chitchat("안녕") is True
    assert agent._looks_like_chitchat("고마워!") is True
    assert agent._looks_like_chitchat("너 이름이 뭐야?") is True
    assert agent._looks_like_chitchat("테스트") is True

    # 투자 관련 질문은 아무리 짧아도 False
    assert agent._looks_like_chitchat("삼성전자 어때") is False
    assert agent._looks_like_chitchat("NVDA 지금 사도 돼?") is False
    # 길이가 길면 인사말 단어가 섞여 있어도 False(안전하게 풀 컨텍스트로)
    assert agent._looks_like_chitchat("고마워, 근데 오늘 코스피 시황이랑 내 포트폴리오 손실한도 좀 알려줘") is False
    assert agent._looks_like_chitchat("") is False


def test_chat_reasoning_effort_is_low_for_chitchat(monkeypatch):
    from agent_console import agent

    monkeypatch.delenv("AGENT_CONSOLE_CHAT_REASONING_EFFORT", raising=False)
    pack = {"surface": "market", "sources": {"events": []}, "memory": []}
    assert agent._chat_reasoning_effort("안녕!", pack) == "low"
    assert agent._chat_reasoning_effort("포트폴리오 리밸런싱 어떻게 하면 좋을까", pack) == "medium"


def test_chat_prompt_skips_heavy_context_sections_for_chitchat(monkeypatch):
    """잡담엔 위키·공유메모리 같은 무거운 컨텍스트 섹션을 아예 안 만든다 — 프롬프트
    크기·조립 비용을 줄여 응답을 더 빠르게 한다. 실제 투자 질문은 그대로 풀 컨텍스트."""
    from agent_console import agent

    monkeypatch.setattr(agent.wiki, "build_context_section", lambda **kwargs: "[위키 지식]\nSENTINEL_WIKI")
    monkeypatch.setattr(agent.shared_memory, "build_context_section", lambda *args, **kwargs: "[컨텍스트 메모리]\nSENTINEL_MEMORY")
    pack = {"surface": "market", "sources": {"events": []}, "memory": []}

    chitchat_prompt = agent._build_general_chat_prompt("안녕!", pack, [])
    assert "SENTINEL_WIKI" not in chitchat_prompt
    assert "SENTINEL_MEMORY" not in chitchat_prompt

    real_prompt = agent._build_general_chat_prompt("오늘 코스피 어때", pack, [])
    assert "SENTINEL_WIKI" in real_prompt
    assert "SENTINEL_MEMORY" in real_prompt


def test_build_wiki_context_section_included_in_curation_prompt(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    wiki.upsert_page({
        "title": "손실한도 규칙",
        "summary": "손실한도 1% 기준",
        "body": "손실한도 1% 기준 본문",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "draft",
        "tags": ["risk"],
        "source_refs": [],
    })

    section = wiki._build_wiki_context_section()
    assert "[현재 위키 상태]" in section
    assert "전체 페이지: 1" in section

    prompt = wiki._build_auto_curation_prompt(
        question="질문",
        answer="답변",
        surface="portfolio",
        candidates=[],
        pack={},
        history=[],
    )
    assert "[현재 위키 상태]" in prompt
    assert "전체 페이지: 1" in prompt


def test_plan_to_page_payload_defaults_missing_kind_to_note():
    """kind 를 안 준 계획은 playbook(특정 종류)이 아니라 note(범용)로 떨어져야 편중이 줄어든다."""
    from agent_console import wiki

    payload = wiki._plan_to_page_payload(
        {"action": "create", "title": "제목", "summary": "요약", "body": "본문"},
        question="질문", answer="답변", surface="market",
    )

    assert payload["kind"] == "note"


def test_archive_stale_pages_preserves_source_digest_provenance(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from datetime import datetime, timedelta, timezone

    from agent_console import wiki

    def _iso(days_ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")

    digest = wiki.upsert_page({
        "title": "오래된 원문 다이제스트",
        "summary": "원문 보존",
        "body": "원문",
        "surface": "ticker",
        "kind": "source_digest",
        "status": "reviewed",
        "source_refs": ["https://example.com/source"],
        "created_at": _iso(120),
        "updated_at": _iso(120),
    })

    result = wiki.archive_stale_pages(max_age_days=14)

    assert result["archived"] == 0
    assert wiki.get_page(digest["id"])["status"] == "reviewed"


def test_portfolio_matrix_dsl_rsi_controls_exposure():
    import pandas as pd

    from agent_console.portfolio_matrix_dsl import rsi_cash_program, run_portfolio_matrix_dsl

    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    prices = pd.DataFrame(
        {
            "QQQ": [
                *range(100, 116),
                *range(116, 92, -1),
            ][:40],
        },
        index=dates,
    )

    result = run_portfolio_matrix_dsl(
        prices,
        {"QQQ": 1.0},
        signal_symbol="QQQ",
        program=rsi_cash_program(30, 70, period=2),
        label="RSI 현금화",
    )

    assert result.ok is True
    assert "Sortino" in result.metrics.columns
    assert result.trades
    assert result.matrix


def test_extract_asset_symbol_exact_ticker_without_intent_word():
    """의도 단어(매수/전망/어때 등) 없이 종목만 언급해도 정확 티커면 인식해야 함(2026-07-25).

    'LLY는 1조 달러가 넘을텐데'가 의도어 부재로 티커 추출에 실패 → 무관한 '시장 상황'
    템플릿으로 빠지던 회귀. 단, 부분매칭(예: "AI"→ABNB, "경기"→ETF)까지 의도어 없이
    허용하면 일반 거시 질문이 오분류되므로, 정확 티커 매칭만 게이트를 우회한다.
    """
    from agent_console.agent import _extract_asset_symbol

    assert _extract_asset_symbol("LLY는 1조 달러가 넘을텐데") == ("LLY", "Eli Lilly")
    assert _extract_asset_symbol("QQQ 요즘 계속 오르네") is not None
    # 일반 거시 질문은 부분매칭 오탐 없이 여전히 None (회귀 방지)
    assert _extract_asset_symbol(
        "다음 분기 거시 경기 흐름과 인플레이션 전개를 근거와 함께 설명해줘"
    ) is None


def test_market_context_question_uses_llm_before_generic_market_template(monkeypatch):
    from agent_console import agent

    pack = {
        "surface": "market",
        "generated_at": "2026-07-23T07:41:00+00:00",
        "sources": {
            "events": [
                {"source": "saveticker", "title": "AI 성장주와 크레딧 스프레드 동반 개선"},
            ],
            "source_counts": [],
            "symbol_counts": [],
        },
        "memory": [],
        "reports": [],
        "ml_activity": [],
        "portfolio": {},
        "paper": {},
        "models": {},
        "focus": [],
    }
    seen = {}

    def fake_llm(question, pack, history=None):
        seen["question"] = question
        return "### LLM 시장 분석\n템플릿이 아니라 LLM이 컨텍스트를 해석했습니다."

    monkeypatch.setattr(agent, "_try_llm_chat", fake_llm)

    answer = agent._compose_answer("오늘 시장 분위기 요약해줘", pack, history=[])

    assert "LLM 시장 분석" in answer
    assert "현재 시장 상황 인식" not in answer
    assert "시장 신호 점수" not in answer
    assert "오늘 시장 분위기 요약해줘" in seen["question"]


def test_domestic_market_question_uses_llm_instead_of_generic_market_template(monkeypatch):
    from agent_console import agent

    pack = {
        "surface": "market",
        "generated_at": "2026-07-23T07:41:00+00:00",
        "sources": {
            "events": [
                {"source": "saveticker", "title": "한국 증시와 원화, AI 지출 낙관론에 상승"},
            ],
            "source_counts": [],
            "symbol_counts": [],
        },
        "memory": [],
        "reports": [],
        "ml_activity": [],
        "portfolio": {},
        "paper": {},
        "models": {},
        "focus": [],
    }
    seen = {}

    def fake_llm(question, pack, history=None):
        seen["question"] = question
        return "### 한국증시 요약\n코스피·코스닥과 원화/외국인 수급을 중심으로 답했습니다."

    monkeypatch.setattr(agent, "_try_llm_chat", fake_llm)

    answer = agent._compose_answer("한국증시는 어땠어", pack, history=[])

    assert "한국증시 요약" in answer
    assert "현재 시장 상황 인식" not in answer
    assert "시장 신호 점수" not in answer
    assert "한국증시는 어땠어" in seen["question"]


def test_server_endpoints(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent as agent_module
    from agent_console import context
    from agent_console.server import create_app

    monkeypatch.setattr(
        context,
        "recent_source_events",
        lambda hours=72, limit=60: [
            {
                "source": "unit",
                "title": "금리 하락",
                "published_at": "2026-07-13T00:00:00+00:00",
                "tickers": ["QQQ"],
            }
        ][:limit],
    )
    monkeypatch.setattr(context, "latest_reports", lambda limit=10: [])
    monkeypatch.setattr(context, "ml_activity", lambda limit=80: [])
    monkeypatch.setattr(context, "paper_state", lambda: {"kr": None, "us": None, "combined": None, "errors": []})
    monkeypatch.setattr(context, "portfolio_state", lambda: {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []})
    monkeypatch.setattr(context, "model_state", lambda: {"items": []})

    def fake_llm(question, pack, history=None):
        agent_module._mark_llm_engine("unit-llm")
        return "**결론**\n금리 하락을 LLM이 해석했습니다."

    monkeypatch.setattr(agent_module, "_try_llm_chat", fake_llm)

    app = create_app()
    client = app.test_client()

    assert client.get("/").status_code == 200
    assert client.get("/api/health").json["ok"] is True
    overview = client.get("/api/context/overview?surface=paper").json
    assert overview["surface"] == "paper"
    assert overview["sources"]["symbol_counts"][0][0] == "QQQ"

    ingest = client.post("/api/memory/ingest", json={"hours": 24}).json
    assert ingest["ok"] is True
    assert ingest["changed"] >= 1

    chat = client.post("/api/agent/chat", json={"surface": "market", "message": "왜 오른 거야?"}).json
    assert chat["ok"] is True
    assert "금리 하락을 LLM이 해석했습니다" in chat["answer"]
    assert "현재 시장 상황 인식" not in chat["answer"]
    assert "시장 신호 점수" not in chat["answer"]
    assert chat["context"]["engine"] == "unit-llm"
    assert client.get("/api/memory?limit=5").json["ok"] is True
    memory_context = client.post("/api/memory/context", json={"screen": "market", "query": "금리"}).json
    assert memory_context["schemaVersion"] == "finance-agent-gui.shared-memory.v1"

    scenario = client.post(
        "/api/portfolio-lab/scenarios",
        json={
            "name": "랩 테스트",
            "allocations": [{"symbol": "QLD", "weight_pct": 20}],
            "rules": {"max_loss_pct": 5},
        },
    ).json
    assert scenario["ok"] is True
    assert client.get("/api/portfolio-lab/scenarios").json["scenarios"][0]["name"] == "랩 테스트"


def test_chart_drawing_snapshots_round_trip_latest_version(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import storage

    drawing = {
        "v": 1,
        "shapes": [{"kind": "trendline", "x0": "2026-01-01", "y0": 100, "x1": "2026-01-02", "y1": 104}],
        "anns": [{"text": "breakout", "x": "2026-01-02", "y": 104}],
    }
    created = storage.save_chart_drawing_snapshot(
        "workspace-1",
        "cw:workspace-1:AAPL:1d:lin",
        drawing,
        source="browser",
    )
    assert created["workspace_id"] == "workspace-1"
    assert created["store_key"] == "cw:workspace-1:AAPL:1d:lin"
    assert created["drawing"] == drawing
    assert created["version"] == 1

    updated_drawing = {**drawing, "shapes": [*drawing["shapes"], {"kind": "hline", "y": 101}]}
    updated = storage.save_chart_drawing_snapshot(
        "workspace-1",
        "cw:workspace-1:AAPL:1d:lin",
        updated_drawing,
        source="browser",
    )

    assert updated["version"] == 2
    assert storage.get_chart_drawing_snapshot("workspace-1", "cw:workspace-1:AAPL:1d:lin")["drawing"] == updated_drawing
    listed = storage.list_chart_drawing_snapshots("workspace-1")
    assert [row["store_key"] for row in listed] == ["cw:workspace-1:AAPL:1d:lin"]
    assert listed[0]["version"] == 2


def test_chart_drawing_snapshot_api_routes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app

    app = create_app()
    client = app.test_client()
    payload = {
        "store_key": "cw:workspace-1:AAPL:1d:lin",
        "drawing": {"v": 1, "shapes": [{"kind": "fib", "x0": "2026-01-01", "x1": "2026-01-10"}]},
        "source": "browser",
    }

    saved = client.post("/api/chart-workspaces/workspace-1/drawings", json=payload)
    assert saved.status_code == 200
    assert saved.json["ok"] is True
    assert saved.json["snapshot"]["version"] == 1

    fetched = client.get(
        "/api/chart-workspaces/workspace-1/drawings",
        query_string={"store_key": "cw:workspace-1:AAPL:1d:lin"},
    )
    assert fetched.status_code == 200
    assert fetched.json["ok"] is True
    assert fetched.json["snapshot"]["drawing"] == payload["drawing"]

    listing = client.get("/api/chart-workspaces/workspace-1/drawings/list")
    assert listing.status_code == 200
    assert listing.json["snapshots"][0]["store_key"] == "cw:workspace-1:AAPL:1d:lin"


def test_context_pack_exposes_strategy_studio_state(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context, storage

    storage.save_strategy_spec({
        "name": "EMA trend",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "ema_fast", "kind": "ema", "period": 20, "source": "close"}],
        "rules": {"entry": [{"field": "close", "op": ">", "ref": "ema_fast"}], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 1},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass", "min_trades": 1},
    })

    pack = context.context_pack("lab")

    assert pack["strategy_studio"]["ok"] is True
    assert pack["strategy_studio"]["spec_count"] == 1
    assert pack["strategy_studio"]["latest"]["name"] == "EMA trend"


def test_ingest_arca_proxy(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context, storage
    from reports import source_collector

    monkeypatch.setattr(source_collector, "arca_proxy_status", lambda proxy=None: {"reachable": True, "proxy": proxy})
    monkeypatch.setattr(
        source_collector,
        "fetch_arca_events",
        lambda max_pages=2, proxy=None, prefer_proxy=False: [
            {
                "source": "arca",
                "title": "📰뉴스 QQQ 반등",
                "url": "https://arca.live/b/stock/444",
                "category": "📰뉴스",
                "tickers": ["QQQ"],
            }
        ],
    )

    result = context.ingest_arca_proxy(max_pages=1, proxy="socks5://127.0.0.1:1080")

    assert result["ok"] is True
    assert result["fetched"] == 1
    assert result["changed"] == 1
    # 단일 진실원 = lib.world_memory (콘솔 자체 테이블이 아니라 공용 타임라인에 적재)
    from lib import world_memory

    rows = world_memory.timeline(limit=5)
    assert rows and rows[0]["title"].startswith("📰뉴스 QQQ")
    assert rows[0]["source"] == "arca:proxy"
    assert storage.list_memory_events() == []  # 콘솔 로컬 테이블엔 더 안 씀


def test_context_pack_memory_reads_unified_world_memory(monkeypatch, tmp_path):
    """컨텍스트 팩 memory = lib.world_memory 타임라인 (크론·/ask 와 같은 축적)."""
    _isolate(monkeypatch, tmp_path)

    from agent_console import context
    from lib import world_memory

    world_memory.log_issue("반도체 수출 규제 확대", category="정책", importance="high",
                           tickers=["NVDA"], body="규제 대상 확대 발표", source="test")

    rows = context.world_memory_rows(limit=10)
    assert rows and rows[0]["title"] == "반도체 수출 규제 확대"
    assert rows[0]["symbols"] == ["NVDA"]

    pack = context.context_pack("market")
    assert any(m.get("title") == "반도체 수출 규제 확대" for m in pack["memory"])


def test_ingest_recent_memory_writes_world_not_console_table(monkeypatch, tmp_path):
    """메모리 적재 버튼 → 월드 메모리 기록 (ML 원장은 오염 방지 위해 제외)·멱등."""
    _isolate(monkeypatch, tmp_path)

    from agent_console import context, storage

    monkeypatch.setattr(context, "recent_source_events",
                        lambda hours=72, limit=120: [
                            {"source": "saveticker", "title": "AI 서버 수요 급증",
                             "tickers": ["NVDA"], "published_at": "2026-07-14T01:00:00+00:00"}])
    monkeypatch.setattr(context, "latest_reports", lambda limit=8: [])

    first = context.ingest_recent_memory(hours=24)
    second = context.ingest_recent_memory(hours=24)

    assert first["changed"] == 1
    assert second["changed"] == 0          # dedupe 멱등
    from lib import world_memory
    assert world_memory.timeline(limit=5)[0]["title"] == "AI 서버 수요 급증"
    assert storage.list_memory_events() == []


def test_migrate_memory_moves_console_rows_to_world(monkeypatch, tmp_path):
    """마이그레이션 CLI — 구 콘솔 market_memory → 단일 월드 메모리 (재실행 안전)."""
    _isolate(monkeypatch, tmp_path)

    from agent_console import migrate_memory, storage

    storage.upsert_memory_events([
        {"observed_at": "2026-07-10T00:00:00+00:00", "source": "arca:proxy",
         "kind": "community_signal", "title": "이관 대상 메모", "symbols": ["QQQ"]},
    ])

    out1 = migrate_memory.migrate_world_memory()
    out2 = migrate_memory.migrate_world_memory()   # 재실행 → 전부 중복 스킵

    assert out1["moved"] == 1
    assert out2["moved"] == 0 and out2["skipped_dup"] == 1
    from lib import world_memory
    assert world_memory.timeline(limit=5)[0]["title"] == "이관 대상 메모"


def test_shared_memory_dir_defaults_to_lib_location(monkeypatch):
    """공유 메모리 기본 디렉토리 = lib/agent_memory 와 동일 (AGENT_MEMORY_DIR 존중)."""
    from agent_console import shared_memory

    monkeypatch.delenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", raising=False)
    monkeypatch.setenv("AGENT_MEMORY_DIR", "/tmp/unified-mem")
    assert str(shared_memory.shared_memory_dir()) == "/tmp/unified-mem"

    monkeypatch.delenv("AGENT_MEMORY_DIR", raising=False)
    assert str(shared_memory.shared_memory_dir()).endswith(".local/share/stock-report/shared-memory")


def test_evidence_context_usage_summary_counts_real_sources(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.evidence_context import build_usage_summary, format_usage_lines

    pack = {
        "sources": {"events": [{"title": "a"}, {"title": "b"}]},
        "memory": [{"title": "m"}],
        "market_snapshot": {"quotes": [{"symbol": "QQQ"}, {"symbol": "NVDA"}], "status": "ok"},
        "paper": {"kr": {"decisions": [{"id": 1}, {"id": 2}, {"id": 3}]}},
    }
    summary = build_usage_summary(
        pack,
        wiki_pages=[{"id": "w1"}, {"id": "w2"}],
        intent={"name": "market_analysis"},
    )

    assert summary == {
        "intent": "market_analysis",
        "events": 2,
        "wiki": 2,
        "realtime": 2,
        "logs": 3,
        "engine": "pending",
        "retrieval": {"quote": "ok", "news": "ok", "broker": "unavailable"},
    }
    assert format_usage_lines(summary) == [
        "맥락: 시장 events 2 / wiki 2 / 실시간 2 / 로그 3",
        "엔진: pending",
        "수집: quote ok, news ok, broker unavailable",
    ]


def test_market_prompt_includes_prediction_market_context(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    pack = {
        "surface": "market",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "market_snapshot": {"quotes": []},
        "prediction_markets": {
            "count": 1,
            "items": [
                {
                    "title": "Will the Fed cut rates in September?: Yes 63.0%",
                    "yes_probability": 0.63,
                    "volume": 640000.0,
                    "liquidity": 18000.0,
                    "topic": "Fed",
                    "url": "https://polymarket.com/event/fed-decision-september",
                    "end_date": "2026-09-16T00:00:00Z",
                }
            ],
        },
    }

    prompt = agent._build_market_context_prompt("시장 위험을 폴리마켓까지 보고 판단해줘", pack)

    assert "[예측시장]" in prompt
    assert "Yes 63.0%" in prompt
    assert "검증된 사실이 아니라" in prompt


def test_intent_names_match_evidence_strategy_spec(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    assert agent._classify_question_intent("왜 이렇게 답했어?")["name"] == "meta_debug"
    assert agent._classify_question_intent("JP모건 다른 IB랑 비교해줘")["name"] == "stock_compare"
    assert agent._classify_question_intent("한국증시는 어땠어")["name"] == "market_analysis"
    assert agent._classify_question_intent("단기투자 실적이 안좋은 이유가 뭘까")["name"] == "strategy_review"
    assert agent._classify_question_intent("지금 수급이랑 코스피 선물 확인해줘")["name"] == "live_market_check"
    assert agent._classify_question_intent("LLM wiki에 뭐가 쌓였어")["name"] == "wiki_lookup"


def test_earnings_questions_do_not_route_to_strategy_review(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    assert agent._classify_question_intent("JP모건 실적을 다른 IB랑 비교해줘")["name"] == "stock_compare"
    assert agent._classify_question_intent("삼성전자 실적 어땠어")["name"] == "ticker_research"


def test_special_intents_are_authoritative_in_the_llm_prompt(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    pack = {
        "surface": "market",
        "sources": {"events": []},
        "memory": [],
        "portfolio": {"holdings": []},
        "paper": {},
        "models": {"items": []},
        "market_snapshot": {"quotes": []},
    }
    prompts = []
    monkeypatch.setattr(
        agent,
        "_try_llm_prompt",
        lambda prompt, **kwargs: prompts.append(prompt) or "질문 의도에 맞춘 답변",
    )

    cases = [
        ("QQQ 기술적 분석해줘", "technical_analysis"),
        ("지금 수급이랑 코스피 선물 확인해줘", "live_market_check"),
        ("JP모건 실적을 다른 IB랑 비교해줘", "stock_compare"),
    ]
    for question, expected_intent in cases:
        prompts.clear()
        assert agent._compose_answer(question, pack, history=[]) == "질문 의도에 맞춘 답변"
        assert len(prompts) == 1
        assert f"intent: {expected_intent}" in prompts[0]


def test_stock_compare_contract_forbids_market_template_and_sets_peers(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    intent = agent._classify_question_intent("JP모건 다른 IB랑 비교해줘")
    lines = "\n".join(agent._intent_contract_lines(intent))

    assert intent["subject"] == "JPM"
    assert intent["default_peers"] == ["JPM", "GS", "MS", "BAC", "C"]
    assert "Yahoo Finance 최신 시세" in lines
    assert "현재 시장 상황 인식" in lines
    assert "시장 신호 점수" in lines
    assert "피어 비교표" in lines


def test_forbidden_template_contract_allows_exclusion_wording_but_blocks_risk_on(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    technical = agent._classify_question_intent("QQQ 기술적 분석해줘")
    assert agent._violates_forbidden_templates(
        "뉴스 제외하고 가격과 거래량만 보겠습니다.",
        technical,
    ) is False

    for question in (
        "QQQ 기술적 분석해줘",
        "JP모건 다른 IB랑 비교해줘",
        "왜 이렇게 답했어?",
    ):
        intent = agent._classify_question_intent(question)
        assert agent._violates_forbidden_templates("현재는 RISK-ON 구간입니다.", intent) is True


def test_general_llm_answer_rejects_forbidden_market_template(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    pack = {
        "surface": "market",
        "sources": {"events": []},
        "memory": [],
        "portfolio": {"holdings": []},
        "paper": {},
        "models": {"items": []},
        "market_snapshot": {"quotes": []},
    }
    monkeypatch.setattr(agent, "_try_llm_chat", lambda *args, **kwargs: "현재 시장 상황 인식\n시장 신호 점수\n엉뚱한 답")
    monkeypatch.setattr(agent, "_fallback_general_chat", lambda question, pack, history: "직접 답변 fallback")
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")

    out = agent._compose_answer("왜 이렇게 답했어?", pack, history=[])

    assert out == "직접 답변 fallback"


def test_structured_patch_route_rejects_patch_without_sandbox_save(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "propose_strategy_patch_with_llm", lambda *args, **kwargs: {
        "ok": True,
        "error": "patch_rejected",
        "patch": {"providers": {"features": [{"path": "../../secret"}]}},
        "patched_spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "save_strategy_version", lambda *args, **kwargs: pytest.fail("rejected patch must not save"))

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/patch",
        json={"question": "run this command", "save_sandbox": True},
    )

    assert response.status_code == 400
    assert response.get_json()["sandbox"]["saved"] is False


def test_forged_activation_token_cannot_authorize_live_save(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio

    forged = strategy_studio._ValidatedActivationToken()
    with pytest.raises(ValueError, match="server-issued capability"):
        strategy_studio.save_strategy_version(
            "spec-1",
            {"name": "live", "base_symbol": "AAPL", "promotion": {"environment": "live"}},
            source="live_activation",
            activation_token=forged,
        )


def test_draft_save_cannot_persist_live_environment(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console.server import create_app

    response = create_app().test_client().post(
        "/api/strategy-studio/specs",
        json={
            "name": "unsafe draft",
            "base_symbol": "AAPL",
            "promotion": {"environment": "live"},
        },
    )

    assert response.status_code == 400
    assert "activation" in response.get_json()["error"]


def test_activation_rejects_contradictory_cpcv_aliases_even_when_gate_claims_acceptance(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "version": 1, "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "_run_strategy_spec_internal", lambda *args, **kwargs: ({
        "ok": True,
        "validation": {
            "validation_mode": "cpcv",
            "promotion_eligible": True,
            "aggregate": {
                "provenance_ok": True,
                "fold_count": 1,
                "cpcv_chronology_ok": True,
                "cpcv_chronology_evidence": [{
                    "fold_id": "cpcv-0",
                    "valid": True,
                    "future_training": False,
                    "no_future_training": False,
                    "train_max": "2026-01-01T00:00:00Z",
                    "test_min": "2026-01-02T00:00:00Z",
                    "train_before_test": True,
                }],
            },
            "provenance": {"ok": True, "checks": [{"fold": "cpcv-0", "ok": True, "provenance_ok": True}]},
            "folds": [{
                "path_id": "cpcv-0",
                "train_max": "2026-01-01T00:00:00Z",
                "test_min": "2026-01-02T00:00:00Z",
                "chronology_evidence": {"train_max": "2026-01-03T00:00:00Z"},
            }],
        },
        "provenance": {"ok": True, "checks": [{"fold": "cpcv-0", "ok": True, "provenance_ok": True}]},
        "data_quality": {"status": "complete", "ok": True},
        "promotion": {"accepted": True, "activation_safe": True, "preview": False, "failed_checks": []},
    }, None))
    monkeypatch.setattr(strategy_studio, "save_strategy_version", lambda *args, **kwargs: pytest.fail("save must not execute"))

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/activate",
        json={"environment": "live", "confirm_live": True},
    )

    assert response.status_code == 409
    assert "cpcv_chronology_evidence" in response.get_json()["activation"]["failed_checks"]


def test_full_validation_routes_use_real_folds_and_keep_preview_single_pass_only(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    prices = pd.DataFrame(
        {"AAPL__close": range(100, 112)},
        index=pd.date_range("2026-01-01", periods=12, freq="D", tz="UTC"),
    )
    monkeypatch.setattr(strategy_studio, "_load_prices", lambda *args, **kwargs: prices)
    monkeypatch.setattr(
        strategy_studio,
        "preview_strategy_spec",
        lambda *args, **kwargs: pytest.fail("full validation must not call preview_strategy_spec"),
    )
    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1",
        "spec": {
            "name": "full",
            "base_symbol": "AAPL",
            "universe": {"type": "list", "symbols": ["AAPL"]},
            "rules": {"entry": [], "exit": []},
            "sizing": {"type": "fixed_pct", "position_pct": 1.0},
            "validation": {
                "mode": "purged_walk_forward",
                "label_horizon": 0,
                "train_bars": 4,
                "test_bars": 2,
                "step_bars": 2,
                "embargo_bars": 0,
                "min_test_periods": 1,
            },
        },
    })
    client = create_app().test_client()

    for endpoint in ("run", "validate"):
        response = client.post(
            f"/api/strategy-studio/specs/spec-1/{endpoint}",
            json={"mode": "purged_walk_forward", "period": "1y"},
        )
        body = response.get_json()
        assert response.status_code == 200
        assert body["validation_mode"] == "purged_walk_forward"
        assert len(body["validation"]["folds"]) == body["validation"]["aggregate"]["fold_count"]
        assert body["validation"]["provenance"]["checks"]
        assert "_activation_token" not in body
        json.dumps(body)


def test_empty_full_validation_keeps_payload_and_capability_tuple(monkeypatch):
    from agent_console import strategy_studio

    monkeypatch.setattr(strategy_studio, "_load_prices", lambda *args, **kwargs: pd.DataFrame())
    strategy = strategy_studio.StrategySpec.from_dict({
        "name": "empty-validation",
        "base_symbol": "AAPL",
        "validation": {"mode": "purged_walk_forward"},
    })

    result = strategy_studio._run_full_validation(
        strategy,
        period="1y",
        validation_mode="purged_walk_forward",
        spec_id="spec-1",
        activation_environment=None,
    )

    assert isinstance(result, tuple)
    assert len(result) == 2
    payload, token = result
    assert payload["ok"] is False
    assert payload["validation"]["folds"] == []
    assert token is None


def test_rejected_full_validation_route_returns_structured_json_failure(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    prices = pd.DataFrame(
        {"AAPL__close": range(100, 112)},
        index=pd.date_range("2026-01-01", periods=12, freq="D", tz="UTC"),
    )
    monkeypatch.setattr(strategy_studio, "_load_prices", lambda *args, **kwargs: prices)
    monkeypatch.setattr(strategy_studio, "evaluate_validation_folds", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        strategy_studio,
        "preview_strategy_spec",
        lambda *args, **kwargs: pytest.fail("rejected full validation must not fall back to preview"),
    )
    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1",
        "spec": {
            "name": "malformed-validation",
            "base_symbol": "AAPL",
            "validation": {
                "mode": "purged_walk_forward",
                "label_horizon": 0,
                "train_bars": 4,
                "test_bars": 2,
                "step_bars": 2,
                "embargo_bars": 0,
            },
        },
    })

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/validate",
        json={"mode": "purged_walk_forward"},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error"] == "full_validation_failed"
    assert body["validation"]["promotion_eligible"] is False
    assert body["promotion"]["accepted"] is False
    assert body["errors"]
    json.dumps(body)


def test_provider_patch_schema_rejects_malformed_unknown_and_path_values():
    from agent_console.strategy_studio import validate_strategy_patch

    base = {"name": "test", "base_symbol": "AAPL"}
    malformed = [
        {"providers": {"features": {"plugin": "momentum"}}},
        {"providers": {"features": [{"plugin": "momentum", "unknown": True}]}},
        {"providers": {"signal": {"type": "ensemble", "members": "not-a-list"}}},
        {"providers": {"indicators": [{"name": "ema", "period": "14"}]}},
        {"providers": {"features": [{"plugin": "not-registered"}]}},
        {"providers": {"signal": {"type": "not-a-signal"}}},
        {"providers": {"signal": {"plugin": "not-registered"}}},
        {"providers": {"indicators": [{"kind": "not-an-indicator"}]}},
        {"providers": {"features": [{"plugin": "momentum", "path": "../../secret"}]}},
        {"providers": {"features": [{"plugin": "momentum", "python_path": "safe"}]}},
        {"providers": {"features": [{"plugin": "momentum", "source": "exec('/tmp/x')"}]}},
    ]

    for patch in malformed:
        errors = validate_strategy_patch(patch, base)
        assert errors, patch

    assert validate_strategy_patch(
        {
            "providers": {
                "features": [{"plugin": "momentum", "lookback": 20, "enabled": True}],
                "signal": {"type": "rule", "members": [{"type": "rule"}]},
            },
        },
        base,
    ) == []


def test_live_save_cannot_be_authorized_by_source_alone(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio

    with pytest.raises(ValueError, match="validated activation token"):
        strategy_studio.save_strategy_version(
            "spec-1",
            {"name": "live", "base_symbol": "AAPL", "promotion": {"environment": "live"}},
            source="live_activation",
        )


def test_activation_requires_fold_data_quality_and_top_level_provenance(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "_run_strategy_spec_internal", lambda *args, **kwargs: ({
        "ok": True,
        "validation_mode": "purged_walk_forward",
        "validation": {
            "validation_mode": "purged_walk_forward",
            "promotion_eligible": True,
            "aggregate": {"provenance_ok": True},
            "provenance": {"ok": False, "checks": []},
            "folds": [],
        },
        "provenance": {"ok": False, "checks": []},
        "data_quality": {"status": "complete", "ok": True},
        "promotion": {"accepted": True, "activation_safe": True, "preview": False, "failed_checks": []},
    }, None))
    monkeypatch.setattr(strategy_studio, "save_strategy_version", lambda *args, **kwargs: pytest.fail("save must not execute"))

    response = create_app().test_client().post(
        "/api/strategy-studio/specs/spec-1/activate",
        json={"environment": "live", "confirm_live": True},
    )

    assert response.status_code == 409
    failed = response.get_json()["activation"]["failed_checks"]
    assert "folds" in failed
    assert "provenance" in failed


def test_legacy_chat_and_default_preview_routes_remain_compatible(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import agent, strategy_studio
    from agent_console.server import create_app

    monkeypatch.setattr(strategy_studio, "get_strategy_spec", lambda *args, **kwargs: {
        "id": "spec-1", "spec": {"name": "test", "base_symbol": "AAPL"},
    })
    monkeypatch.setattr(strategy_studio, "preview_strategy_spec", lambda *args, **kwargs: {"ok": True, "report": {}})
    monkeypatch.setattr(agent, "answer", lambda message, surface: {"ok": True, "answer": "legacy", "surface": surface})
    client = create_app().test_client()

    preview = client.post("/api/strategy-studio/specs/spec-1/preview", json={})
    chat = client.post("/api/agent/chat", json={"message": "안녕"})
    assert preview.status_code == 200
    assert preview.get_json()["ok"] is True
    assert chat.status_code == 200
    assert chat.get_json()["answer"] == "legacy"
