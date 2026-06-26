#!/usr/bin/env python3
"""test_kr_market_data.py — KR 생존편향 제거 데이터층 (무네트워크, marcap/FDR 모킹).

검증: 코드 정규화 · 시점별 시총 상위 N(point-in-time) · marcap_asof · 다년 OHLCV ·
상폐 사유 분류(M&A/자진 회피X, 부실 회피O) · 부실 퇴출 라벨 추출.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fake_year(y):
    rows = []
    for date in [f"{y}-06-01", f"{y}-12-30"]:
        for code, name, mc, close in [("005930", "삼성전자", 300, 100),
                                      ("000660", "SK하이닉스", 120, 50),
                                      ("035720", "카카오", 60, 30),
                                      ("068270", "셀트리온", 80, 40)]:
            rows.append({"Code": code, "Name": name, "Close": float(close),
                         "Marcap": mc * 1e12, "Market": "KOSPI", "Date": pd.Timestamp(date)})
    return pd.DataFrame(rows)


def test_norm_code_and_yf():
    from providers import kr_market_data as km
    assert km.norm_code("5930") == "005930"          # 구년도 leading-zero 보정
    assert km.norm_code("005930") == "005930"
    assert km.norm_code("005930.KS") == "005930"
    assert km.to_yf("5930") == "005930.KS"


def test_top_n_by_marcap(monkeypatch):
    from providers import kr_market_data as km
    monkeypatch.setattr(km, "_marcap_year", _fake_year)
    top2 = km.top_n_by_marcap("2024-12-30", n=2)
    assert top2 == ["005930", "000660"]              # 시총 상위 2 (point-in-time)
    top4 = km.top_n_by_marcap("2024-12-30", n=10)
    assert set(top4) == {"005930", "000660", "035720", "068270"}


def test_marcap_asof_last_trading_day(monkeypatch):
    from providers import kr_market_data as km
    monkeypatch.setattr(km, "_marcap_year", _fake_year)
    snap = km.marcap_asof("2024-07-01")              # 6-01 과 12-30 중 <= 7-01 → 6-01
    assert snap is not None and len(snap) == 4
    assert str(snap["Date"].iloc[0])[:10] == "2024-06-01"


def test_ohlcv_from_marcap(monkeypatch):
    from providers import kr_market_data as km
    monkeypatch.setattr(km, "_marcap_year", _fake_year)
    s = km.ohlcv_from_marcap("005930", 2024, 2024)
    assert s is not None and len(s) == 2 and s.iloc[0] == 100.0


def test_classify_delisting_reason():
    from providers import kr_market_data as km
    assert km.classify_delisting_reason("피흡수합병") == "merger"
    assert km.classify_delisting_reason("주식의 포괄적 교환") == "merger"
    assert km.classify_delisting_reason("자진상장폐지") == "voluntary"   # 부실 아님
    assert km.classify_delisting_reason("감사의견 거절로 상장폐지") == "distress"
    assert km.classify_delisting_reason("관리종목 지정") == "distress"
    assert km.classify_delisting_reason("자본잠식") == "distress"
    assert km.classify_delisting_reason("") == "other"


def test_distress_delistings_filters(monkeypatch):
    from providers import kr_market_data as km
    fake = pd.DataFrame([
        {"Symbol": "111111", "Name": "부실기업", "DelistingDate": "2018-05-01", "Reason": "자본잠식"},
        {"Symbol": "222222", "Name": "합병소멸", "DelistingDate": "2019-03-01", "Reason": "피흡수합병"},
        {"Symbol": "33333", "Name": "관리상폐", "DelistingDate": "2020-07-01", "Reason": "감사의견거절"},
    ])
    monkeypatch.setattr(km, "delisting_master", lambda force=False: fake)
    d = km.distress_delistings()
    assert set(d.keys()) == {"111111", "033333"}     # 합병(호재)은 제외, 6자리 정규화
    assert d["111111"]["reason"] == "자본잠식"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
