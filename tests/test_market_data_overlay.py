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


def test_fetch_qqq_realtime_overlay(monkeypatch):
    """fetch_qqq_data: 실시간 신선시 current 를 실시간가로 교체, 아니면 yfinance 종가."""
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=70, freq="D")
    df = pd.DataFrame({"High": [100.0] * 70, "Low": [90.0] * 70, "Close": [95.0] * 70}, index=idx)
    monkeypatch.setattr(md, "_history_cached", lambda *a, **k: df)
    monkeypatch.setattr(md, "_update_drawdown_anchor", lambda h, c: h)   # 파일 I/O 회피

    monkeypatch.setattr(md, "_realtime_current", lambda s: 98.0)         # 실시간 오버레이
    assert md.fetch_qqq_data()["current"] == 98.0

    monkeypatch.setattr(md, "_realtime_current", lambda s: None)         # 실시간 없음 → 종가
    assert md.fetch_qqq_data()["current"] == 95.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
