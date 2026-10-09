#!/usr/bin/env python3
"""test_llm_direct.py — openrouter hermes 명령의 직접 호출 전환 (무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import llm_direct as D

CMD = ["hermes", "chat", "-q", "프롬프트", "--provider", "openrouter",
       "--model", "deepseek/deepseek-v4-flash-0731", "-Q"]


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


def ok_post(calls):
    def post(url, json=None, timeout=None, headers=None):
        calls.append((url, json, timeout, headers))
        return FakeResp({"choices": [{"message": {"content": ' {"score": 7} '}}]})
    return post


def must_not_fallback(*a, **k):
    raise AssertionError("fallback이 호출되면 안 됨")


def test_parse_only_openrouter_hermes_chat():
    assert D.parse_openrouter_chat(CMD) == ("프롬프트", "deepseek/deepseek-v4-flash-0731")
    codex = [c if c != "openrouter" else "openai-codex" for c in CMD]
    assert D.parse_openrouter_chat(codex) is None
    assert D.parse_openrouter_chat(CMD + ["--image", "a.png"]) is None
    assert D.parse_openrouter_chat(["agy", "--print", "x"]) is None
    assert D.parse_openrouter_chat("hermes chat") is None


def test_direct_call_returns_completed_process(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("LLM_DIRECT_OPENROUTER", raising=False)
    calls = []
    r = D.run(CMD, capture_output=True, text=True, timeout=33, post=ok_post(calls), fallback=must_not_fallback)
    assert r.returncode == 0 and r.stdout == '{"score": 7}\n'
    url, body, timeout, headers = calls[0]
    assert body["model"] == "deepseek/deepseek-v4-flash-0731"
    assert body["messages"] == [{"role": "user", "content": "프롬프트"}]
    assert body["reasoning"] == {"enabled": False}
    assert timeout == 33 and headers["Authorization"] == "Bearer k"


def test_non_openrouter_command_passes_through(monkeypatch):
    seen = []
    codex = [c if c != "openrouter" else "openai-codex" for c in CMD]
    D.run(codex, timeout=5, fallback=lambda cmd, *a, **k: seen.append((cmd, k)) or "orig")
    assert seen == [(codex, {"timeout": 5})]


def test_failure_falls_back_to_hermes(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    seen = []

    def bad_post(*a, **k):
        return FakeResp({}, status=402)

    out = D.run(CMD, timeout=5, post=bad_post, fallback=lambda cmd, *a, **k: seen.append(cmd) or "hermes")
    assert out == "hermes" and seen == [CMD]


def test_missing_key_or_disabled_uses_hermes(monkeypatch):
    seen = []
    fb = lambda cmd, *a, **k: seen.append(cmd) or "hermes"  # noqa: E731
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert D.run(CMD, post=ok_post([]), fallback=fb) == "hermes"
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_DIRECT_OPENROUTER", "false")
    assert D.run(CMD, post=ok_post([]), fallback=fb) == "hermes"
    assert len(seen) == 2


def test_empty_content_falls_back(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    empty = lambda *a, **k: FakeResp({"choices": [{"message": {"content": "  "}}]})  # noqa: E731
    assert D.run(CMD, post=empty, fallback=lambda *a, **k: "hermes") == "hermes"
