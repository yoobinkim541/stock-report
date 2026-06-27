#!/usr/bin/env python3
"""test_realtime_quotes.py — 실시간 캐시 reader seam (무네트워크·폐형해)."""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers.realtime_quotes as rq


def _write_cache(tmp_path, symbols: dict, *, hb_age: float = 0.0):
    now = time.time()
    cache = {"__heartbeat__": {"ts": now - hb_age, "connected": True}}
    cache.update(symbols)
    p = tmp_path / "rt.json"
    p.write_text(json.dumps(cache), encoding="utf-8")
    return str(p)


@pytest.fixture
def live(monkeypatch, tmp_path):
    """REALTIME on + CACHE_PATH 리다이렉트 헬퍼 반환."""
    monkeypatch.setenv("REALTIME_ENABLED", "true")

    def setup(symbols, *, hb_age=0.0):
        monkeypatch.setattr(rq, "CACHE_PATH", _write_cache(tmp_path, symbols, hb_age=hb_age))
    return setup


# ── 순수 신선도 ───────────────────────────────────────────────────────────────

def test_is_fresh_boundaries():
    now = 1000.0
    assert rq._is_fresh(995.0, now, 60) is True       # 5s old
    assert rq._is_fresh(940.0, now, 60) is True        # 정확히 60s
    assert rq._is_fresh(939.0, now, 60) is False       # 61s → stale
    assert rq._is_fresh(None, now, 60) is False
    assert rq._is_fresh("bad", now, 60) is False
    assert rq._is_fresh(1005.0, now, 60) is False      # 미래 과다 → 거부


# ── 게이트 / 폴백 ─────────────────────────────────────────────────────────────

def test_disabled_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    monkeypatch.setattr(rq, "CACHE_PATH",
                        _write_cache(tmp_path, {"AAPL": {"price": 200, "ts": time.time()}}))
    assert rq.get_price("AAPL") is None


def test_fresh_price_returned(live):
    live({"AAPL": {"price": 201.5, "volume": 1000, "ts": time.time()}})
    assert rq.get_price("AAPL") == 201.5
    assert rq.get_volume("AAPL") == 1000.0
    assert rq.is_fresh("AAPL") is True


def test_stale_symbol_none(live):
    live({"AAPL": {"price": 201.5, "ts": time.time() - 1000}})
    assert rq.get_price("AAPL") is None
    assert rq.is_fresh("AAPL") is False


def test_heartbeat_stale_wholesale_fallback(live):
    # 심볼은 신선하지만 heartbeat 오래됨(프로세스 죽음) → 캐시 전체 불신
    live({"AAPL": {"price": 201.5, "ts": time.time()}}, hb_age=999)
    assert rq.get_price("AAPL") is None


def test_missing_symbol_none(live):
    live({"AAPL": {"price": 201.5, "ts": time.time()}})
    assert rq.get_price("MSFT") is None


def test_best_buy_ask_sell_bid(live):
    live({"005930": {"price": 71000, "best_ask": 71100, "best_bid": 70900,
                     "asks": [[71100, 5]], "bids": [[70900, 7]], "ts": time.time()}})
    assert rq.best("005930", "buy") == 71100.0    # 매수는 ask
    assert rq.best("005930", "sell") == 70900.0   # 매도는 bid
    ob = rq.get_orderbook("005930")
    assert ob["best_ask"] == 71100 and ob["best_bid"] == 70900


def test_corrupt_cache_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("REALTIME_ENABLED", "true")
    p = tmp_path / "bad.json"
    p.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(rq, "CACHE_PATH", str(p))
    assert rq.get_price("AAPL") is None            # 예외 없이 None
    assert rq.get_orderbook("AAPL") is None
    assert rq.heartbeat_age() is None


def test_heartbeat_age(live):
    live({"AAPL": {"price": 1, "ts": time.time()}}, hb_age=5)
    age = rq.heartbeat_age()
    assert age is not None and 3 < age < 8


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
