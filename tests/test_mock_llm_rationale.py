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
        M.validate_output({"summary": "요약", "trade_instruction": "매수"})


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
