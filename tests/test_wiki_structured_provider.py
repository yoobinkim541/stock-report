"""Offline provider ordering, strict JSON and bounded wiki-call budgets."""
import json

import pytest

from agent_console import agent
from lib import llm_cli, llm_direct
from reports import wiki_source_compiler


@pytest.fixture
def providers(monkeypatch):
    calls = []
    results = {"codex": None, "openrouter": None, "hermes": None}
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "1")
    monkeypatch.setenv("LLM_DIRECT_OPENROUTER", "true")
    monkeypatch.delenv("WIKI_STRUCTURED_OPENROUTER_MODEL", raising=False)

    def invoke(name):
        def call(prompt, *args, **kwargs):
            calls.append((name, prompt, args, kwargs))
            value = results[name]
            if isinstance(value, Exception):
                raise value
            return value
        return call

    monkeypatch.setattr(agent, "_try_codex_chat", invoke("codex"))
    monkeypatch.setattr(llm_direct, "direct_chat", invoke("openrouter"))
    monkeypatch.setattr(agent, "_try_hermes_chat", invoke("hermes"))
    return calls, results


@pytest.mark.parametrize("winner", ["codex", "openrouter", "hermes", None])
def test_ordered_fallback_accepts_only_complete_objects(providers, winner):
    calls, results = providers
    results.update({"codex": '{"duplicate":1,"duplicate":2}',
                    "openrouter": 'prose {"selected":[]}', "hermes": '[]'})
    response = json.dumps({"selected": [], "body": "한국어 근거 " * 1500}, ensure_ascii=False)
    if winner:
        results[winner] = response
    assert wiki_source_compiler.structured_llm_call("한국어 원문 JSON") == (response if winner else None)
    names = [call[0] for call in calls]
    order = ["codex", "openrouter", "hermes"]
    assert names == (order[:order.index(winner) + 1] if winner else order)
    for name, prompt, args, kwargs in calls:
        assert prompt == "한국어 원문 JSON"
        assert kwargs["structured_output"] is True
        if name == "openrouter":
            assert args == ("anthropic/claude-sonnet-4.5",)
            assert kwargs["max_tokens"] == 8192
            assert 0 < kwargs["timeout"] <= 60
        else:
            assert kwargs["search"] is False
            assert kwargs["reasoning_effort"] == "medium"
            assert 0 < kwargs["max_timeout"] <= 60


def test_exceptions_continue_without_exposing_private_source(providers, caplog):
    calls, results = providers
    results.update({"codex": RuntimeError("PRIVATE source text"),
                    "openrouter": RuntimeError("PRIVATE key"), "hermes": '{"claims":[]}'})
    assert llm_cli.structured_chat("PRIVATE source text") == '{"claims":[]}'
    assert len(calls) == 3
    assert "PRIVATE" not in caplog.text


def test_disabled_global_gate_does_not_invoke_providers(monkeypatch, providers):
    calls, _ = providers
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "0")
    assert llm_cli.structured_chat("source") is None
    assert not calls


def test_disabled_direct_provider_and_custom_model(monkeypatch, providers):
    calls, results = providers
    monkeypatch.setenv("LLM_DIRECT_OPENROUTER", "false")
    results["hermes"] = '{"selected":[]}'
    assert llm_cli.structured_chat("source") == results["hermes"]
    assert [call[0] for call in calls] == ["codex", "hermes"]
    calls.clear()
    monkeypatch.setenv("LLM_DIRECT_OPENROUTER", "true")
    monkeypatch.setenv("WIKI_STRUCTURED_OPENROUTER_MODEL", "approved/capable-model")
    results["openrouter"] = '{"claims":[]}'
    assert llm_cli.structured_chat("source") == results["openrouter"]
    assert calls[-1][2] == ("approved/capable-model",)


@pytest.mark.parametrize("budget", [0, -1])
def test_zero_budget_does_not_invoke_providers(providers, budget):
    calls, _ = providers
    assert llm_cli.structured_chat("source", max_timeout=budget) is None
    assert not calls


def test_shared_budget_expires_without_starting_more_providers(monkeypatch, providers):
    calls, _ = providers
    clock = iter([0, 0, 0, 31, 31])
    monkeypatch.setattr(llm_cli.time, "monotonic", lambda: next(clock))
    assert llm_cli.structured_chat("source", max_timeout=30) is None
    assert [call[0] for call in calls] == ["codex"]
    assert calls[0][3]["max_timeout"] == 30
    assert calls[0][3]["deadline"] == 30


def test_total_budget_capped_and_each_stage_uses_remaining_time(monkeypatch, providers):
    calls, _ = providers
    clock = iter([0, 0, 0, 60, 60, 60, 120, 120, 120, 180])
    monkeypatch.setattr(llm_cli.time, "monotonic", lambda: next(clock))
    assert llm_cli.structured_chat("source", max_timeout=9999) is None
    assert [call[0] for call in calls] == ["codex", "openrouter", "hermes"]
    assert calls[0][3]["deadline"] == 60
    assert calls[1][3]["timeout"] == 60
    assert calls[2][3]["deadline"] == 180


def test_callback_forwards_budget(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_cli, "structured_chat", lambda prompt, **kwargs: seen.append((prompt, kwargs)) or '{"ok":true}')
    assert wiki_source_compiler.structured_llm_call("source", max_timeout=27) == '{"ok":true}'
    assert seen == [("source", {"max_timeout": 27})]


@pytest.mark.parametrize("configured", ["codxe", "codex,codxe", "hermes, openai", "codex;hermes"])
def test_unknown_provider_fails_closed_without_sending_the_prompt(monkeypatch, providers, configured):
    """D-12: 오타 제공자를 Hermes 로 조용히 보내지 않는다 — 어떤 제공자에도 프롬프트 미전달."""
    calls, results = providers
    results.update({"codex": '{"ok":true}', "openrouter": '{"ok":true}', "hermes": '{"ok":true}'})
    monkeypatch.setenv("WIKI_STRUCTURED_PROVIDERS", configured)
    assert llm_cli.structured_chat("source") is None
    assert calls == []


def test_provider_names_are_normalized_and_deduplicated(monkeypatch, providers):
    calls, results = providers
    results["hermes"] = '{"ok":true}'
    monkeypatch.setenv("WIKI_STRUCTURED_PROVIDERS", " Codex ,HERMES,codex, ")
    assert llm_cli.structured_chat("source") == '{"ok":true}'
    assert [call[0] for call in calls] == ["codex", "hermes"]


@pytest.mark.parametrize("configured", ["", " , "])
def test_empty_provider_list_calls_nothing(monkeypatch, providers, configured):
    calls, _ = providers
    monkeypatch.setenv("WIKI_STRUCTURED_PROVIDERS", configured)
    assert llm_cli.structured_chat("source") is None
    assert calls == []
