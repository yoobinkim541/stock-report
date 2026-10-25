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
        "summary": "누적 {cum_ret_pct}로 QQQ 대비 {excess_pctp} 우위입니다.",
        "position_notes": ["AAPL {positions.AAPL.weight_pct} · {positions.AAPL.return_pct} 확인"],
        "decision_notes": ["최근 AAPL 편입은 {recent_decisions.AAPL.policy_score} 근거"],
        "risk_checks": ["{strategy_mdd_pct} 유지 여부 확인 필요"],
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
    assert result["summary"] == ("누적 [누적 수익률 +12.35%]로 QQQ 대비 "
                                 "[벤치마크 대비 초과수익 +5.35%p] 우위입니다.")
    assert result["position_notes"] == ["AAPL [AAPL 비중 60.00%] · [AAPL 평가수익률 +8.25%] 확인"]
    assert result["decision_notes"] == ["최근 AAPL 편입은 [AAPL 정책점수 0.82] 근거"]
    assert result["confidence"] == 70


@pytest.mark.parametrize("output, needle", [
    ({"summary": "ZZZZ 상승 9999% 확정", "position_notes": "가짜"}, "missing keys"),
    (_valid(summary="ZZZZ 비중 점검 필요"), "unknown ticker ZZZZ"),
    (_valid(summary="누적 수익률 9999% 기록"), "literal number 9999"),
    (_valid(risk_checks=["다음 분기 +25% 상승 전망"]), "literal number 25"),
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
    ok = M.validate_output(_valid(summary="삼성전자(005930) {positions.005930.weight_pct}",
                                  position_notes=[], decision_notes=[], risk_checks=[]), payload)
    assert ok["summary"] == "삼성전자(005930) [삼성전자(005930) 비중 68.20%]"
    with pytest.raises(ValueError, match="unknown code 000660"):
        M.validate_output(_valid(summary="000660 비중 확대 검토",
                                 position_notes=[], decision_notes=[], risk_checks=[]), payload)


def test_a_fix_exec_fallback_section_marks_quantitative_basis():
    lines = M.fallback_section("guard rejected: summary: unknown ticker ZZZZ")
    assert lines[0] == "🧠 LLM 판단근거"
    assert any("정량 근거 fallback" in line for line in lines)
    assert any("guard rejected" in line for line in lines)
    assert M.fallback_section("disabled") == []


# ── RA-04 회귀: 수치는 (종목·지표·부호·단위) 사실 참조로만 게시 ─────────────────────

_FACT_PAYLOAD = {
    "market": "US", "nav": 100.0, "cum_ret_pct": -10.0, "excess_pctp": -4.0,
    "positions": [{"ticker": "AAPL", "weight_pct": 40.0, "return_pct": -10.0, "value": 40.0},
                  {"ticker": "MSFT", "weight_pct": 10.0, "return_pct": 3.0, "value": 10.0}],
    "scorecard": {"n_buy": 12, "buy_hit": 50.0},
}


def _only_summary(summary):
    return _valid(summary=summary, position_notes=[], decision_notes=[], risk_checks=[])


@pytest.mark.parametrize("summary, needle", [
    ("AAPL 누적수익률 +10%", "literal number 10"),          # 손실 -10% 를 +10% 로 부호 반전
    ("AAPL 누적수익률 100%", "literal number 100"),          # NAV 100 을 수익률 100% 로 전용
    ("AAPL 비중 10%", "literal number 10"),                  # MSFT 비중 10 을 AAPL 비중으로
    ("편입 12건 기준 수익률 12%", "literal number 12"),       # 건수를 금융 지표로 전용
    ("누적 {cum_ret_pct} 이익 확인", "contradicts cum_ret_pct"),   # 음수 값 + 상승/이익 표현
    ("AAPL {positions.AAPL.return_pct} 상승", "contradicts positions.AAPL.return_pct"),
    ("MSFT {positions.MSFT.return_pct} 손실", "contradicts positions.MSFT.return_pct"),
    ("AAPL {positions.AAPL.cum_ret_pct} 확인", "unknown fact reference positions.AAPL.cum_ret_pct"),
    ("ZZZZ {positions.ZZZZ.weight_pct}", "unknown fact reference positions.ZZZZ.weight_pct"),
    ("누적 { cum_ret_pct } 확인", "malformed fact reference"),      # 치환되지 않는 참조 원문 게시 금지
])
def test_a_exec_fix2_rationale_rejects_unbound_or_sign_flipped_numbers(summary, needle):
    with pytest.raises(ValueError, match=needle.replace(".", r"\.")):
        M.validate_output(_only_summary(summary), _FACT_PAYLOAD)


def test_a_exec_fix2_rationale_renders_bound_values_with_entity_sign_and_unit():
    out = M.validate_output(_valid(
        summary="누적 {cum_ret_pct}로 QQQ 대비 {excess_pctp} 열위",
        position_notes=["AAPL 비중 {positions.MSFT.weight_pct}"],     # 다른 종목 값 인용
        decision_notes=["수익률 {nav}"],                               # NAV 를 수익률 자리에 인용
        risk_checks=["표본 {scorecard.n_buy}"],
    ), _FACT_PAYLOAD)
    # 게시되는 숫자는 언제나 payload 값 + 그 값의 종목·지표·부호·단위 라벨이다.
    assert out["summary"] == ("누적 [누적 수익률 -10.00%]로 QQQ 대비 "
                              "[벤치마크 대비 초과수익 -4.00%p] 열위")
    assert out["position_notes"] == ["AAPL 비중 [MSFT 비중 10.00%]"]
    assert out["decision_notes"] == ["수익률 [NAV $100.00]"]
    assert out["risk_checks"] == ["표본 [편입 평가 건수 12]"]


def test_a_exec_fix2_rationale_drops_ambiguous_duplicate_entity_facts():
    payload = {"market": "US", "recent_decisions": [
        {"ticker": "AAPL", "policy_score": 0.8}, {"ticker": "AAPL", "policy_score": 0.3}]}
    assert "recent_decisions.AAPL.policy_score" not in M.fact_table(payload)
    with pytest.raises(ValueError, match="unknown fact reference"):
        M.validate_output(_only_summary("AAPL {recent_decisions.AAPL.policy_score}"), payload)
