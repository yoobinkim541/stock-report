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
                        lambda t: {"div_yield": 0.0098, "payout": 0.2, "pbr": 6.69, "roe": 0.34})
    monkeypatch.setattr(intrinsic, "_spot_price", lambda t: 417.0)
    out = intrinsic.intrinsic("MSFT")
    assert out["rim"] and out["rim"]["mid"] > 0
    assert out["rim"]["low"] <= out["rim"]["mid"] <= out["rim"]["high"]
    assert out["ddm_reliable"] is False                              # payout 0.2 < 0.4 → 신뢰도 낮음
    assert out["upside_pct"] is not None


def test_intrinsic_high_payout_reliable(monkeypatch):
    from providers import earnings_data
    monkeypatch.setattr(earnings_data, "valuation_metrics",
                        lambda t: {"div_yield": 0.04, "payout": 0.7, "pbr": 2.0, "roe": 0.15})
    monkeypatch.setattr(intrinsic, "_spot_price", lambda t: 100.0)
    out = intrinsic.intrinsic("KO")
    assert out["ddm_reliable"] is True
    assert out["ddm"] is not None


def test_intrinsic_ddm_uses_fraction_scale_div_yield_correctly(monkeypatch):
    """earnings_data.valuation_metrics()의 div_yield 는 소수(0.04=4%) — intrinsic.py 가
    이를 퍼센트로 오인해 다시 /100 하면 DDM 적정가가 실제값의 약 1/100로 나온다."""
    from providers import earnings_data
    monkeypatch.setattr(earnings_data, "valuation_metrics",
                        lambda t: {"div_yield": 0.04, "payout": 0.7, "pbr": None, "roe": None})
    monkeypatch.setattr(intrinsic, "_spot_price", lambda t: 100.0)
    out = intrinsic.intrinsic("KO")
    assert out["ddm"] is not None
    # d0 = div_yield * price = $4/주. 기본 g=0.04, r_band mid=0.095:
    # ddm = 4*1.04/(0.095-0.04) ≈ 75.64. 버그 상태면 d0=$0.04 → mid ≈ 0.756 (100배 축소).
    assert 70 < out["ddm"]["mid"] < 80


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


def test_econ_parse_converts_utc_z_suffix_to_kst():
    """saveticker API 는 UTC(Z 접미) 로 이벤트 시각을 준다 — KST 로 변환 없이 naive
    로 그대로 쓰면 '오늘' 필터링·표시 시각이 최대 하루/9시간 어긋난다."""
    sample = [{"title": "FOMC", "event_date": "2026-07-11T23:00:00Z", "color": "#EF4444"}]
    out = econ_calendar._parse(sample)
    when = out[0]["when"]
    assert when is not None
    assert when.utcoffset().total_seconds() == 9 * 3600          # KST = UTC+9
    assert (when.year, when.month, when.day, when.hour) == (2026, 7, 12, 8)  # 다음날 08:00 KST
    assert out[0]["date_str"] == "07/12 08:00"


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
