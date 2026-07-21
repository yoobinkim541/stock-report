from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AGENT_CONSOLE_SOURCE_CACHE_DIR", str(tmp_path / "reports" / "source-cache"))
    monkeypatch.setenv("AGENT_CONSOLE_ML_DATA_DIR", str(tmp_path / "reports" / "ml-data"))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_AUTOCURATE_ENABLED", "0")
    from lib import world_memory as _wm
    monkeypatch.setattr(_wm, "DB_PATH", tmp_path / "world_issue_log.sqlite3")


def test_sitecustomize_merges_recent_history_and_preserves_followups(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    import sitecustomize  # noqa: F401  - runtime patch side effect
    from agent_console import agent, storage

    storage.add_conversation("user", "포트폴리오에서 오라클은 들고 가고 싶어", "portfolio")
    storage.add_conversation("assistant", "오라클은 보호 포지션으로 두고 다른 고베타를 줄이겠습니다.", "portfolio")

    history = agent.recent_conversation_history("market", limit=10)
    assert any("오라클은 들고 가고 싶어" in row.get("message", "") for row in history)
    assert agent.infer_surface("그럼?", history=history, default="market") == "portfolio"


def test_sitecustomize_answer_uses_merged_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    import sitecustomize  # noqa: F401  - runtime patch side effect
    from agent_console import agent, context, storage

    storage.add_conversation("user", "포트폴리오에서 오라클은 들고 가고 싶어", "portfolio")
    storage.add_conversation("assistant", "오라클은 보호 포지션으로 두고 다른 고베타를 줄이겠습니다.", "portfolio")

    monkeypatch.setattr(
        context,
        "context_pack",
        lambda surface: {
            "ok": True,
            "surface": surface,
            "generated_at": "2026-07-21T00:00:00+00:00",
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
    captured = {}

    def fake_compose(question, pack, history=None):
        captured["history"] = history or []
        return (
            "질문은 이해했습니다: **그럼?**\n\n"
            "지금은 로컬 모델 응답을 바로 받지 못했지만, 질문 자체에 답하는 방향으로 처리하겠습니다."
        )

    monkeypatch.setattr(agent, "_compose_answer", fake_compose)

    result = agent.answer("그럼?", "portfolio")
    assert any("오라클" in row.get("message", "") for row in captured["history"])
    assert "후속으로 이해했습니다" in result["answer"]
    assert result["surface"] == "portfolio"


def test_sitecustomize_humanizes_generic_fallback(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    import sitecustomize  # noqa: F401  - runtime patch side effect
    from agent_console import agent, context

    context_payload = {
        "ok": True,
        "surface": "portfolio",
        "generated_at": "2026-07-21T00:00:00+00:00",
        "sources": {"events": [], "source_counts": [], "symbol_counts": []},
        "reports": [],
        "ml_activity": [],
        "portfolio": {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []},
        "paper": {"kr": None, "us": None, "combined": None, "errors": []},
        "models": {"items": []},
        "memory": [],
        "focus": [],
    }
    monkeypatch.setattr(context, "context_pack", lambda surface: context_payload | {"surface": surface})
    monkeypatch.setattr(
        agent,
        "_compose_answer",
        lambda question, pack, history=None: (
            "질문은 이해했습니다: **그럼?**\n\n"
            "지금은 로컬 모델 응답을 바로 받지 못했지만, 질문 자체에 답하는 방향으로 처리하겠습니다."
        ),
    )

    result = agent.answer("그럼?", "portfolio")
    assert "후속으로 이해했습니다" in result["answer"]
    assert result["surface"] == "portfolio"
