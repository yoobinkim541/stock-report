"""짧은 후속 발화의 규칙 기반 폴백 문구 — agent.answer 내장 경로 (무LLM).

예전엔 sitecustomize.py 가 agent.answer 를 통째로 교체해 같은 문구를 붙였다(master 2b5b1a8 로 삭제).
이 문구는 이제 agent._humanize_generic_fallback 에만 있으므로, 그 계약을 네이티브 answer 로 고정한다.
"""
from __future__ import annotations

from agent_console_support import _isolate

_GENERIC_FALLBACK = (
    "질문은 이해했습니다: **그럼?**\n\n"
    "지금은 로컬 모델 응답을 바로 받지 못했지만, 질문 자체에 답하는 방향으로 처리하겠습니다."
)


def _context_payload(surface: str) -> dict:
    return {
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
    }


def _setup(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_AUTOCURATE_ENABLED", "0")
    from agent_console import context

    monkeypatch.setattr(context, "context_pack", _context_payload)


def test_short_followup_fallback_names_the_previous_question(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from agent_console import agent, storage

    storage.add_conversation("user", "포트폴리오에서 오라클은 들고 가고 싶어", "portfolio")
    storage.add_conversation("assistant", "오라클은 보호 포지션으로 두고 다른 고베타를 줄이겠습니다.", "portfolio")
    captured = {}

    def fake_compose(question, pack, history=None):
        captured["history"] = history or []
        return _GENERIC_FALLBACK

    monkeypatch.setattr(agent, "_compose_answer", fake_compose)

    result = agent.answer("그럼?", "portfolio")
    assert any("오라클" in row.get("message", "") for row in captured["history"])
    assert "“포트폴리오에서 오라클은 들고 가고 싶어”" in result["answer"]
    assert "후속으로 이해했습니다" in result["answer"]
    assert result["surface"] == "portfolio"


def test_short_followup_fallback_without_history_keeps_the_question_intent(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from agent_console import agent

    monkeypatch.setattr(agent, "_compose_answer", lambda question, pack, history=None: _GENERIC_FALLBACK)

    result = agent.answer("그럼?", "portfolio")
    assert "짧은 후속으로 이해했습니다" in result["answer"]
    assert result["surface"] == "portfolio"
