"""tests/test_intrinsic_econ.py — QT2 신규 소스 (DDM/RIM·경제캘린더) 단위테스트.

밸류에이션 math 는 닫힌해로 검증. 경제캘린더는 파싱/정렬/중요도 매핑(무네트워크).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import econ_calendar, intrinsic


def test_ddm_value_closed_form():
    assert abs(intrinsic.ddm_value(4.0, 0.05, 0.09) - 105.0) < 1e-6   # 4*1.05/0.04
    assert intrinsic.ddm_value(4.0, 0.10, 0.09) is None               # g>=r
    assert intrinsic.ddm_value(0.0, 0.05, 0.09) is None               # 무배당


def test_rim_value_closed_form():
    exp = 62.3 + 62.3 * (0.34 - 0.09) / (0.09 - 0.05)
    assert abs(intrinsic.rim_value(62.3, 0.34, 0.09, 0.05) - exp) < 1e-6
    assert intrinsic.rim_value(62.3, 0.34, 0.05, 0.05) is None        # r<=g


def test_intrinsic_low_payout_flags_ddm(monkeypatch):
    from providers import earnings_data
    monkeypatch.setattr(earnings_data, "valuation_metrics",
                        lambda t: {"div_yield": 0.98, "payout": 0.2, "pbr": 6.69, "roe": 0.34})
    monkeypatch.setattr(intrinsic, "_spot_price", lambda t: 417.0)
    out = intrinsic.intrinsic("MSFT")
    assert out["rim"] and out["rim"]["mid"] > 0
    assert out["rim"]["low"] <= out["rim"]["mid"] <= out["rim"]["high"]
    assert out["ddm_reliable"] is False                              # payout 0.2 < 0.4 → 신뢰도 낮음
    assert out["upside_pct"] is not None


def test_intrinsic_high_payout_reliable(monkeypatch):
    from providers import earnings_data
    monkeypatch.setattr(earnings_data, "valuation_metrics",
                        lambda t: {"div_yield": 4.0, "payout": 0.7, "pbr": 2.0, "roe": 0.15})
    monkeypatch.setattr(intrinsic, "_spot_price", lambda t: 100.0)
    out = intrinsic.intrinsic("KO")
    assert out["ddm_reliable"] is True
    assert out["ddm"] is not None


def test_intrinsic_missing_inputs_graceful(monkeypatch):
    from providers import earnings_data
    monkeypatch.setattr(earnings_data, "valuation_metrics", lambda t: {})
    monkeypatch.setattr(intrinsic, "_spot_price", lambda t: None)
    out = intrinsic.intrinsic("XYZ")
    assert out["rim"] is None and out["ddm"] is None
    assert out["upside_pct"] is None


def test_econ_importance():
    assert econ_calendar._importance("#EF4444")[0] == "high"
    assert econ_calendar._importance("#10B981")[0] == "low"
    assert econ_calendar._importance(None)[0] == "info"


def test_econ_parse_sort_and_marker():
    sample = [
        {"title": "  CPI  ", "event_date": "2026-07-11T21:30:00", "color": "#EF4444"},
        {"title": "no date", "event_date": None, "color": "#10B981"},
        {"title": "FOMC", "event_date": "2026-07-01T18:00:00", "color": "#EF4444"},
    ]
    out = econ_calendar._parse(sample)
    assert out[0]["title"] == "FOMC"                 # 가장 이른 날짜
    assert out[0]["marker"] == "🔴" and out[0]["importance"] == "high"
    assert out[0]["when"] is not None
    assert out[-1]["title"] == "no date"             # 무일자 → 맨 뒤
