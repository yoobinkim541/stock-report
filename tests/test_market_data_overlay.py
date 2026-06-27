#!/usr/bin/env python3
"""test_market_data_overlay.py — fetch_portfolio_value 실시간 스팟 오버레이 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers.market_data as md
import providers.realtime_quotes as rq


def test_overlay_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)
    assert md._realtime_spot_overlay(["MSFT", "QQQI"]) == {}


def test_overlay_returns_fresh_only(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    fresh = {"MSFT": 372.0, "NVDA": 0}     # NVDA 0 → 제외
    monkeypatch.setattr(rq, "get_price", lambda s, **k: fresh.get(s))
    out = md._realtime_spot_overlay(["MSFT", "NVDA", "QQQI"])
    assert out == {"MSFT": 372.0}          # 신선·양수만


def test_overlay_kr_suffix_stripped(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    seen = []
    monkeypatch.setattr(rq, "get_price", lambda s, **k: (seen.append(s), 71000.0)[1])
    out = md._realtime_spot_overlay(["005930.KS"])
    assert out == {"005930.KS": 71000.0} and seen == ["005930"]


def test_overlay_never_raises(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("cache error")
    monkeypatch.setattr(rq, "get_price", _boom)
    assert md._realtime_spot_overlay(["MSFT"]) == {}   # 예외 → {} (폴백 보장)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
