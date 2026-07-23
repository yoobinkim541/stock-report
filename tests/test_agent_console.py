from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AGENT_CONSOLE_SOURCE_CACHE_DIR", str(tmp_path / "reports" / "source-cache"))
    monkeypatch.setenv("AGENT_CONSOLE_ML_DATA_DIR", str(tmp_path / "reports" / "ml-data"))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    # 단일 월드 메모리(lib.world_memory)도 테스트별 격리 — DB_PATH 는 호출 시점 참조
    from lib import world_memory as _wm
    monkeypatch.setattr(_wm, "DB_PATH", tmp_path / "world_issue_log.sqlite3")


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


def test_wiki_capture_and_context_section(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    page = wiki.capture_from_chat(
        "손실한도 1% 안에서 QQQ와 TQQQ를 비교해줘",
        "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.",
        surface="portfolio",
        title="손실한도와 레버리지",
        status="reviewed",
        kind="playbook",
        tags=["risk", "portfolio"],
        source_refs=["conversation:001"],
    )
    pages = wiki.list_pages(query="손실한도", surface="portfolio")
    section = wiki.build_context_section(query="손실한도", surface="portfolio", limit=4)

    assert page["title"] == "손실한도와 레버리지"
    assert pages and pages[0]["title"] == "손실한도와 레버리지"
    assert "[위키 지식]" in section
    assert "손실한도와 레버리지" in section


def test_wiki_list_pages_prefers_qmd_search_when_available(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    fallback = wiki.capture_from_chat(
        "레버리지 원칙",
        "로컬 점수 검색으로 잡히는 기존 페이지입니다.",
        surface="portfolio",
        title="기존 로컬 후보",
        status="reviewed",
        kind="playbook",
    )
    qmd_target = wiki.capture_from_chat(
        "손실한도 1%",
        "qmd 의미 검색으로 먼저 잡혀야 하는 페이지입니다.",
        surface="portfolio",
        title="qmd 우선 후보",
        status="reviewed",
        kind="playbook",
    )
    calls = []

    class FakeQmd:
        @staticmethod
        def export_pages(pages):
            calls.append(("export", len(pages)))
            return {"ok": True, "files": []}

        @staticmethod
        def search(query, *, limit=10, surface="all", status="all"):
            calls.append(("search", query, limit, surface, status))
            return [
                {
                    "provider": "qmd",
                    "page_id": qmd_target["id"],
                    "title": "qmd 우선 후보",
                    "summary": "qmd hit",
                    "score": 0.98,
                }
            ]

    monkeypatch.setattr(wiki, "qmd_search", FakeQmd, raising=False)

    pages = wiki.list_pages(query="레버리지", surface="portfolio", limit=3)
    section = wiki.build_context_section(query="레버리지", surface="portfolio", limit=3)

    assert pages[0]["id"] == qmd_target["id"]
    assert pages[0]["search_provider"] == "qmd"
    assert fallback["id"] in {page["id"] for page in pages}
    assert ("export", 2) in calls
    assert "[위키 지식]" in section
    assert "qmd 우선 후보" in section


def test_agent_context_prompt_includes_wiki(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent, context

    monkeypatch.setattr(
        context,
        "context_pack",
        lambda surface: {
            "ok": True,
            "surface": surface,
            "generated_at": "2026-07-13T06:45:00+00:00",
            "sources": {"events": [], "source_counts": [], "symbol_counts": []},
            "reports": [],
            "ml_activity": [],
            "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []},
            "paper": {"kr": None, "us": None, "combined": None, "errors": []},
            "models": {"items": []},
            "memory": [],
            "focus": [],
        },
    )
    monkeypatch.setattr(agent.shared_memory, "build_context_section", lambda payload: "[컨텍스트 메모리]\n- shared")
    monkeypatch.setattr(agent.wiki, "build_context_section", lambda **kwargs: "[위키 지식]\n- wiki card")

    prompt = agent.build_context_prompt("portfolio")

    assert "[컨텍스트 메모리]" in prompt
    assert "[위키 지식]" in prompt
    assert "wiki card" in prompt


def test_wiki_auto_curate_skips_transient_acknowledgements(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    assert wiki.auto_curate_from_chat(
        "진행해줘",
        "ㅇㅇ 진행해",
        surface="portfolio",
        llm=None,
        pack={"focus": []},
        history=[],
    ) is None


def test_wiki_auto_curate_from_chat_updates_existing_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import wiki

    existing = wiki.capture_from_chat(
        "손실한도 1% 기준으로 QQQ와 TQQQ를 비교해줘",
        "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.",
        surface="portfolio",
        title="손실한도와 레버리지",
        status="draft",
        kind="playbook",
        tags=["risk", "portfolio"],
        source_refs=["conversation:001"],
    )

    def fake_llm(prompt: str) -> str:
        assert "JSON object" in prompt
        return (
            '{"action":"update","title":"손실한도와 레버리지","summary":"손실한도 1%에서는 QQQ를 기본으로 두고 TQQQ는 예산을 더 크게 봅니다.",'
            '"body":"손실한도 1%에서는 QQQ를 기본으로 두고 TQQQ는 변동성 예산을 더 크게 잡습니다.\\n- QQQ 기본\\n- TQQQ는 보수적\\n- 현금 완충 필요",'
            '"kind":"playbook","status":"reviewed","tags":["risk","portfolio","leverage"],'
            '"source_refs":["conversation:002"],"target_id":"'
            + existing["id"]
            + '","confidence":0.91,"reason":"merge with existing"}'
        )

    saved = wiki.auto_curate_from_chat(
        "손실한도 1% 안에서 QQQ와 TQQQ를 다시 정리해줘",
        "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.\n- QQQ 기본\n- TQQQ는 보수적\n- 현금 완충 필요",
        surface="portfolio",
        llm=fake_llm,
        pack={"focus": ["포트폴리오"]},
        history=[{"role": "user", "message": "손실한도 1%"}],
    )

    assert saved is not None
    assert saved["page"]["id"] == existing["id"]
    assert "현금 완충 필요" in saved["page"]["body"]
    assert "leverage" in saved["page"]["tags"]


def test_wiki_api_routes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.server import create_app

    app = create_app()
    client = app.test_client()

    capture = client.post(
        "/api/wiki/capture",
        json={
            "question": "손실한도 1% 기준으로 QQQ와 TQQQ를 비교해줘",
            "answer": "QQQ는 기본, TQQQ는 손실한도와 변동성 예산을 더 크게 씁니다.",
            "surface": "portfolio",
            "title": "손실한도와 레버리지",
            "status": "reviewed",
            "kind": "playbook",
            "tags": ["risk", "portfolio"],
        },
    )
    assert capture.status_code == 200
    page_id = capture.get_json()["page"]["id"]

    got = client.get(f"/api/wiki/pages/{page_id}")
    listed = client.get("/api/wiki/pages?query=손실한도&surface=portfolio&status=all&limit=10")

    assert got.status_code == 200
    assert got.get_json()["page"]["title"] == "손실한도와 레버리지"
    assert listed.status_code == 200
    assert listed.get_json()["pages"][0]["title"] == "손실한도와 레버리지"


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


def test_agent_answer_autocurates_wiki(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent, context

    monkeypatch.setattr(
        context,
        "context_pack",
        lambda surface: {
            "ok": True,
            "surface": surface,
            "generated_at": "2026-07-13T06:45:00+00:00",
            "sources": {"events": [], "source_counts": [], "symbol_counts": []},
            "reports": [],
            "ml_activity": [],
            "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []},
            "paper": {"kr": None, "us": None, "combined": None, "errors": []},
            "models": {"items": []},
            "memory": [],
            "focus": ["포트폴리오 맥락"],
        },
    )
    calls = []

    def fake_curate(question, answer, **kwargs):
        calls.append({"question": question, "answer": answer, "kwargs": kwargs})
        return {"ok": True, "action": "create", "page": {"id": "abc"}}

    monkeypatch.setattr(agent.wiki, "auto_curate_from_chat", fake_curate)
    monkeypatch.setattr(agent, "_compose_answer", lambda question, pack, history=None: "### 답변\n테스트")

    result = agent.answer("손실한도 1% 안에서 QQQ와 TQQQ를 다시 정리해줘", "portfolio")

    assert result["ok"] is True
    assert calls and calls[0]["question"].startswith("손실한도 1%")
    assert calls[0]["kwargs"]["surface"] == "portfolio"


def test_agent_answer_async_postprocess_does_not_block_on_wiki(monkeypatch):
    import threading
    import time

    from agent_console import agent, context

    monkeypatch.setattr(
        context,
        "context_pack",
        lambda surface: {
            "ok": True,
            "surface": surface,
            "generated_at": "2026-07-13T06:45:00+00:00",
            "sources": {"events": [], "source_counts": [], "symbol_counts": []},
            "reports": [],
            "ml_activity": [],
            "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []},
            "paper": {"kr": None, "us": None, "combined": None, "errors": []},
            "models": {"items": []},
            "memory": [],
            "focus": ["포트폴리오 맥락"],
        },
    )
    started = threading.Event()
    release = threading.Event()

    def slow_curate(question, answer, **kwargs):
        started.set()
        release.wait(timeout=2)
        return {"ok": True}

    monkeypatch.setattr(agent.wiki, "auto_curate_from_chat", slow_curate)
    monkeypatch.setattr(agent, "_compose_answer", lambda question, pack, history=None: "빠른 답변")

    t0 = time.monotonic()
    result = agent.answer("후처리 비동기 테스트", "market", async_postprocess=True)
    elapsed = time.monotonic() - t0

    assert result["ok"] is True
    assert result["answer"] == "빠른 답변"
    assert result["context"]["postprocess"]["wiki_autocurate"] == "queued"
    assert elapsed < 0.5
    assert started.wait(timeout=1)
    release.set()
    agent._LAST_POSTPROCESS_THREAD.join(timeout=1)


def test_agent_trading_logic_question_uses_logic_report():
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "market",
        "generated_at": "2026-07-13T05:01:00+00:00",
        "sources": {
            "events": [{"source": "unit", "title": "이란 긴장과 유가 상승"}],
            "source_counts": [("unit", 1)],
            "symbol_counts": [("QQQ", 1)],
        },
        "memory": [],
        "reports": [],
        "paper": {
            "kr": {
                "cum_ret": 1.2, "strat_mdd": 3.5, "bench_mdd": 4.0,
                "cost": {"turnover": 60.0},
                "scorecard": {"buy_hit": 55.0, "n_buy": 12, "sell_hit": None, "n_sell": 0},
                "decisions": [{"ticker": "005930"}] * 20,
            },
            "us": {
                "cum_ret": -0.8, "strat_mdd": 3.9, "bench_mdd": 3.7,
                "cost": {"turnover": 102.0},
                "scorecard": {"buy_hit": None, "n_buy": 0, "sell_hit": None, "n_sell": 0},
                "decisions": [{"ticker": "QQQ"}] * 5,
            },
        },
        "ml_activity": [
            {"_file": "kr_intraday_decisions.jsonl", "ticker": "005930"},
            {"_file": "kr_intraday_outcomes.jsonl", "success": True, "net_pnl": 1000},
        ],
    }

    answer = _compose_answer("지금 우리가 가지고 있는 모의투자랑 단기투자 로직을 평가해줘", pack)

    assert "모의·단기투자 로직 평가" in answer
    assert "현재 시장 상황 인식" not in answer
    assert "표본" in answer
    assert "시장 설명이 매매 판단을 덮고" in answer


def test_agent_answer_survives_context_pack_failure(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")

    from agent_console import agent, context

    def broken_context(surface):
        raise RuntimeError("context boom")

    monkeypatch.setattr(context, "context_pack", broken_context)

    result = agent.answer("안녕", "portfolio")

    assert result["ok"] is True
    assert result["context"]["context_error"] == "context boom"
    assert "안녕" in result["answer"]


def test_agent_answer_survives_conversation_store_failure(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")

    from agent_console import agent, context, storage

    monkeypatch.setattr(storage, "list_conversation", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db read")))
    monkeypatch.setattr(storage, "add_conversation", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db write")))
    monkeypatch.setattr(
        context,
        "context_pack",
        lambda surface: {
            "ok": True,
            "surface": surface,
            "generated_at": "2026-07-13T06:40:00+00:00",
            "sources": {"events": [], "source_counts": [], "symbol_counts": []},
            "reports": [],
            "ml_activity": [],
            "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []},
            "paper": {"kr": None, "us": None, "combined": None, "errors": []},
            "models": {"items": []},
            "memory": [],
            "focus": [],
        },
    )

    result = agent.answer("안녕", "market")

    assert result["ok"] is True
    assert result["conversation"] == []
    assert "안녕" in result["answer"]


def test_agent_answer_survives_answer_composition_failure(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")

    from agent_console import agent, context, storage

    monkeypatch.setattr(storage, "list_conversation", lambda *args, **kwargs: [])
    monkeypatch.setattr(storage, "add_conversation", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        context,
        "context_pack",
        lambda surface: {
            "ok": True,
            "surface": surface,
            "generated_at": "2026-07-13T06:41:00+00:00",
            "sources": {"events": [], "source_counts": [], "symbol_counts": []},
            "reports": [],
            "ml_activity": [],
            "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []},
            "paper": {"kr": None, "us": None, "combined": None, "errors": []},
            "models": {"items": []},
            "memory": [],
            "focus": [],
        },
    )
    monkeypatch.setattr(agent, "_compose_answer", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad route")))

    result = agent.answer("테스트 질문", "market")

    assert result["ok"] is True
    assert "답변 조립 중 일부 내부 컨텍스트 오류" in result["answer"]
    assert "ValueError" in result["answer"]


def test_agent_portfolio_risk_question_uses_holdings_not_market_template(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "portfolio",
        "generated_at": "2026-07-13T06:01:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "ml_activity": [],
        "paper": {},
        "portfolio": {
            "holdings": [
                {"ticker": "NVDA", "name": "Nvidia", "weight": 32.0, "ret": 18.0, "value": 32000},
                {"ticker": "MU", "name": "Micron", "weight": 24.0, "ret": -7.0, "value": 24000},
                {"ticker": "CASH", "name": "Cash", "weight": 20.0, "ret": 0.0, "value": 20000},
                {"ticker": "QLD", "name": "ProShares Ultra QQQ", "weight": 12.0, "ret": -3.0, "value": 12000},
                {"ticker": "MSFT", "name": "Microsoft", "weight": 12.0, "ret": 4.0, "value": 12000},
            ],
            "summary": {},
            "risk": {},
            "targets": {},
            "errors": [],
        },
    }

    answer = _compose_answer("현재 비중에서 먼저 줄여야 할 리스크를 봐줘", pack)

    assert "먼저 줄일 리스크" in answer
    assert "우선 줄일 후보" in answer
    assert "NVDA" in answer
    assert "MU" in answer
    assert "시장 신호 점수" not in answer
    assert "Codex에게 바로 물어볼 질문" not in answer


def test_agent_portfolio_loss_limit_scenario_uses_loss_budget(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "portfolio",
        "generated_at": "2026-07-13T06:02:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "ml_activity": [],
        "paper": {},
        "portfolio": {
            "holdings": [
                {"ticker": "NVDA", "name": "Nvidia", "weight": 32.0, "ret": 18.0, "value": 32000},
                {"ticker": "MU", "name": "Micron", "weight": 24.0, "ret": -7.0, "value": 24000},
                {"ticker": "CASH", "name": "Cash", "weight": 20.0, "ret": 0.0, "value": 20000},
                {"ticker": "QLD", "name": "ProShares Ultra QQQ", "weight": 12.0, "ret": -3.0, "value": 12000},
                {"ticker": "MSFT", "name": "Microsoft", "weight": 12.0, "ret": 4.0, "value": 12000},
            ],
            "summary": {},
            "risk": {},
            "targets": {},
            "errors": [],
        },
    }

    answer = _compose_answer("최대 손실한도 1% 기준으로 시나리오를 제안해줘", pack)

    assert "최대 손실한도 시나리오" in answer
    assert "계좌 손실한도 1.0%" in answer
    assert "포지션 크기 공식" in answer
    assert "손절폭 5%면 최대 20%" in answer
    assert "시장 신호 점수" not in answer
    assert "Codex에게 바로 물어볼 질문" not in answer


def test_agent_portfolio_keep_holding_followup_respects_preference(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "portfolio",
        "generated_at": "2026-07-13T06:32:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "ml_activity": [],
        "paper": {},
        "portfolio": {
            "holdings": [
                {"ticker": "ORCL", "name": "오라클", "weight": 20.0, "ret": 41.1, "value": 20000},
                {"ticker": "NVDA", "name": "Nvidia", "weight": 18.0, "ret": 12.0, "value": 18000},
                {"ticker": "QLD", "name": "ProShares Ultra QQQ", "weight": 12.0, "ret": -3.0, "value": 12000},
                {"ticker": "MU", "name": "Micron", "weight": 10.0, "ret": -7.0, "value": 10000},
            ],
            "summary": {},
            "risk": {},
            "targets": {},
            "errors": [],
        },
    }
    history = [
        {"role": "user", "message": "현재 비중에서 먼저 줄여야 할 리스크를 봐줘"},
        {"role": "assistant", "message": "먼저 줄일 리스크를 보겠습니다."},
    ]

    answer = _compose_answer("근데 오라클은 들고 가고 싶은데", pack, history=history)

    assert "오라클(ORCL) 유지 조건부 리밸런싱" in answer
    assert "보호 포지션" in answer
    assert "대신 줄일 후보" in answer
    assert "QLD" in answer
    assert "시장 신호 점수" not in answer
    assert "포트폴리오 로직 점검" not in answer


def test_agent_portfolio_keep_weight_followup_beats_risk_template(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "portfolio",
        "generated_at": "2026-07-13T06:34:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "ml_activity": [],
        "paper": {},
        "portfolio": {
            "holdings": [
                {"ticker": "ORCL", "name": "오라클", "weight": 20.0, "ret": 41.1, "value": 20000},
                {"ticker": "QLD", "name": "ProShares Ultra QQQ", "weight": 12.0, "ret": -3.0, "value": 12000},
                {"ticker": "MU", "name": "Micron", "weight": 10.0, "ret": -7.0, "value": 10000},
            ],
            "summary": {},
            "risk": {},
            "targets": {},
            "errors": [],
        },
    }

    answer = _compose_answer("오라클 비중은 유지하고 싶은데", pack, history=[])

    assert "오라클(ORCL) 유지 조건부 리밸런싱" in answer
    assert "보호 포지션" in answer
    assert "먼저 줄일 리스크" not in answer
    assert "시장 신호 점수" not in answer


def test_agent_portfolio_ambiguous_complaint_does_not_use_market_template(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "portfolio",
        "generated_at": "2026-07-13T06:35:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "ml_activity": [],
        "paper": {},
        "portfolio": {
            "holdings": [
                {"ticker": "ORCL", "name": "오라클", "weight": 20.0, "ret": 41.1, "value": 20000},
                {"ticker": "QLD", "name": "ProShares Ultra QQQ", "weight": 12.0, "ret": -3.0, "value": 12000},
            ],
            "summary": {},
            "risk": {},
            "targets": {},
            "errors": [],
        },
    }

    answer = _compose_answer("왜 같은 말만 반복해?", pack)

    assert "시장 신호 점수" not in answer
    assert "포트폴리오 로직 점검" not in answer
    assert "반복" in answer


def test_agent_my_portfolio_question_gives_overview_not_hallucinated_ticker(monkeypatch):
    """'내 포트폴리오 어때' 가 '내'→내수주(326230.KS) 부분매칭으로 단일종목 의견에
    오분류되던 회귀 방지 — 실제 보유 개요를 준다."""
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer, _extract_asset_symbol

    # 흔한 한글어가 티커로 환각되지 않아야 함
    assert _extract_asset_symbol("내 포트폴리오 어때") is None
    assert _extract_asset_symbol("시장 어때") is None
    # 진짜 티커/종목명은 그대로 추출
    assert (_extract_asset_symbol("NVDA 어때") or (None,))[0] == "NVDA"

    pack = {
        "surface": "portfolio",
        "generated_at": "2026-07-15T06:35:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [], "reports": [], "ml_activity": [], "paper": {},
        "portfolio": {
            "holdings": [
                {"ticker": "QQQI", "name": "Neos Nasdaq", "weight": 20.9, "ret": 5.2, "value": 2000},
                {"ticker": "UNH", "name": "유나이티드헬스", "weight": 14.9, "ret": 33.5, "value": 1400},
            ],
            "summary": {}, "risk": {}, "targets": {}, "errors": [],
        },
    }
    answer = _compose_answer("내 포트폴리오 어때", pack)
    assert "내 포트폴리오 현황" in answer
    assert "326230" not in answer and "내수주" not in answer  # 환각 티커 없어야
    assert "QQQI" in answer  # 실제 보유가 나와야


def test_agent_korean_asset_name_routes_to_asset_answer(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "market",
        "generated_at": "2026-07-13T06:36:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "paper": {},
        "ml_activity": [],
    }

    answer = _compose_answer("오라클 어때", pack)

    assert "Oracle(ORCL) 의견" in answer
    assert "시장 신호 점수" not in answer


def test_agent_lab_short_followup_does_not_use_market_template(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "lab",
        "generated_at": "2026-07-13T06:37:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "paper": {},
        "ml_activity": [],
    }

    answer = _compose_answer("이 조건 너무 보수적인데", pack)

    assert "전략랩 맥락" in answer
    assert "시장 신호 점수" not in answer
    assert "현재 시장 상황 인식" not in answer


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


def test_agent_general_question_does_not_force_market_template(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "market",
        "generated_at": "2026-07-13T05:01:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "paper": {},
        "ml_activity": [],
    }

    answer = _compose_answer("안녕 뭐 할 수 있어?", pack)

    assert "현재 시장 상황 인식" not in answer
    assert "할 수 있습니다" in answer or "일반 질문" in answer
    assert "비활성화" not in answer


def test_agent_asset_short_question_handles_sol(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "market",
        "generated_at": "2026-07-13T05:01:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "paper": {},
        "ml_activity": [],
    }

    answer = _compose_answer("sol top 2+ 는 어떄", pack)

    assert "솔라나" in answer
    assert "SOL-USD" in answer
    assert "현재 시장 상황 인식" not in answer
    assert "비활성화" not in answer


def test_agent_followup_correction_remembers_domestic_etf_context(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "market",
        "generated_at": "2026-07-13T05:01:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "paper": {},
        "ml_activity": [],
    }
    history = [
        {"role": "user", "message": "sol ai top 2+ 은 어때"},
        {"role": "assistant", "message": "SOL-USD 기준으로 답할게."},
    ]

    answer = _compose_answer("아니아니 국내 etf", pack, history=history)

    assert "국내 ETF" in answer
    assert "직전 질문" in answer
    assert "SOL-USD" not in answer
    assert "솔라나" not in answer
    assert "모델 응답을 바로 받지는 못했지만" not in answer


def test_agent_domestic_etf_question_does_not_extract_crypto_sol(monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    from agent_console.agent import _compose_answer

    pack = {
        "surface": "market",
        "generated_at": "2026-07-13T05:01:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "reports": [],
        "paper": {},
        "ml_activity": [],
    }

    answer = _compose_answer("SOL AI top 2+ 국내 ETF 어때", pack, history=[])

    assert "국내 ETF" in answer
    assert "SOL-USD" not in answer


def test_agent_codex_chat_runner_writes_last_message(monkeypatch, tmp_path):
    from agent_console.agent import _try_codex_chat

    monkeypatch.setenv("AGENT_CONSOLE_CODEX_CWD", str(tmp_path))

    def fake_runner(cmd, **kwargs):
        out_path = cmd[cmd.index("--output-last-message") + 1]
        assert cmd[:2] == ["codex", "exec"]
        assert "--ephemeral" in cmd
        assert "--sandbox" in cmd and "read-only" in cmd
        # codex-cli 0.144+ 는 exec 모드에 승인 프롬프트가 없어 --ask-for-approval 이 제거됨
        assert "--ask-for-approval" not in cmd
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("Codex 응답")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    assert _try_codex_chat("테스트", runner=fake_runner) == "Codex 응답"


def test_agent_gemini_chat_runner_builds_correct_command(monkeypatch):
    from agent_console.agent import _try_gemini_chat

    def fake_runner(cmd, **kwargs):
        assert cmd[:2] == ["hermes", "chat"]
        assert "-q" in cmd and "테스트" in cmd
        assert "--provider" in cmd and "gemini" in cmd
        assert "--model" in cmd and "gemini-2.5-flash" in cmd

        class Result:
            returncode = 0
            stdout = "Gemini 응답"
            stderr = ""

        return Result()

    assert _try_gemini_chat("테스트", runner=fake_runner) == "Gemini 응답"


def test_agent_gemini_chat_disabled_by_env(monkeypatch):
    from agent_console.agent import _try_gemini_chat

    monkeypatch.setenv("AGENT_CONSOLE_GEMINI_ENABLED", "0")
    assert _try_gemini_chat("테스트", runner=lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("gate off 인데 runner 가 호출됨"))) is None


def test_agent_llm_chat_rejects_unusable_non_korean_codex_output(monkeypatch):
    from agent_console import agent

    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_runner(cmd, capture_output, text, timeout):
        calls.append(cmd)
        if cmd[:2] == ["codex", "exec"]:
            out_path = cmd[cmd.index("--output-last-message") + 1]
            Path(out_path).write_text("你好，我无法给到相关内容。", encoding="utf-8")
        elif cmd[:2] == ["hermes", "chat"] and "gemini" not in cmd:
            return type("R", (), {"returncode": 2, "stdout": "", "stderr": "auth"})()
        elif cmd[:2] == ["hermes", "chat"] and "gemini" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "한국어 Gemini 답변", "stderr": ""})()
        return Result()

    agent._reset_llm_engine()
    answer = agent._try_llm_prompt("한국증시는 어땠어", runner=fake_runner)

    assert answer == "한국어 Gemini 답변"
    assert agent._LAST_LLM_ENGINE == "gemini"


def test_agent_llm_chat_falls_through_codex_hermes_to_gemini(monkeypatch):
    """codex·hermes(openai-codex) 둘 다 실패해도 gemini 폴백이 실답을 채택한다."""
    from agent_console.agent import _try_llm_chat

    def fake_runner(cmd, **kwargs):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "auth expired"

        if cmd[:2] == ["codex", "exec"]:
            return Result()
        if cmd[:2] == ["hermes", "chat"] and "gemini" not in cmd:
            return Result()
        if cmd[:2] == ["hermes", "chat"] and "gemini" in cmd:
            class Ok:
                returncode = 0
                stdout = "Gemini 실답"
                stderr = ""
            return Ok()
        raise AssertionError(f"예상 못한 cmd: {cmd}")

    pack = {"surface": "market", "sources": {"events": []}, "memory": []}
    assert _try_llm_chat("질문", pack, runner=fake_runner) == "Gemini 실답"


def test_server_endpoints(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

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
    assert "현재 시장 상황 인식" in chat["answer"]
    assert "시장 신호 점수" in chat["answer"]
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


def test_infer_surface_routes_by_question_keywords():
    """자동 맥락 라우팅 — 버튼 없이 질문만으로 surface 추론 (순수·무네트워크)."""
    from agent_console import agent

    assert agent.infer_surface("내 포트폴리오에서 먼저 줄여야 할 리스크 봐줘") == "portfolio"
    assert agent.infer_surface("모의투자 성과가 좋아진 이유 나눠줘") == "paper"
    assert agent.infer_surface("이 가설을 백테스트 규칙으로 바꿔줘") == "lab"
    assert agent.infer_surface("오늘 시장 분위기 요약해줘") == "market"
    # 자산 심볼 + 의도어 → ticker (심볼 추출은 _extract_asset_symbol 재사용)
    assert agent.infer_surface("NVDA 지금 매수해도 어때?") == "ticker"


def test_infer_surface_short_followup_keeps_previous():
    """짧은 후속 발화는 직전 맥락 유지 · 빈 질문/이상 default 는 안전 폴백."""
    from agent_console import agent

    assert agent.infer_surface("그럼 왜?", default="portfolio") == "portfolio"
    assert agent.infer_surface("", default="paper") == "paper"
    assert agent.infer_surface("그럼?", default="없는화면") == "market"
    # 긴 일반 질문은 직전 맥락과 무관하게 market
    long_q = "다음 분기 거시 경기 흐름과 인플레이션 전개를 근거와 함께 설명해줘"
    assert agent.infer_surface(long_q, default="portfolio") == "market"


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


def test_codex_chat_includes_web_search_flag(monkeypatch, tmp_path):
    """codex exec 에 --search(웹 검색) 기본 포함 — 최신 정보 보강."""
    from agent_console.agent import _try_codex_chat

    monkeypatch.setenv("AGENT_CONSOLE_CODEX_CWD", str(tmp_path))
    seen = {}

    def fake_runner(cmd, **kwargs):
        seen["cmd"] = cmd
        out_path = cmd[cmd.index("--output-last-message") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("검색 보강 응답")

        class Result:
            returncode = 0
            stdout = ""

        return Result()

    assert _try_codex_chat("최신 뉴스", runner=fake_runner) == "검색 보강 응답"
    assert "--search" in seen["cmd"]


def test_codex_chat_retries_without_search_on_failure(monkeypatch, tmp_path):
    """--search 미지원 구버전 codex → 즉시 실패 시 검색 없이 1회 재시도."""
    from agent_console.agent import _try_codex_chat

    monkeypatch.setenv("AGENT_CONSOLE_CODEX_CWD", str(tmp_path))
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(list(cmd))

        class Result:
            returncode = 1 if len(calls) == 1 else 0
            stdout = "재시도 응답"

        if len(calls) == 2:
            out_path = cmd[cmd.index("--output-last-message") + 1]
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("재시도 응답")
        return Result()

    assert _try_codex_chat("테스트", runner=fake_runner) == "재시도 응답"
    assert len(calls) == 2
    assert "--search" in calls[0] and "--search" not in calls[1]


def test_answer_reports_engine_local_rules_when_llm_off(monkeypatch, tmp_path):
    """LLM off 시 답변 엔진 = local-rules 로 정직 표기 (UI meta 원천)."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")

    from agent_console import agent

    result = agent.answer("안녕", "market")

    assert result["ok"] is True
    assert result["context"]["engine"] == "local-rules"
