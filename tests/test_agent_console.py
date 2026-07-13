from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AGENT_CONSOLE_SOURCE_CACHE_DIR", str(tmp_path / "reports" / "source-cache"))
    monkeypatch.setenv("AGENT_CONSOLE_ML_DATA_DIR", str(tmp_path / "reports" / "ml-data"))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))


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
        assert "--ask-for-approval" in cmd and "never" in cmd
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("Codex 응답")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    assert _try_codex_chat("테스트", runner=fake_runner) == "Codex 응답"


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
    assert storage.list_memory_events()[0]["source"] == "arca:proxy"
