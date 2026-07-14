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
