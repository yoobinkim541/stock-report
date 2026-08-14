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


def test_kospi200_members_survives_single_transient_empty_page(monkeypatch):
    """중간 페이지 하나가 일시적으로 빈 응답(네트워크 글리치)이어도, 그걸 목록 끝으로
    오판해 뒤 페이지 종목을 누락하면 안 된다 — 2연속 빈 페이지일 때만 종료."""
    from providers import naver_kr as nk

    def fake_get(url):
        if "page=1" in url:
            return "code=005930".encode("euc-kr")
        if "page=2" in url:
            return "".encode("euc-kr")        # 일시적 빈 페이지 (진짜 끝 아님)
        if "page=3" in url:
            return "code=000660".encode("euc-kr")
        return "".encode("euc-kr")            # page4 부터 진짜 끝
    monkeypatch.setattr(nk, "_get", fake_get)
    assert nk.kospi200_members() == ["000660", "005930"]


def test_kospi200_members_survives_single_page_fetch_exception(monkeypatch):
    """한 페이지에서 예외(타임아웃 등)가 나도 전체 순회를 중단하지 말고 다음
    페이지를 계속 시도해야 한다 — 기존엔 try/except 가 루프 전체를 감싸 한 페이지
    실패로 이후 모든 페이지 종목이 누락됐음."""
    from providers import naver_kr as nk

    def fake_get(url):
        if "page=1" in url:
            return "code=005930".encode("euc-kr")
        if "page=2" in url:
            raise TimeoutError("일시적 네트워크 오류")
        if "page=3" in url:
            return "code=000660".encode("euc-kr")
        return "".encode("euc-kr")
    monkeypatch.setattr(nk, "_get", fake_get)
    assert nk.kospi200_members() == ["000660", "005930"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
