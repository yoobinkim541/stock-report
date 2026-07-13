from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AGENT_CONSOLE_SOURCE_CACHE_DIR", str(tmp_path / "reports" / "source-cache"))
    monkeypatch.setenv("AGENT_CONSOLE_ML_DATA_DIR", str(tmp_path / "reports" / "ml-data"))


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


def test_context_pack_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import context

    pack = context.context_pack("market")
    assert pack["ok"] is True
    assert pack["surface"] == "market"
    assert "sources" in pack
    assert "memory" in pack


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
