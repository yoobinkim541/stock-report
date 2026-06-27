#!/usr/bin/env python3
"""test_naver_kr.py — Naver KR 수급 + KOSPI200 (무네트워크, 모킹)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_num_pct_parse():
    from providers import naver_kr as nk
    assert nk._num("-5,975,701") == -5975701
    assert nk._num("+9,298,204") == 9298204
    assert nk._num("x") is None
    assert nk._pct("47.27%") == 0.4727
    assert nk._pct(None) is None


def test_parse_trend():
    from providers import naver_kr as nk
    j = [{"bizdate": "20260626", "foreignerPureBuyQuant": "-5,975,701",
          "organPureBuyQuant": "-3,593,889", "individualPureBuyQuant": "+9,298,204",
          "foreignerHoldRatio": "47.27%", "closePrice": "53,200"}]
    rows = nk._parse_trend(j)
    assert rows[0]["foreign_net"] == -5975701 and rows[0]["inst_net"] == -3593889
    assert rows[0]["indiv_net"] == 9298204 and rows[0]["foreign_ratio"] == 0.4727
    assert rows[0]["close"] == 53200


def test_investor_flow_features(monkeypatch):
    from providers import naver_kr as nk
    flow = [   # 최신순
        {"date": "20260626", "foreign_net": 100, "inst_net": 50, "indiv_net": -150, "foreign_ratio": 0.47, "close": 1},
        {"date": "20260625", "foreign_net": 200, "inst_net": -30, "indiv_net": -170, "foreign_ratio": 0.47, "close": 1},
        {"date": "20260624", "foreign_net": -50, "inst_net": 10, "indiv_net": 40, "foreign_ratio": 0.47, "close": 1},
        {"date": "20260623", "foreign_net": 300, "inst_net": 20, "indiv_net": -320, "foreign_ratio": 0.47, "close": 1},
        {"date": "20260622", "foreign_net": 400, "inst_net": 0, "indiv_net": -400, "foreign_ratio": 0.47, "close": 1},
    ]
    monkeypatch.setattr(nk, "investor_flow", lambda code, days=20: flow)
    f = nk.investor_flow_features("005930")
    assert f["foreign_buy_streak"] == 2                 # 최신 +100,+200 후 -50 중단
    assert f["foreign_net_5d"] == 100 + 200 - 50 + 300 + 400
    assert f["foreign_ratio"] == 0.47


def test_kospi200_members(monkeypatch):
    from providers import naver_kr as nk

    def fake_get(url):
        if "page=1" in url:
            return "a code=005930 b code=000660 c code=005930".encode("euc-kr")
        return "".encode("euc-kr")        # page2 비어있음 → 중단
    monkeypatch.setattr(nk, "_get", fake_get)
    assert nk.kospi200_members() == ["000660", "005930"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
