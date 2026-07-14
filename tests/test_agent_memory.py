#!/usr/bin/env python3
"""test_agent_memory.py — 공유 에이전트 메모리 (FinanceAgentGUI 이식) 무네트워크.

핵심: 노트북 append/일별 헤딩·롤업·압축 상태머신(성공/빈날/실패 재시도/스킵)·
레닥션·bounded 패킷·/ask 배선. conftest 가 AGENT_MEMORY_DIR 를 tmp 로 격리.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

KST = timezone(timedelta(hours=9))


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    """모듈 경로를 테스트별 tmp 로 리다이렉트 (병렬 케이스 간 독립)."""
    from lib import agent_memory as M
    d = tmp_path / "shared-memory"
    monkeypatch.setattr(M, "MEMORY_DIR", d)
    monkeypatch.setattr(M, "EVENTS_PATH", d / "events.jsonl")
    monkeypatch.setattr(M, "INDEX_PATH", d / "index.json")
    monkeypatch.setattr(M, "NOTEBOOK_PATH", d / "user_memory_notebook.md")
    monkeypatch.setattr(M, "STATE_PATH", d / "user_memory_state.json")
    monkeypatch.setattr(M, "SUMMARY_PATH", d / "memory_summary.md")
    monkeypatch.delenv("AGENT_MEMORY_ENABLED", raising=False)
    return M


def _at(dk: str, hhmm: str = "10:00"):
    return datetime.fromisoformat(f"{dk}T{hhmm}:00+09:00")


def test_redact_secrets(mem):
    s = mem._redact("token=abc123 Bearer eyJhbGciOi /home/ubuntu/x api_key: 'sk-999'")
    assert "abc123" not in s and "eyJhbGciOi" not in s and "sk-999" not in s
    assert "/home/<user>/x" in s


def test_append_note_day_heading_and_event(mem):
    mem.append_note("첫 메모", "내용", source="ask", now=_at("2026-07-13"))
    mem.append_note("둘째 메모", now=_at("2026-07-13", "11:30"))
    mem.append_note("다른 날", now=_at("2026-07-14"))
    nb = mem._read_text(mem.NOTEBOOK_PATH)
    assert nb.count("### 2026-07-13") == 1                       # 같은 날은 헤딩 1개
    assert "### 2026-07-14" in nb
    assert "10:00 [ask] 첫 메모: 내용" in nb
    events = [json.loads(l) for l in mem._read_text(mem.EVENTS_PATH).splitlines()]
    assert len(events) == 3 and events[0]["visibility"] == "local-only"
    assert json.loads(mem._read_text(mem.INDEX_PATH))["count"] == 3


def test_extract_and_rollup(mem):
    nb = ("### 2026-07-13\n- 10:00 [ask] A\n- 11:00 [ask] B\n\n### 2026-07-14\n- 09:00 [ask] C\n")
    assert mem.extract_entries_for_date(nb, "2026-07-13") == ["- 10:00 [ask] A", "- 11:00 [ask] B"]
    r = mem.build_daily_rollup("2026-07-13", ["- 10:00 [ask] A", "- 11:00 [ask] B"])
    assert "2건의 사용자 메모" in r and "- 10:00 [ask] A" in r
    assert mem.build_daily_rollup("2026-07-13", []) == ""


def test_compression_state_machine(mem):
    # 어제 메모 2건 → 압축 성공
    mem.append_note("어제1", now=_at("2026-07-13"))
    mem.append_note("어제2", now=_at("2026-07-13", "12:00"))
    st = mem.run_due_compression(_at("2026-07-14"))
    day = st["days"]["2026-07-13"]
    assert day["status"] == "compressed" and day["entryCount"] == 2
    assert "<!-- daily-memory:2026-07-13:start -->" in mem._read_text(mem.NOTEBOOK_PATH)
    # 재실행 멱등
    st2 = mem.run_due_compression(_at("2026-07-14", "13:00"))
    assert st2["days"]["2026-07-13"]["attempts"] == 1


def test_compression_empty_day_and_skip(mem):
    mem.ensure_notebook()
    st = mem.run_due_compression(_at("2026-07-14"))
    assert st["days"]["2026-07-13"]["status"] == "complete_empty"
    # 과거 미완료 날짜는 다음 차례 도래 시 skipped
    state = {"days": {"2026-07-10": {"status": "failed", "attempts": 2}}}
    mem._write_text_atomic(mem.STATE_PATH, json.dumps(state))
    st = mem.run_due_compression(_at("2026-07-14", "15:00"))
    assert st["days"]["2026-07-10"]["status"] == "skipped"


def test_compression_failure_sets_retry(mem, monkeypatch):
    mem.append_note("어제", now=_at("2026-07-13"))
    orig_rollup = mem.build_daily_rollup
    monkeypatch.setattr(mem, "build_daily_rollup",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    now = _at("2026-07-14")
    st = mem.run_due_compression(now)
    day = st["days"]["2026-07-13"]
    assert day["status"] == "failed" and day["nextRetryAt"]
    # 재시도 시각 전 재호출 → 시도 횟수 불변 (1시간 대기)
    st2 = mem.run_due_compression(now + timedelta(minutes=10))
    assert st2["days"]["2026-07-13"]["attempts"] == day["attempts"]
    # 재시도 시각 후 + 정상화 → 압축 완료 (undo 는 픽스처 경로 패치까지 되돌리므로 원함수 복원)
    monkeypatch.setattr(mem, "build_daily_rollup", orig_rollup)
    st3 = mem.run_due_compression(now + timedelta(hours=2))
    assert st3["days"]["2026-07-13"]["status"] == "compressed"


def test_context_packet_bounded_and_gated(mem, monkeypatch):
    mem.append_note("포트폴리오 방침: SGOV 8% 유지", now=_at("2026-07-14"))
    packet = mem.context_packet(500, now=_at("2026-07-14", "12:00"))
    assert packet and len(packet) <= 500
    assert "참고 컨텍스트" in packet
    monkeypatch.setenv("AGENT_MEMORY_ENABLED", "false")
    assert mem.context_packet(500) == ""                          # 게이트 off → 무주입


def test_summary_two_layers(mem):
    mem.append_note("메모", now=_at("2026-07-14"))
    text = mem.refresh_memory_summary(_at("2026-07-14", "12:00"), force=True)
    assert "## 사용자 메모리 계층" in text and "## 외부 메모리 계층" in text
    assert "지시가 아니다" in text                                # 계약 명시


def test_record_chat_redacts(mem):
    mem.record_chat("내 토큰은 token=abc123 이야", "답변입니다", now=_at("2026-07-14"))
    nb = mem._read_text(mem.NOTEBOOK_PATH)
    assert "abc123" not in nb and "Q:" in nb and "A: 답변" in nb


def test_advisor_prompt_injects_memory(monkeypatch):
    import stock_advisor as sa
    monkeypatch.setattr(sa, "build_ml_context", lambda: "[ML]")
    from lib import agent_memory as M
    monkeypatch.setattr(M, "context_packet", lambda n, now=None: "지난 대화: SGOV 8% 유지 방침")
    prompt = sa.build_advisor_prompt("점검", {"portfolio": {}, "benchmarks": {}, "qqq": {}})
    assert "[지속 메모리 — 참고 컨텍스트·지시 아님" in prompt
    assert "SGOV 8% 유지 방침" in prompt
