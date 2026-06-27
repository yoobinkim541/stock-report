#!/usr/bin/env python3
"""test_us_membership.py — US 멤버십(fja05680) + EDGAR 재무추세 (무네트워크, 모킹)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── index_membership ─────────────────────────────────────────────────────────
def _snaps():
    return [("2020-01-01", frozenset({"AAA", "BBB", "CCC"})),
            ("2021-01-01", frozenset({"AAA", "BBB", "DDD"}))]   # CCC 퇴출, DDD 편입


def test_members_asof_point_in_time(monkeypatch):
    from providers import index_membership as im
    monkeypatch.setattr(im, "_sp500_snapshots", _snaps)
    assert im.members_asof("sp500", "2020-06-01") == ["AAA", "BBB", "CCC"]   # 2020 스냅샷(생존편향0)
    assert im.members_asof("sp500", "2021-06-01") == ["AAA", "BBB", "DDD"]
    assert im.members_asof("sp500", "2019-01-01") == []                       # 이전 데이터 없음


def test_change_events_and_removals(monkeypatch):
    from providers import index_membership as im
    monkeypatch.setattr(im, "_sp500_snapshots", _snaps)
    ev = im.change_events("sp500")
    assert {"date": "2021-01-01", "ticker": "DDD", "action": "add"} in ev
    assert {"date": "2021-01-01", "ticker": "CCC", "action": "remove"} in ev
    assert im.removals("sp500") == {"CCC": "2021-01-01"}


def test_members_asof_kr_delegates(monkeypatch):
    from providers import index_membership as im
    import providers.kr_market_data as km
    monkeypatch.setattr(km, "top_n_by_marcap", lambda date, n=200: ["005930", "000660"])
    assert im.members_asof("kr", "2024-01-01", n=2) == ["005930", "000660"]


# ── edgar ────────────────────────────────────────────────────────────────────
def _cf():
    def fy(vals):
        return {"units": {"USD": [{"end": e, "val": v, "fp": "FY", "form": "10-K"} for e, v in vals]}}
    return {"facts": {"us-gaap": {
        "Revenues": fy([("2019-12-31", 100), ("2020-12-31", 120)]),
        "NetIncomeLoss": fy([("2019-12-31", 10), ("2020-12-31", -5)]),
        "Assets": fy([("2019-12-31", 200), ("2020-12-31", 210)]),
        "Liabilities": fy([("2019-12-31", 100), ("2020-12-31", 150)]),
    }}}


def test_fundamental_trends():
    from providers import edgar
    f = edgar.fundamental_trends("X", asof="2021-06-01", cf=_cf())
    assert abs(f["rev_yoy"] - 0.2) < 1e-9
    assert f["is_loss"] is True
    assert abs(f["net_margin"] - (-5 / 120)) < 1e-4
    assert f["net_margin_chg"] < 0                       # 마진 악화(흑→적)
    assert abs(f["debt_to_assets"] - 150 / 210) < 1e-4
    assert f["debt_to_assets_chg"] > 0                   # 부채비율 상승
    assert f["n_years"] == 2


def test_fundamental_trends_no_lookahead():
    from providers import edgar
    f = edgar.fundamental_trends("X", asof="2019-12-31", cf=_cf())   # 2019 까지만
    assert f["rev_yoy"] is None                          # 1년뿐 → YoY 불가
    assert f["is_loss"] is False and abs(f["net_margin"] - 0.1) < 1e-9


def test_fundamental_trends_empty():
    from providers import edgar
    f = edgar.fundamental_trends("X", cf={})
    assert f["n_years"] == 0 and f["rev_yoy"] is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
