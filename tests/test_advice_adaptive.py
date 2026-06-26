#!/usr/bin/env python3
"""test_advice_adaptive.py — 포트폴리오 advice 적응 평가 (무네트워크)."""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import advice_adaptive_eval as A     # noqa: E402


def _track(pairs):
    """[(meta,rule)] → paper_track dict(성숙)."""
    return {f"2026-01-{i+1:02d}": {"ret_meta_5d": m, "ret_rule_5d": r}
            for i, (m, r) in enumerate(pairs)}


def test_samples_only_matured():
    t = {"d1": {"ret_meta_5d": 1.0, "ret_rule_5d": 0.5},
         "d2": {"meta": {}, "rule": {}}}            # 미성숙(ret 없음)
    assert A._samples(t) == [(1.0, 0.5)]


def test_evaluate_holds_on_insufficient():
    ev = A.evaluate([(1.0, 0.5)] * 5)
    assert ev["adopt"] is False and "미달" in ev["reason"]


def test_evaluate_adopts_when_meta_beats_rule_with_lower_downside():
    # meta 가 rule 대비 우위 + 하방 작음 → 채택 가능
    pairs = [(2.0, 1.0)] * 20                        # meta 항상 +1%p, 둘 다 하방 0
    ev = A.evaluate(pairs)
    assert ev["adopt"] is True and ev["excess"] == pytest.approx(1.0)


def test_evaluate_rejects_when_meta_higher_downside():
    # meta 평균 우위지만 큰 손실(하방>rule) → MDD 제약 위반 → 거부
    pairs = [(5.0, 1.0)] * 18 + [(-20.0, -1.0), (-20.0, -1.0)]
    ev = A.evaluate(pairs)
    assert ev["meta_dd"] > ev["rule_dd"] and ev["adopt"] is False


def test_evaluate_rejects_when_meta_underperforms():
    pairs = [(0.5, 1.0)] * 20                        # rule 이 더 좋음
    ev = A.evaluate(pairs)
    assert ev["excess"] < 0 and ev["adopt"] is False


def test_recommended_blend_capped():
    assert A._recommended_blend({"excess": 0.10}) == A.BLEND_MAX      # 큰 우위 → 상한
    assert A._recommended_blend({"excess": 0.0}) == pytest.approx(0.3)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
