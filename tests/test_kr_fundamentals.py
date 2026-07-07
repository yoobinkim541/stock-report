#!/usr/bin/env python3
"""test_kr_fundamentals.py — KR DART+marcap 밸류에이션 계산 (무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_kr_valuation_metrics_calculates_core_ratios():
    from providers import kr_fundamentals as kf

    fin = {
        "revenue": 300_000_000_000_000.0,
        "operating_income": 45_000_000_000_000.0,
        "net_income": 30_000_000_000_000.0,
        "equity": 240_000_000_000_000.0,
        "assets": 500_000_000_000_000.0,
        "liabilities": 260_000_000_000_000.0,
        "eps": None,
        "fs_div": "CFS",
        "fs_nm": "연결재무제표",
    }
    row = {
        "Code": "005930",
        "Name": "삼성전자",
        "Market": "KOSPI",
        "Marcap": 420_000_000_000_000.0,
        "Close": 70_000.0,
        "Stocks": 6_000_000_000.0,
        "Date": "2026-07-06",
    }

    v = kf.valuation_metrics("005930.KS", year=2025, financials=fin, marcap_row=row)

    assert v["market_type"] == "kr"
    assert v["source"] == "DART+marcap"
    assert v["per"] == 14.0
    assert v["pbr"] == 1.75
    assert v["psr"] == 1.4
    assert v["roe"] == 0.125
    assert v["eps_ttm"] == 5000.0
    assert v["bps"] == 40000.0
    assert v["confidence"] == "high"


def test_kr_valuation_metrics_loss_has_no_per():
    from providers import kr_fundamentals as kf

    v = kf.valuation_metrics(
        "123456.KS",
        financials={"net_income": -100.0, "equity": 1000.0, "revenue": 5000.0},
        marcap_row={"Code": "123456", "Marcap": 2000.0, "Stocks": 100.0},
    )

    assert v["per"] is None
    assert v["per_status"] == "loss"
    assert v["pbr"] == 2.0


def test_earnings_data_uses_kr_fundamentals_first(monkeypatch):
    from providers import earnings_data as ed
    from providers import kr_fundamentals as kf

    monkeypatch.setattr(kf, "recent_annual_metrics", lambda t: {
        "market_type": "kr",
        "per": 11.0,
        "pbr": 1.2,
        "roe": 0.13,
        "eps_ttm": 6400.0,
        "confidence": "high",
        "source": "DART+marcap",
    })
    monkeypatch.setattr(ed, "_ticker", lambda s: (_ for _ in ()).throw(RuntimeError("should not fetch")))

    v = ed.valuation_metrics("005930.KS")

    assert v["per"] == 11.0
    assert v["source"] == "DART+marcap"


def test_earnings_data_kr_falls_back_to_yfinance(monkeypatch):
    from providers import earnings_data as ed
    from providers import kr_fundamentals as kf

    class FakeTicker:
        info = {"trailingPE": 9.0, "priceToBook": 1.1, "returnOnEquity": 0.12}
        dividends = []

    monkeypatch.setattr(kf, "recent_annual_metrics", lambda t: {
        "market_type": "kr", "confidence": "missing", "error": "DART_API_KEY 미설정"
    })
    monkeypatch.setattr(ed, "_ticker", lambda s: FakeTicker())

    v = ed.valuation_metrics("005930.KS")

    assert v["market_type"] == "kr"
    assert v["per"] == 9.0
    assert v["pbr"] == 1.1
    assert v["roe"] == 0.12
