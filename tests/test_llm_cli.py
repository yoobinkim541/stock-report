#!/usr/bin/env python3
"""test_llm_cli.py — LLM 백업 체인 (hermes 1차 → agy 2차·opt-in) 무네트워크."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

from lib import llm_cli as L


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def test_backup_off_by_default(monkeypatch):
    monkeypatch.delenv("LLM_BACKUP_ENABLED", raising=False)
    text, note = L.backup_chat("질문", runner=lambda *a, **k: FakeCompleted(stdout="x"))
    assert text is None and note == "backup off"          # 기본 off — 기존 동작 불변


def test_backup_uses_agy_print_in_scratch_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw))
        return FakeCompleted(stdout="백업 답변")

    text, note = L.backup_chat("질문입니다", timeout=90, runner=runner)
    assert text == "백업 답변" and note == "backup:agy"
    cmd, kw = calls[0]
    assert cmd[:2] == ["agy", "--print"] and cmd[2] == "질문입니다"
    assert "--print-timeout" in cmd and "90s" in cmd
    # 에이전트 CLI 안전핀: 레포가 아닌 빈 스크래치 cwd
    import os as _os
    assert kw["cwd"] != _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    assert "llm-backup-scratch" in kw["cwd"]


def test_backup_failures_graceful(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")
    t, n = L.backup_chat("q", runner=lambda *a, **k: FakeCompleted(returncode=1, stderr="err"))
    assert t is None and "비정상" in n
    t, n = L.backup_chat("q", runner=lambda *a, **k: FakeCompleted(stdout="  "))
    assert t is None and "빈 출력" in n

    def missing(*a, **k):
        raise FileNotFoundError("agy")

    t, n = L.backup_chat("q", runner=missing)
    assert t is None and "미설치" in n


def test_news_spike_llm_backup_chain(monkeypatch):
    """hermes 실패 → agy 백업이 판정 제공 (형식 검증은 동일 파서)."""
    import news_spike_detector as N
    monkeypatch.setattr(N, "NEWS_LLM_ENABLED", True)
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")

    def runner(cmd, **kw):
        if cmd[0] == "hermes":
            return FakeCompleted(returncode=1, stderr="hermes down")
        assert cmd[0] == "agy"
        return FakeCompleted(stdout="8|백업 판정 근거")

    score, reason, used = N.judge_importance(
        {"title": "어느 기업 신제품 발표", "tags": ["속보"]}, allow_llm=True, runner=runner)
    assert used is True and score == 8 and "LLM 판정" in reason


def test_advisor_backup_answer_no_file_tools(monkeypatch):
    """hermes 실패 → agy 백업 답변 채택 + '파일 편집 미지원' 정직 표기."""
    import stock_advisor as sa
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")
    monkeypatch.setattr(sa, "build_ml_context", lambda: "[ML]")

    def runner(cmd, **kw):
        if cmd[0] == "hermes":
            raise RuntimeError("hermes timeout")
        return FakeCompleted(stdout="백업 상담 답변")

    market = {"portfolio": {}, "benchmarks": {}, "qqq": {}}
    answer = sa.ask_portfolio_advisor("점검해줘", market, runner=runner)
    assert "백업 상담 답변" in answer
    assert "파일 편집 기능은 이 모드에서 미지원" in answer


def test_overlay_backup_still_fact_guarded(monkeypatch):
    """overlay 백업 출력도 fact guard 를 통과해야 채택 — 환각 숫자면 폐기."""
    from investment_report import _generate_llm_overlay
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")
    clean = {"date": "2026-07-14", "market_summary": {"qqq": 600.0}}

    def runner_bad(cmd, **kw):
        if cmd[0] == "hermes":
            return FakeCompleted(returncode=1)
        return FakeCompleted(stdout="- QQQ 가 999999 까지 간다")   # 입력에 없는 숫자

    text, status = _generate_llm_overlay(clean, "", runner=runner_bad)
    assert text is None and "fact guard rejected backup" in status

    def runner_ok(cmd, **kw):
        if cmd[0] == "hermes":
            return FakeCompleted(returncode=1)
        return FakeCompleted(stdout="- 시장 요약: QQQ 600 유지 관찰")

    text, status = _generate_llm_overlay(clean, "", runner=runner_ok)
    assert text and status.startswith("ok (backup:")


def test_status_reports_missing_binaries():
    st = L.status()
    assert "backup_enabled" in st and st["backup_cli"] == "agy"
    assert "hermes" in st                                  # 샌드박스: 미설치/오류 문자열


# ── 공용 호출 계약: 예산·비밀·프로세스 그룹 (리뷰 D-04/D-07/D-08) ─────────────────

import subprocess  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

SECRET = "D_FIX_FAKE_TOKEN_7a93"


def test_backup_does_not_stretch_a_small_remaining_budget(monkeypatch):
    """남은 예산 1초를 30초 최소값·+30초 여유로 늘리면 상위 deadline 을 넘긴다."""
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw))
        return FakeCompleted(stdout="답")

    assert L.backup_chat("질문", timeout=1, runner=runner)[0] == "답"
    cmd, kw = calls[0]
    assert kw["timeout"] == 1
    assert cmd[cmd.index("--print-timeout") + 1] == "1s"
    calls.clear()
    assert L.backup_chat("질문", timeout=0.4, runner=runner) == (None, "backup 예산 없음")
    assert calls == []                                     # 예산이 없으면 시작하지 않음


def test_backup_failure_notes_do_not_echo_prompt_or_stderr(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")

    def timeout_runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)            # str 에 명령 인자(=프롬프트) 포함

    text, note = L.backup_chat(f"프롬프트 {SECRET}", runner=timeout_runner)
    assert text is None and SECRET not in note and "TimeoutExpired" in note
    text, note = L.backup_chat("q", runner=lambda *a, **k: FakeCompleted(
        returncode=2, stderr=f"echo: {SECRET}"))
    assert text is None and SECRET not in note and "rc=2" in note


def test_chat_once_backup_uses_the_remaining_budget(monkeypatch):
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")
    clock = {"t": 100.0}
    monkeypatch.setattr(L.time, "monotonic", lambda: clock["t"])
    seen = []

    def runner(cmd, **kw):
        if cmd[0] == "hermes":
            clock["t"] += 25                                # 1차가 예산 대부분을 씀
            return FakeCompleted(returncode=1)
        seen.append(kw["timeout"])
        return FakeCompleted(stdout="백업")

    assert L.chat_once("질문", timeout=30, runner=runner) == ("백업", "backup:agy")
    assert seen == [5]


_TREE_SCRIPT = """#!{python}
import os, signal, subprocess, sys, time
ignore = os.environ.get("D_TREE_MODE") == "ignore"
if ignore:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child_code = ("import signal, time\\n"
              + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n" if ignore else "")
              + "time.sleep(60)\\n")
