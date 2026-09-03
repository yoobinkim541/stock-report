"""tests/test_chart_vision.py — LLM 비전 기반 차트 패턴 분석(삼각수렴·엘리엇 파동 등).

검증:
  - render_chart_image(): OHLC 프레임 → PNG 파일 생성, 빈/불완전 프레임이면 None
  - _build_vision_prompt(): 패턴 분류·인젝션 가드 문구 포함
  - _parse_vision_response(): 순수 JSON/코드펜스 JSON 파싱, patterns 키 없으면 None
  - analyze_chart_patterns(): llm_fn 주입으로 이미지 렌더 성공 시 patterns 반환,
    렌더 실패·LLM 예외·파싱 실패 시 ok=False 로 안전 실패
"""
from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import chart_vision as cv  # noqa: E402


def _hist(n=200, start=100.0, step=0.3):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [x + 1 for x in close],
            "Low": [x - 1 for x in close],
            "Close": [x + 0.1 for x in close],
            "Volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


def test_render_chart_image_creates_png(tmp_path):
    out_path = str(tmp_path / "chart.png")
    result = cv.render_chart_image(_hist(), "TEST", out_path)
    assert result == out_path
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 0


def test_render_chart_image_returns_none_for_empty_frame(tmp_path):
    out_path = str(tmp_path / "chart.png")
    empty = pd.DataFrame({"Open": [], "High": [], "Low": [], "Close": []})
    assert cv.render_chart_image(empty, "TEST", out_path) is None
    assert not os.path.exists(out_path)


def test_render_chart_image_returns_none_for_missing_columns(tmp_path):
    out_path = str(tmp_path / "chart.png")
    bad = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
    assert cv.render_chart_image(bad, "TEST", out_path) is None


def test_build_vision_prompt_includes_ticker_taxonomy_and_injection_guard():
    prompt = cv._build_vision_prompt("NVDA")
    assert "NVDA" in prompt
    assert "triangle_convergence" in prompt
    assert "elliott_wave" in prompt
    assert "패턴이 뚜렷하지 않으면" in prompt
    assert "신뢰할 수 없는 콘텐츠" in prompt or "실행하지 말고" in prompt


def test_parse_vision_response_plain_json():
    text = '{"patterns": [{"kind": "triangle_convergence", "confidence": 0.7, "description": "d", "implication": "i"}], "summary": "s"}'
    parsed = cv._parse_vision_response(text)
    assert parsed["summary"] == "s"
    assert parsed["patterns"][0]["kind"] == "triangle_convergence"


def test_parse_vision_response_code_fenced():
    text = '설명\n```json\n{"patterns": [], "summary": "패턴 없음"}\n```\n'
    parsed = cv._parse_vision_response(text)
    assert parsed == {"patterns": [], "summary": "패턴 없음"}


def test_parse_vision_response_missing_patterns_key_returns_none():
    assert cv._parse_vision_response('{"summary": "no patterns key"}') is None


def test_parse_vision_response_garbage_returns_none():
    assert cv._parse_vision_response("자연어 답변") is None
    assert cv._parse_vision_response(None) is None
    assert cv._parse_vision_response("") is None


def test_analyze_chart_patterns_returns_patterns_on_success():
    calls = []

    def _fake_llm(prompt, image_path):
        calls.append((prompt, image_path))
        return (
            '{"patterns": [{"kind": "elliott_wave", "confidence": 0.6, '
            '"description": "5파 추진", "implication": "상승 지속 가능성"}], '
            '"summary": "엘리엇 5파 진행 중"}'
        )

    result = cv.analyze_chart_patterns(_hist(), "NVDA", llm_fn=_fake_llm)

    assert result["ok"] is True
    assert result["patterns"][0]["kind"] == "elliott_wave"
    assert result["summary"] == "엘리엇 5파 진행 중"
    assert result["ticker"] == "NVDA"
    assert len(calls) == 1
    # llm_fn 이 진짜 이미지 파일 경로를 받았는지(렌더 후 삭제 전에 호출됨)
    assert calls[0][1].endswith(".png")


def test_analyze_chart_patterns_fails_safe_when_image_render_fails():
    def _fake_llm(prompt, image_path):
        raise AssertionError("should not be called when render fails")

    empty = pd.DataFrame({"Open": [], "High": [], "Low": [], "Close": []})
    result = cv.analyze_chart_patterns(empty, "NVDA", llm_fn=_fake_llm)

    assert result["ok"] is False
    assert result["reason"] == "image_render_failed"
    assert result["patterns"] == []


def test_analyze_chart_patterns_fails_safe_on_llm_exception():
    def _raise(prompt, image_path):
        raise RuntimeError("llm unavailable")

    result = cv.analyze_chart_patterns(_hist(), "NVDA", llm_fn=_raise)

    assert result["ok"] is False
    assert "llm unavailable" in result["reason"]


def test_analyze_chart_patterns_fails_safe_on_unparseable_response():
    result = cv.analyze_chart_patterns(_hist(), "NVDA", llm_fn=lambda p, i: "자연어 답변")

    assert result["ok"] is False
    assert result["reason"] == "invalid_llm_response"


def test_analyze_chart_patterns_cleans_up_temp_image(tmp_path):
    captured_path = {}

    def _fake_llm(prompt, image_path):
        captured_path["path"] = image_path
        assert os.path.exists(image_path)
        return '{"patterns": [], "summary": "없음"}'

    cv.analyze_chart_patterns(_hist(), "NVDA", llm_fn=_fake_llm)

    assert not os.path.exists(captured_path["path"])
