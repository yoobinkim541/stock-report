"""tests/test_chart_vision.py — LLM 비전 기반 차트 패턴 분석(삼각수렴·엘리엇 파동 등).

검증:
  - render_chart_image(): OHLC 프레임 → PNG 파일 생성, 빈/불완전 프레임이면 None
  - _build_vision_prompt(): 패턴 분류·인젝션 가드 문구 포함
  - _parse_vision_response(): 순수 JSON/코드펜스 JSON 파싱, patterns 키 없으면 None
  - analyze_chart_patterns(): llm_fn 주입으로 이미지 렌더 성공 시 patterns 반환,
    렌더 실패·LLM 예외·파싱 실패·스키마 위반 시 ok=False 로 안전 실패,
    계산값과 대조되지 않는 수치 문장은 숨김(unverified_claims)
  - analysis_identity(): 봉 단위·세션·새 봉·보정 OHLC·리플레이 위치별로 다른 캐시 key
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


# --- C-01 / C-02 / C-03 회귀 ---------------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402


def _review_hist(values=None, index=None):
    values = list(range(100, 200)) if values is None else values
    if index is None:
        index = pd.date_range("2026-08-01", periods=len(values), freq="h", tz="UTC")
    return pd.DataFrame({
        "Open": values, "High": [v + 1 for v in values],
        "Low": [v - 1 for v in values], "Close": values,
        "Volume": range(100, 100 + len(values)),
    }, index=index)


def _llm_returning(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return lambda prompt, image_path: text


@pytest.mark.parametrize("patterns", [
    [1, {}, None],
    [{}],
    [{"kind": "invented", "confidence": 0.5}],
    [{"kind": "wedge", "confidence": "certain"}],
    [{"kind": "wedge", "confidence": True}],
    [{"kind": "wedge", "confidence": 1.5}],
    [{"kind": "wedge", "confidence": -0.1}],
    [{"kind": "wedge"}],
    [{"kind": "wedge", "confidence": 0.5, "description": 42}],
    [{"kind": "wedge", "confidence": 0.5, "description": "x" * 601}],
])
def test_vision_rejects_schema_violations_instead_of_reporting_no_pattern(patterns):
    result = cv.analyze_chart_patterns(_review_hist(), "AAPL", llm_fn=_llm_returning({"patterns": patterns}))

    assert result["ok"] is False
    assert result["reason"].startswith("invalid_llm_schema")
    assert result["patterns"] == []


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_vision_rejects_non_finite_confidence(literal):
    text = '{"patterns": [{"kind": "wedge", "confidence": %s}], "summary": ""}' % literal

    result = cv.analyze_chart_patterns(_review_hist(), "AAPL", llm_fn=_llm_returning(text))

    assert result["ok"] is False
    assert "confidence" in result["reason"]


def test_vision_rejects_non_string_summary():
    result = cv.analyze_chart_patterns(
        _review_hist(), "AAPL", llm_fn=_llm_returning({"patterns": [], "summary": ["list"]}),
    )

    assert result["ok"] is False
    assert result["reason"].startswith("invalid_llm_schema")


def test_vision_valid_empty_patterns_is_success():
    result = cv.analyze_chart_patterns(
        _review_hist(), "AAPL", llm_fn=_llm_returning({"patterns": [], "summary": "뚜렷한 패턴 없음"}),
    )

    assert result["ok"] is True
    assert result["patterns"] == []
    assert result["summary"] == "뚜렷한 패턴 없음"


def test_vision_hides_numeric_claims_that_contradict_computed_values():
    payload = {
        "patterns": [{
            "kind": "wedge", "confidence": 0.6,
            "description": "현재가 999999, RSI 999, 지지선 -100",
            "implication": "상승 쐐기 이탈 주의.",
        }],
        "summary": "목표가 999999",
    }

    result = cv.analyze_chart_patterns(_review_hist(), "AAPL", llm_fn=_llm_returning(payload))

    assert result["ok"] is True
    assert result["facts"]["last_close"] == pytest.approx(199.0)
    assert result["facts"]["rsi_14"] == pytest.approx(100.0)
    pattern = result["patterns"][0]
    assert pattern["description"] == ""
    assert pattern["implication"] == "상승 쐐기 이탈 주의."
    assert result["summary"] == ""
    shown = json.dumps([result["patterns"], result["summary"]], ensure_ascii=False)
    assert "999999" not in shown and "999" not in shown and "-100" not in shown
    reasons = {reason for claim in result["unverified_claims"] for reason in claim["reasons"]}
    assert {"current_price_mismatch", "rsi_out_of_bounds", "non_positive_price", "out_of_range_level"} <= reasons


def test_vision_keeps_claims_consistent_with_computed_values():
    payload = {
        "patterns": [{
            "kind": "elliott_wave", "confidence": 0.7,
            "description": "현재가 199 부근, RSI(14) 100 으로 과매수. 지지선 180 확인.",
            "implication": "RSI 70 이상 구간이 이어지면 5파 마무리 가능.",
        }],
        "summary": "엘리엇 5파 진행 중. RSI 55 로 중립.",
    }

    result = cv.analyze_chart_patterns(_review_hist(), "AAPL", llm_fn=_llm_returning(payload))

    assert result["ok"] is True
    pattern = result["patterns"][0]
    assert pattern["description"] == "현재가 199 부근, RSI(14) 100 으로 과매수. 지지선 180 확인."
    assert pattern["implication"] == "RSI 70 이상 구간이 이어지면 5파 마무리 가능."
    assert result["summary"] == "엘리엇 5파 진행 중."
    assert [claim["reasons"] for claim in result["unverified_claims"]] == [["rsi_mismatch"]]


def test_vision_prompt_carries_computed_facts():
    captured = {}

    def _llm(prompt, image_path):
        captured["prompt"] = prompt
        return '{"patterns": [], "summary": ""}'

    cv.analyze_chart_patterns(_review_hist(), "AAPL", llm_fn=_llm)

    assert "현재가(마지막 종가) 199" in captured["prompt"]
    assert "RSI(14) 100.0" in captured["prompt"]


def test_analysis_identity_separates_timeframe_new_bar_corrected_bar_and_replay():
    base = _review_hist()
    key = cv.analysis_identity(base, "AAPL", timeframe="1h", session="regular")["key"]

    assert cv.analysis_identity(base.copy(), "AAPL", timeframe="1h", session="regular")["key"] == key
    assert cv.analysis_identity(base, "AAPL", timeframe="1d", session="regular")["key"] != key
    assert cv.analysis_identity(base, "AAPL", timeframe="1h", session="extended")["key"] != key
    assert cv.analysis_identity(base, "MSFT", timeframe="1h", session="regular")["key"] != key

    newer = _review_hist(list(range(100, 201)))
    assert cv.analysis_identity(newer, "AAPL", timeframe="1h", session="regular")["key"] != key

    corrected = base.copy()
    corrected.iloc[-1, corrected.columns.get_loc("Close")] = 198
    assert cv.analysis_identity(corrected, "AAPL", timeframe="1h", session="regular")["key"] != key

    replay_a = cv.analysis_identity(base, "AAPL", timeframe="1h", replay_cursor="2026-08-03T00:00:00Z")
    replay_b = cv.analysis_identity(base, "AAPL", timeframe="1h", replay_cursor="2026-08-04T00:00:00Z")
    assert replay_a["key"] != replay_b["key"]

    identity = cv.analysis_identity(base, "AAPL", timeframe="1h")
    assert identity["last_bar"] == base.index[-1].isoformat()
    assert identity["prompt_version"] == cv.VISION_PROMPT_VERSION


def test_analysis_result_stores_the_requested_identity():
    identity = cv.analysis_identity(_review_hist(), "AAPL", timeframe="1h")

    ok = cv.analyze_chart_patterns(_review_hist(), "AAPL", llm_fn=_llm_returning({"patterns": []}), identity=identity)
    failed = cv.analyze_chart_patterns(_review_hist(), "AAPL", llm_fn=_llm_returning("x"), identity=identity)

    assert ok["identity"]["key"] == identity["key"]
    assert failed["identity"]["key"] == identity["key"]
