#!/usr/bin/env python3
"""test_price_alerts_realtime.py — _spot_price 실시간 우선·yfinance 폴백 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot.price_alerts as pa
import providers.realtime_quotes as rq


class _Col:
    def __init__(self, p):
        self.iloc = [p]


class _Hist:
    def __init__(self, price):
        self.empty = price is None
        self._p = price

    def __getitem__(self, key):
        return _Col(self._p)


class _Ticker:
    _price = None

    def __init__(self, t):
        pass

    def history(self, period="1d"):
        return _Hist(_Ticker._price)


def _fake_yf(price):
    _Ticker._price = price
    return type("YF", (), {"Ticker": _Ticker})


def test_realtime_preferred_when_fresh(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda s, **k: 150.0 if s == "AAPL" else None)

    def _boom(*a, **k):
        raise AssertionError("실시간 신선인데 yfinance 호출됨")
    monkeypatch.setattr(pa, "yf", type("YF", (), {"Ticker": _boom}))

    assert pa._spot_price("AAPL") == 150.0


def test_falls_back_to_yfinance_when_realtime_none(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda s, **k: None)   # stale/없음
    monkeypatch.setattr(pa, "yf", _fake_yf(201.3))
    assert pa._spot_price("MSFT") == 201.3


def test_disabled_uses_yfinance(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("비활성인데 실시간 조회됨")
    monkeypatch.setattr(rq, "get_price", _boom)
    monkeypatch.setattr(pa, "yf", _fake_yf(99.0))
    assert pa._spot_price("ORCL") == 99.0


def test_kr_suffix_stripped_for_cache_lookup(monkeypatch):
    seen = {}
    monkeypatch.setattr(rq, "enabled", lambda: True)

    def _gp(s, **k):
        seen["sym"] = s
        return 71000.0
    monkeypatch.setattr(rq, "get_price", _gp)
    assert pa._spot_price("005930.KS") == 71000.0
    assert seen["sym"] == "005930"      # .KS 접미 제거 후 캐시키 조회


def test_all_sources_fail_returns_none(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)
    monkeypatch.setattr(pa, "yf", _fake_yf(None))   # 빈 히스토리
    assert pa._spot_price("XYZ") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
