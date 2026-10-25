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


AGY_CMD = ["hermes", "chat", "-q", "프롬프트", "--provider", "agy",
           "--model", "gemini-3.8-flash-medium", "-Q"]


def fake_agy(calls, rc=0, out=" 답 \n", err=""):
    def runner(cmd, **kw):
        calls.append((cmd, kw))
        return D.subprocess.CompletedProcess(cmd, rc, out, err)
    return runner


def test_parse_agy_chat():
    assert D.parse_agy_chat(AGY_CMD) == ("프롬프트", "gemini-3.8-flash-medium")
    assert D.parse_agy_chat(CMD) is None
    assert D.parse_openrouter_chat(AGY_CMD) is None


def test_agy_direct_call(monkeypatch):
    monkeypatch.delenv("LLM_DIRECT_AGY", raising=False)
    calls = []
    r = D.run(AGY_CMD, timeout=40, agy_runner=fake_agy(calls), fallback=must_not_fallback)
    assert r.returncode == 0 and r.stdout == "답\n"
    cmd, kw = calls[0]
    assert cmd[:2] == ["agy", "--print"] and cmd[2] == "프롬프트"
    assert cmd[cmd.index("--model") + 1] == "gemini-3.8-flash-medium"
    assert cmd[cmd.index("--print-timeout") + 1] == "40s"
    assert kw["cwd"].endswith("llm-backup-scratch")


def test_agy_failure_falls_back_to_hermes(monkeypatch):
    monkeypatch.delenv("LLM_DIRECT_AGY", raising=False)
    seen = []
    fb = lambda cmd, *a, **k: seen.append(cmd) or "hermes"  # noqa: E731
    assert D.run(AGY_CMD, agy_runner=fake_agy([], rc=1, err="x"), fallback=fb) == "hermes"
    assert D.run(AGY_CMD, agy_runner=fake_agy([], out="  "), fallback=fb) == "hermes"
    monkeypatch.setenv("LLM_DIRECT_AGY", "false")
    assert D.run(AGY_CMD, agy_runner=fake_agy([]), fallback=fb) == "hermes"
    assert seen == [AGY_CMD] * 3


def test_structured_direct_requests_json_capability_with_explicit_budget(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_DIRECT_MAX_TOKENS", "999999")
    calls = []
    assert D.direct_chat("source", "capable/model", timeout=17, post=ok_post(calls),
                         structured_output=True, max_tokens=8192) == '{"score": 7}'
    _, body, timeout, _ = calls[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["provider"] == {"require_parameters": True}
    assert body["max_tokens"] == 8192 and timeout == 17


def test_structured_direct_rejects_invalid_and_truncated_outputs(monkeypatch):
    import pytest

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    for content, reason in [
        ('{"x":1,"x":2}', "stop"), ('{"x":NaN}', "stop"),
        ('{"x":1e999}', "stop"), ('prose {"x":1}', "stop"),
        ("[]", "stop"), ('{"x":1}', "length"),
        ('{"x":"' + "a" * 100001 + '"}', "stop"),
    ]:
        def post(*args, **kwargs):
            return FakeResp({"choices": [{"message": {"content": content}, "finish_reason": reason}]})
        with pytest.raises(ValueError, match="structured_response_invalid"):
            D.direct_chat("source", "capable/model", post=post, structured_output=True)


def test_plain_direct_keeps_existing_request_contract(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_DIRECT_MAX_TOKENS", "4096")
    calls = []
    D.direct_chat("source", "existing/model", post=ok_post(calls))
    body = calls[0][1]
    assert "response_format" not in body and "provider" not in body
    assert body["max_tokens"] == 4096


# ── 단일 예산·호출별 토큰 상한·비밀 없는 로그 (리뷰 D-04/D-06/D-07) ────────────────

import logging  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

SECRET = "D_FIX_FAKE_TOKEN_7a93"


def _clocked(monkeypatch, start=1000.0):
    clock = {"t": start}
    monkeypatch.setattr(D.time, "monotonic", lambda: clock["t"])
    return clock


def test_hermes_fallback_gets_only_the_remaining_budget(monkeypatch):
    """D-07: 직접 호출이 timeout 대부분을 쓴 뒤 hermes 폴백이 timeout 전체를 다시 받으면 안 된다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    clock = _clocked(monkeypatch)
    seen = []

    def slow_failure(*args, timeout=None, **kwargs):
        seen.append(("http", timeout))
        clock["t"] += 4
        raise TimeoutError("read timed out")

    D.run(CMD, timeout=5, post=slow_failure,
          fallback=lambda cmd, *a, **k: seen.append(("fallback", k["timeout"])) or "hermes")
    assert seen == [("http", 5), ("fallback", 1)]


def test_exhausted_budget_does_not_start_the_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    clock = _clocked(monkeypatch)

    def slow_failure(*args, **kwargs):
        clock["t"] += 4.5
        raise TimeoutError("read timed out")

    with pytest.raises(subprocess.TimeoutExpired) as info:
        D.run(CMD, timeout=5, post=slow_failure, fallback=must_not_fallback)
    assert "프롬프트" not in str(info.value)


def test_agy_keeps_a_small_budget_small():
    """D-07: agy 에 1초를 주면 프로세스 상한도 1초 — 60초(30 최소값+30 여유)로 늘지 않는다."""
    calls = []
    D.agy_chat("프롬프트", "gemini-3.8-flash-medium", timeout=1, runner=fake_agy(calls))
    cmd, kw = calls[0]
    assert kw["timeout"] == 1
    assert cmd[cmd.index("--print-timeout") + 1] == "1s"


def test_runner_forwards_call_specific_max_tokens_to_the_api(monkeypatch):
    """D-06: 호출부 상한이 API body 까지 가고, hermes 폴백 명령에는 새지 않는다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_DIRECT_MAX_TOKENS", "4096")
    calls = []
    D.run(CMD, timeout=9, post=ok_post(calls), fallback=must_not_fallback, max_tokens=1536)
    assert calls[0][1]["max_tokens"] == 1536
    seen = []
    D.run(CMD, timeout=9, post=lambda *a, **k: FakeResp({}, status=402), max_tokens=512,
          fallback=lambda cmd, *a, **k: seen.append(k) or "hermes")
    assert "max_tokens" not in seen[0]


def test_direct_failure_logs_carry_no_prompt_or_error_body(monkeypatch, caplog):
    """D-04: 실패 로그에는 경로·예외 종류·소요시간만 — 프롬프트/응답 오류 본문 금지."""
    caplog.set_level(logging.INFO, logger=D.logger.name)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    secret_cmd = [c if c != "프롬프트" else f"프롬프트 {SECRET}" for c in CMD]

    def leaky_post(*args, **kwargs):
        raise RuntimeError(f"upstream echoed {SECRET}")

    D.run(secret_cmd, timeout=5, post=leaky_post, fallback=lambda *a, **k: "hermes")

    def agy_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)             # str 에 프롬프트 포함

    secret_agy = [c if c != "프롬프트" else f"프롬프트 {SECRET}" for c in AGY_CMD]
    D.run(secret_agy, timeout=5, agy_runner=agy_timeout, fallback=lambda *a, **k: "hermes")
    assert "직접 호출 실패" in caplog.text
    assert SECRET not in caplog.text
