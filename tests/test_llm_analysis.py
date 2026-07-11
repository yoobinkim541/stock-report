"""providers/llm_analysis 단위 테스트 — 프롬프트·파서(금지어·균형·길이)·캐시·graceful (무네트워크)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import llm_analysis as la  # noqa: E402

_GOOD = {"summary": "고성장 대비 밸류 부담이 공존한다",
         "bulls": ["클라우드 매출 성장 지속", "마진 개선 추세"],
         "bears": ["PER 30배 밸류 부담", "환율 역풍"],
         "valuation": "PER 30·ROE 40% — 성장 대비 프리미엄 구간.",
         "technicals": "200일선 아래·RSI 중립 — 방향성 탐색 구간.",
         "checkpoints": ["다음 분기 마진 방향", "리비전 모멘텀 지속 여부"]}


def test_build_prompt_contract():
    p = la.build_prompt("MSFT", "Microsoft (MSFT)", {"현재가": 385.1, "밸류에이션": {"per": 30.0}})
    assert "DATA" in p and "385.1" in p and '"per": 30.0' in p
    assert "매수/매도/보유 권고" in p and "지어내지 마라" in p    # 처방 금지·환각 금지 지시
    assert "JSON 객체 한 개만" in p
    # DATA 절단 (비용 통제)
    big = la.build_prompt("T", "T", {"x": "y" * 9000})
    assert len(big) < 4600


def test_parse_analysis_valid_and_fenced():
    assert la.parse_analysis(json.dumps(_GOOD, ensure_ascii=False))["summary"].startswith("고성장")
    fenced = "설명입니다\n```json\n" + json.dumps(_GOOD, ensure_ascii=False) + "\n```"
    out = la.parse_analysis(fenced)
    assert out and len(out["bulls"]) == 2 and len(out["checkpoints"]) == 2


def test_parse_analysis_forbidden_and_balance():
    # 항목 금지어 → 그 항목만 폐기
    d = dict(_GOOD, bulls=["지금이 기회다 매수하세요", "클라우드 성장"])
    out = la.parse_analysis(json.dumps(d, ensure_ascii=False))
    assert out and out["bulls"] == ["클라우드 성장"]
    # 요약 금지어 → 전체 폐기
    assert la.parse_analysis(json.dumps(dict(_GOOD, summary="확실한 매수 기회"),
                                        ensure_ascii=False)) is None
    # 강점/리스크 한쪽 비면 균형 실패 → 전체 폐기
    assert la.parse_analysis(json.dumps(dict(_GOOD, bears=[]), ensure_ascii=False)) is None
    # 목표가 제시 항목 폐기
    d2 = dict(_GOOD, bears=["목표가 500달러 하회 위험", "환율 역풍"])
    assert la.parse_analysis(json.dumps(d2, ensure_ascii=False))["bears"] == ["환율 역풍"]


def test_parse_analysis_caps_and_garbage():
    d = dict(_GOOD, bulls=["a" * 300] * 9, checkpoints=["c"] * 9)
    out = la.parse_analysis(json.dumps(d, ensure_ascii=False))
    assert len(out["bulls"]) <= 4 and all(len(b) <= 100 for b in out["bulls"])
    assert len(out["checkpoints"]) <= 3
    assert la.parse_analysis("그냥 텍스트") is None
    assert la.parse_analysis("[1, 2]") is None
    assert la.parse_analysis("") is None


class _Res:
    def __init__(self, out, code=0):
        self.stdout, self.stderr, self.returncode = out, "", code


def test_analyze_ok_cache_and_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(la, "CACHE_DIR", tmp_path)
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return _Res(json.dumps(_GOOD, ensure_ascii=False))

    out, status = la.analyze("MSFT", "Microsoft", {"현재가": 385}, runner=runner)
    assert status == "ok" and out["summary"] and out["generated_at"] and out["model"]
    assert "--provider" in calls[0] and "-Q" in calls[0]
    # 2회차 = 디스크 캐시 (LLM 재호출 없음)
    out2, status2 = la.analyze("MSFT", "Microsoft", {}, runner=runner)
    assert status2 == "cached" and len(calls) == 1 and out2["summary"] == out["summary"]
    # force = 재호출
    _, status3 = la.analyze("MSFT", "Microsoft", {}, runner=runner, force=True)
    assert status3 == "ok" and len(calls) == 2
    # 실패 graceful
    assert la.analyze("T2", "T", {}, runner=lambda *a, **k: _Res("", 1))[1].startswith("call failed")
    assert la.analyze("T3", "T", {}, runner=lambda *a, **k: _Res("no json"))[1] == "empty"
    monkeypatch.setenv("DASH_LLM_ANALYSIS_ENABLED", "0")
    assert la.analyze("T4", "T", {})[1] == "disabled"
