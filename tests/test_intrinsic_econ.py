"""tests/test_intrinsic_econ.py — QT2 신규 소스 (DDM/RIM·경제캘린더) 단위테스트.

밸류에이션 math 는 닫힌해로 검증. 경제캘린더는 파싱/정렬/중요도 매핑(무네트워크).
"""
import os
import sys

import pytest

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


def test_econ_parse_keeps_us_events_and_high_impact_abroad_in_kst_order():
    rows = [
        {"title": "Core CPI m/m", "country": "USD", "date": "2026-11-19T08:30:00-05:00", "impact": "High",
         "forecast": "0.3%", "previous": "0.2%"},
        {"title": "German ZEW", "country": "EUR", "date": "2026-11-18T05:00:00-05:00", "impact": "Medium"},
        {"title": "BOJ Policy Rate", "country": "JPY", "date": "2026-11-18T22:00:00-05:00", "impact": "High"},
        {"title": "Bank Holiday", "country": "USD", "date": "2026-11-26T00:00:00-05:00", "impact": "Holiday"},
        {"title": "no zone", "country": "USD", "date": "2026-11-19T08:30:00", "impact": "High"},
    ]
    out = econ_calendar._parse(rows)
    assert [event["title"] for event in out] == ["JPY BOJ Policy Rate", "Core CPI m/m", "Bank Holiday"]
    cpi = out[1]
    assert cpi["when"].utcoffset().total_seconds() == 9 * 3600
    assert cpi["date_str"] == "11/19 22:30"                     # 08:30 ET = 22:30 KST
    assert (cpi["importance"], cpi["marker"]) == ("high", "🔴")
    assert (cpi["forecast"], cpi["previous"]) == ("0.3%", "0.2%")
    assert out[2]["importance"] == "info"


def test_econ_upcoming_window_and_failed_feed_is_not_an_empty_week(monkeypatch):
    from datetime import date

    import requests

    class Response:
        def __init__(self, status, rows=None):
            self.status_code, self.ok, self._rows = status, status == 200, rows
        def json(self):
            return self._rows
    rows = [{"title": t, "country": "USD", "date": d, "impact": "Medium"} for t, d in (
        ("past", "2026-11-16T10:00:00-05:00"), ("today", "2026-11-18T10:00:00-05:00"),
        ("later", "2026-11-21T10:00:00-05:00"))]
    monkeypatch.setattr(requests, "get", lambda *a, **kw: Response(200, rows))
    assert [e["title"] for e in econ_calendar.upcoming_events(days=3, start=date(2026, 11, 18))] == ["today"]
    for status, availability in ((429, "blocked"), (426, "error")):
        monkeypatch.setattr(requests, "get", lambda *a, status=status, **kw: Response(status))
        with pytest.raises(Exception) as exc_info:
            econ_calendar.upcoming_events()
        assert (exc_info.value.availability, exc_info.value.status_code) == (availability, status)
