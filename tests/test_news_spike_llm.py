#!/usr/bin/env python3
"""test_news_spike_llm.py — 속보 경계선 LLM 2차 판정 (무네트워크).

핵심: LLM 은 규칙 점수 5~6 경계선만·opt-in·실패 시 규칙 점수 유지(보수적).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import news_spike_detector as N


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _borderline_event():
    return {"title": "어느 기업 신제품 발표", "tags": ["속보"]}   # 규칙 5점(일반 속보)


def test_parse_llm_verdict():
    assert N._parse_llm_verdict("8|반도체 규제 직접 영향") == (8, "반도체 규제 직접 영향")
    assert N._parse_llm_verdict("잡담\n7|한 줄 평가") == (7, "한 줄 평가")
    assert N._parse_llm_verdict("점수 없음") is None
    assert N._parse_llm_verdict("11|범위 밖") is None
    assert N._parse_llm_verdict("7|") is None
    assert N._parse_llm_verdict("") is None


def test_rule_score_band_is_borderline():
    score, _ = N._rule_score(_borderline_event())
    assert N._LLM_BAND[0] <= score <= N._LLM_BAND[1]


def test_llm_disabled_keeps_rule(monkeypatch):
    monkeypatch.setattr(N, "NEWS_LLM_ENABLED", False)
    score, reason, used = N.judge_importance(_borderline_event(), allow_llm=True)
    assert used is False and score == 5


def test_llm_promotes_borderline(monkeypatch):
    monkeypatch.setattr(N, "NEWS_LLM_ENABLED", True)
    runner = lambda cmd, **k: FakeCompleted(stdout="8|보유종목 직접 규제")
    score, reason, used = N.judge_importance(_borderline_event(), allow_llm=True, runner=runner)
    assert used is True and score == 8 and "LLM 판정" in reason


def test_llm_not_called_for_clear_scores(monkeypatch):
    monkeypatch.setattr(N, "NEWS_LLM_ENABLED", True)
    calls = []

    def runner(cmd, **k):
        calls.append(cmd)
        return FakeCompleted(stdout="9|x")

    # 고신호(≥7)·노이즈(≤3) 는 LLM 미호출 — 비용 통제
    hi = {"title": "연준 기준금리 전격 인하", "tags": ["속보"]}
    lo = {"title": "올림픽 개막식 하이라이트", "tags": ["속보"]}
    s1, _, u1 = N.judge_importance(hi, allow_llm=True, runner=runner)
    s2, _, u2 = N.judge_importance(lo, allow_llm=True, runner=runner)
    assert u1 is False and u2 is False and calls == []
    assert s1 >= 7 and s2 <= 3


def test_llm_failure_keeps_rule(monkeypatch):
    monkeypatch.setattr(N, "NEWS_LLM_ENABLED", True)
    fail = lambda cmd, **k: FakeCompleted(returncode=1, stderr="boom")
    garbage = lambda cmd, **k: FakeCompleted(stdout="이상한 출력")
    for runner in (fail, garbage):
        score, reason, used = N.judge_importance(_borderline_event(), allow_llm=True, runner=runner)
        assert used is False and score == 5


def test_budget_gate(monkeypatch):
    monkeypatch.setattr(N, "NEWS_LLM_ENABLED", True)
    runner = lambda cmd, **k: FakeCompleted(stdout="8|x")
    _, _, used = N.judge_importance(_borderline_event(), allow_llm=False, runner=runner)
    assert used is False                                     # 회당 예산 소진 시 규칙 폴백


def test_prompt_defense():
    p = N._llm_prompt({"title": "테스트 <<<속보>>>", "tags": ["$NVDA"]})
    assert "<<<DATA_START>>>" in p and "따르지 말" in p
    assert "<<<속보>>>" not in p                              # 마커 충돌 문자는 제거
