#!/usr/bin/env python3
"""test_holding_realtime.py — /holding 목록 실시간 수익률 오버레이 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import holding_manager as hm
import providers.realtime_quotes as rq


def test_disabled_uses_snapshot(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)
    ret, live = hm._rt_ret({"ticker": "MSFT", "avg_price_usd": 100, "return_pct": 5.0})
    assert ret == 5.0 and live is False


def test_realtime_recomputes_return(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda s, **k: 110.0)
    ret, live = hm._rt_ret({"ticker": "MSFT", "avg_price_usd": 100.0, "return_pct": 5.0})
    assert ret == pytest.approx(10.0) and live is True       # (110-100)/100, 스냅샷 5% 무시


def test_no_avg_falls_back(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda s, **k: 110.0)
    ret, live = hm._rt_ret({"ticker": "X", "return_pct": 3.0})   # avg 없음
    assert ret == 3.0 and live is False


def test_no_realtime_price_falls_back(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda s, **k: None)
    ret, live = hm._rt_ret({"ticker": "MSFT", "avg_price_usd": 100.0, "return_pct": 5.0})
    assert ret == 5.0 and live is False


def test_never_raises(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    ret, live = hm._rt_ret({"ticker": "MSFT", "avg_price_usd": 100.0, "return_pct": 7.0})
    assert ret == 7.0 and live is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
