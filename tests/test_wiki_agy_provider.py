"""Server-local Antigravity (agy) structured provider: model fallback, quota skip, isolation."""
import subprocess

import pytest

from lib import llm_cli

QUOTA = ("error: Individual quota reached. Please upgrade your subscription. Resets in 50m\n"
         "AGY_ERROR: {\"status\":\"RESOURCE_EXHAUSTED\",\"error_code\":429}")


class Runner:
    def __init__(self, outcomes):
        self.outcomes, self.calls = dict(outcomes), []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        model = cmd[cmd.index("--model") + 1]
        outcome = self.outcomes.get(model, ("", "boom", 1))
        if isinstance(outcome, Exception):
            raise outcome
        out, err, code = outcome
        return subprocess.CompletedProcess(cmd, code, out, err)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_AGY_MODELS", "m-high,m-medium,m-argon")
    monkeypatch.setenv("WIKI_AGY_BIN", "/usr/bin/agy")
    monkeypatch.setenv("WIKI_AGY_SCRATCH", str(tmp_path / "scratch"))
    llm_cli._AGY_COOLDOWN.clear()


def test_first_available_model_answers_in_isolated_scratch_dir(tmp_path):
    runner = Runner({"m-high": ('{"ok": true}', "", 0)})
    assert llm_cli.agy_structured_call("p", timeout=30, runner=runner) == '{"ok": true}'
    cmd, kwargs = runner.calls[0]
    assert cmd[0] == "/usr/bin/agy" and cmd[cmd.index("--model") + 1] == "m-high"
    assert cmd[-2:] == ["-p", "p"]
    assert kwargs["cwd"] == str(tmp_path / "scratch") and (tmp_path / "scratch").is_dir()


def test_untrusted_source_text_never_gets_auto_approved_tools():
    runner = Runner({"m-high": ('{"ok": true}', "", 0)})
    llm_cli.agy_structured_call("p", timeout=30, runner=runner)
    cmd = runner.calls[0][0]
    assert "--dangerously-skip-permissions" not in cmd
    assert "--sandbox" in cmd and "--disable-slash-commands" in cmd


def test_quota_exhausted_model_is_skipped_and_remembered():
    runner = Runner({"m-high": ("", QUOTA, 3), "m-medium": ('{"n": 1}', "", 0)})
    assert llm_cli.agy_structured_call("p", timeout=30, runner=runner) == '{"n": 1}'
    assert [c[0][c[0].index("--model") + 1] for c in runner.calls] == ["m-high", "m-medium"]
    runner.calls.clear()
    assert llm_cli.agy_structured_call("p", timeout=30, runner=runner) == '{"n": 1}'
    assert [c[0][c[0].index("--model") + 1] for c in runner.calls] == ["m-medium"]


def test_empty_output_timeout_and_missing_binary_fall_through_to_none():
    runner = Runner({"m-high": ("  \n", "", 0), "m-medium": subprocess.TimeoutExpired("agy", 1),
                     "m-argon": FileNotFoundError()})
    assert llm_cli.agy_structured_call("p", timeout=30, runner=runner) is None
    assert len(runner.calls) == 3


def test_all_models_exhausted_makes_no_calls_until_cooldown_expires(monkeypatch):
    runner = Runner({m: ("", QUOTA, 3) for m in ("m-high", "m-medium", "m-argon")})
    assert llm_cli.agy_structured_call("p", timeout=30, runner=runner) is None
    runner.calls.clear()
    assert llm_cli.agy_structured_call("p", timeout=30, runner=runner) is None
    assert runner.calls == []
    monkeypatch.setattr(llm_cli.time, "monotonic", lambda: 10 ** 9)
    runner.outcomes["m-high"] = ('{"back": 1}', "", 0)
    assert llm_cli.agy_structured_call("p", timeout=30, runner=runner) == '{"back": 1}'


def test_structured_chat_uses_agy_stage_and_validates_json(monkeypatch):
    monkeypatch.setenv("WIKI_STRUCTURED_PROVIDERS", "agy")
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "1")
    answers = iter(['prose {"x": 1}', '```json\n{"x": 2}\n```'])
    monkeypatch.setattr(llm_cli, "agy_structured_call", lambda prompt, **kw: next(answers))
    assert llm_cli.structured_chat("p") is None
    assert llm_cli.structured_chat("p").strip().startswith("```json")


def test_default_provider_order_is_unchanged(monkeypatch):
    monkeypatch.delenv("WIKI_STRUCTURED_PROVIDERS", raising=False)
    monkeypatch.setenv("AGENT_CONSOLE_LLM_ENABLED", "1")
    assert llm_cli.DEFAULT_STRUCTURED_PROVIDERS == ("codex", "openrouter", "hermes")
