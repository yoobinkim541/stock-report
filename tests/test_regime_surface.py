#!/usr/bin/env python3
"""test_regime_surface.py — 레짐 리포트 노출 헬퍼 (무네트워크·순수).

barbell_strategy.regime_line(포맷)·_section_qqq_radar(삽입/생략)·build_simulation_report(억제).
detect_regime 의 네트워크 경로는 graceful None 만 단위로 확인(실데이터는 스모크에서).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_regime_line_none():
    import barbell_strategy as B
    assert B.regime_line(None) is None
    assert B.regime_line({}) is None


def test_regime_line_sideways_calm():
    import barbell_strategy as B
    s = B.regime_line({"sideways": True, "substate": "sideways_calm", "er": 0.28, "ret60": 0.021})
    assert "횡보" in s and "저변동" in s and "ER 0.28" in s and "+2.1%" in s


def test_regime_line_sideways_choppy():
    import barbell_strategy as B
    s = B.regime_line({"sideways": True, "substate": "sideways_choppy", "er": 0.31, "ret60": -0.03})
    assert "횡보" in s and "고변동" in s and "-3.0%" in s


def test_regime_line_trend():
    import barbell_strategy as B
    s = B.regime_line({"sideways": False, "er": 0.55, "ret60": 0.09})
    assert "추세" in s and "ER 0.55" in s


def test_radar_inserts_and_omits_regime():
    import barbell_strategy as B
    qqq = {"current": 500, "high_52w": 510, "low_52w": 400, "position_52w_pct": 90,
           "mom_1m_pct": 2.0, "mom_3m_pct": 5.0}
    ma = {"above_ma200": True, "gap_pct": 5.0}
    with_line = B._section_qqq_radar(qqq, ma, -2.0, 55.0, 20.0, {}, regime_ln="  📈 레짐   추세/방향성")
    assert any("레짐" in x for x in with_line)
    without = B._section_qqq_radar(qqq, ma, -2.0, 55.0, 20.0, {})
    assert not any("레짐" in x for x in without)


def test_sim_report_omits_regime():
    import barbell_strategy as B
    rep = B.build_simulation_report("2")   # show_regime=False → 무네트워크·레짐 줄 없음
    assert "레짐" not in rep


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