# 후손은 부모의 stdout/stderr 파이프를 상속한다(파이프를 쥔 채 남는 손자 재현).
child = subprocess.Popen([sys.executable, "-c", child_code])
with open(os.environ["D_TREE_PID_FILE"], "w") as fh:
    fh.write(str(child.pid))
time.sleep(60)
"""


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="프로세스 그룹/proc 은 리눅스 전용")
@pytest.mark.parametrize("mode", ["normal", "ignore"])
def test_backup_timeout_terminates_the_whole_cli_process_group(monkeypatch, tmp_path, mode):
    """D-08: 백업 CLI timeout 시 CLI 가 띄운 손자(파이프 상속·TERM 무시 포함)까지 정리된다."""
    script = tmp_path / "fake-agy"
    script.write_text(_TREE_SCRIPT.format(python=sys.executable), encoding="utf-8")
    script.chmod(0o755)
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("LLM_BACKUP_ENABLED", "true")
    monkeypatch.setenv("D_TREE_PID_FILE", str(pid_file))
    monkeypatch.setenv("D_TREE_MODE", mode)
    monkeypatch.setattr(L, "BACKUP_CLI", str(script))
    monkeypatch.setattr(L, "PROCESS_GROUP_GRACE", 0.5, raising=False)

    started = time.monotonic()
    text, note = L.backup_chat(f"프롬프트 {SECRET}", timeout=1.5)
    elapsed = time.monotonic() - started

    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = _alive(child_pid)
    if alive:
        os.kill(child_pid, 9)
    assert not alive, "CLI 의 후손 프로세스가 timeout 뒤에도 살아 있음"
    assert elapsed < 10                                    # 예산(1.5s)+정리 유예 안에서 반환
    assert text is None and "TimeoutExpired" in note and SECRET not in note


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="프로세스 그룹은 리눅스 전용")
def test_run_cli_timeout_exception_does_not_carry_prompt():
    with pytest.raises(subprocess.TimeoutExpired) as info:
        L.run_cli([sys.executable, "-c", "import time; time.sleep(30)", SECRET],
                  capture_output=True, text=True, timeout=0.3)
    assert SECRET not in str(info.value)
