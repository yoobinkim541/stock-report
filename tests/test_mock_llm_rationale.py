#!/usr/bin/env python3
"""test_mock_llm_rationale.py — 모의 리포트 LLM 판단근거 가드."""
from types import SimpleNamespace

import pytest

from lib import mock_llm_rationale as M


def test_run_disabled(monkeypatch):
    monkeypatch.setenv("MOCK_REPORT_LLM_ENABLED", "0")
    result, status = M.run({"market": "KR"})
    assert result is None
    assert status == "disabled"


def test_validate_output_rejects_unknown_keys():
    with pytest.raises(ValueError, match="unknown keys"):
        M.validate_output({"summary": "요약", "trade_instruction": "매수"}, {"market": "US"})


def test_run_accepts_schema_checked_json(monkeypatch):
    monkeypatch.setenv("MOCK_REPORT_LLM_ENABLED", "1")
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout=(
            '{"summary":"전략은 벤치마크 대비 우위입니다.",'
            '"position_notes":["MSFT 비중 점검"],'
            '"decision_notes":["최근 편입은 정책점수 근거"],'
            '"risk_checks":["비용차감 성과 확인"],'
            '"confidence":72}'
        ), stderr="")

    result, status = M.run({"market": "US", "positions": [{"ticker": "MSFT"}]}, runner=fake_runner)
    assert status == "ok"
    assert result["summary"] == "전략은 벤치마크 대비 우위입니다."
    assert result["position_notes"] == ["MSFT 비중 점검"]
    assert result["confidence"] == 72
    assert calls[0][0][:4] == ["hermes", "chat", "-q", calls[0][0][3]]
    assert "MSFT" in calls[0][0][3]


def test_run_rejects_non_json_output(monkeypatch):
    monkeypatch.setenv("MOCK_REPORT_LLM_ENABLED", "1")

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="plain text", stderr="")

    result, status = M.run({"market": "KR"}, runner=fake_runner)
    assert result is None
    assert status.startswith("guard rejected:")


# ── A-09 회귀: payload 대비 근거 검증 — 실패는 guard rejected + 정량 근거 fallback ─

_PAYLOAD = {
    "market": "US", "nav": 100.0, "day_ret_pct": 0.42, "cum_ret_pct": 12.3456,
    "benchmark_ret_pct": 7.0, "excess_pctp": 5.3456, "strategy_mdd_pct": 4.1,
    "benchmark_mdd_pct": 12.0,
    "positions": [{"ticker": "AAPL", "weight_pct": 60.0, "return_pct": 8.25, "value": 60.0}],
    "recent_decisions": [{"side": "편입", "ticker": "AAPL", "reason": "정책점수 0.82 상위",
                          "policy_score": 0.82}],
}


def _valid(**overrides):
    out = {
        "summary": "누적 +12.3%로 QQQ 대비 +5.3%p 우위입니다.",
        "position_notes": ["AAPL 비중 60% · 평가수익 8.3% 확인"],
        "decision_notes": ["최근 AAPL 편입은 정책점수 0.82 근거"],
        "risk_checks": ["MDD 4.1% 유지 여부 확인 필요"],
        "confidence": 70,
    }
    out.update(overrides)
    return out


def _run_with(monkeypatch, output):
    import json
    monkeypatch.setenv("MOCK_REPORT_LLM_ENABLED", "1")

    def runner(cmd, **kwargs):
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return SimpleNamespace(returncode=0, stdout=text, stderr="")

    return M.run(_PAYLOAD, runner=runner)


def test_a_fix_exec_grounded_output_is_published(monkeypatch):
    result, status = _run_with(monkeypatch, _valid())
    assert status == "ok"
    assert result["position_notes"] == ["AAPL 비중 60% · 평가수익 8.3% 확인"]
    assert result["confidence"] == 70


@pytest.mark.parametrize("output, needle", [
    ({"summary": "ZZZZ 상승 9999% 확정", "position_notes": "가짜"}, "missing keys"),
    (_valid(summary="ZZZZ 비중 점검 필요"), "unknown ticker ZZZZ"),
    (_valid(summary="누적 수익률 9999% 기록"), "number not in input 9999%"),
    (_valid(risk_checks=["다음 분기 +25% 상승 전망"]), "number not in input 25%"),
    (_valid(summary="AAPL 다음 달 상승 확정"), "assertive"),
    (_valid(position_notes="AAPL 비중 점검"), "position_notes must be list"),
    (_valid(summary=["누적 우위"]), "summary must be non-empty string"),
    (_valid(decision_notes=[1, 2]), "decision_notes must be list"),
    (_valid(confidence=True), "confidence must be number"),
    (_valid(confidence="70"), "confidence must be number"),
    ({}, "missing keys"),
])
def test_a_fix_exec_ungrounded_or_malformed_output_rejected(monkeypatch, output, needle):
    result, status = _run_with(monkeypatch, output)
    assert result is None
    assert status.startswith("guard rejected:") and needle in status


def test_a_fix_exec_kr_code_must_be_in_payload():
    payload = {"market": "KR", "positions": [{"code": "005930", "name": "삼성전자", "weight_pct": 68.2}]}
    ok = M.validate_output(_valid(summary="삼성전자(005930) 비중 68.2%",
                                  position_notes=[], decision_notes=[], risk_checks=[]), payload)
    assert ok["summary"] == "삼성전자(005930) 비중 68.2%"
    with pytest.raises(ValueError, match="unknown code 000660"):
        M.validate_output(_valid(summary="000660 비중 확대 검토",
                                 position_notes=[], decision_notes=[], risk_checks=[]), payload)


def test_a_fix_exec_fallback_section_marks_quantitative_basis():
    lines = M.fallback_section("guard rejected: summary: unknown ticker ZZZZ")
    assert lines[0] == "🧠 LLM 판단근거"
    assert any("정량 근거 fallback" in line for line in lines)
    assert any("guard rejected" in line for line in lines)
    assert M.fallback_section("disabled") == []
