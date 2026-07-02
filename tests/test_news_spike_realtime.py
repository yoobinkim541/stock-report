#!/usr/bin/env python3
"""test_news_spike_realtime.py — 속보 실시간 시세 동반표시 (무네트워크·부가)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import news_spike_detector as N
import providers.realtime_quotes as rq


def test_tag_when_enabled_and_fresh(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda s, **k: {"AAPL": 283.78, "MSFT": 372.97}.get(s))
    tag = N._realtime_tag(["AAPL", "MSFT"])
    assert "AAPL $283.78" in tag and "MSFT $372.97" in tag and tag.startswith("  📈")


def test_tag_empty_when_disabled(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)
    assert N._realtime_tag(["AAPL"]) == ""


def test_tag_empty_no_tickers(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    assert N._realtime_tag([]) == ""


def test_tag_never_raises(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(rq, "get_price", _boom)
    assert N._realtime_tag(["AAPL"]) == ""        # 예외 → "" (속보 차단 안 함)


def test_tag_skips_unpriced(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "get_price", lambda s, **k: 100.0 if s == "AAPL" else None)
    tag = N._realtime_tag(["AAPL", "ZZZZ"])
    assert "AAPL $100.00" in tag and "ZZZZ" not in tag


def test_portfolio_set_derived_from_universe_not_hardcoded():
    """중요도 판정용 _PORTFOLIO 가 portfolio_universe 파생인지 (하드코딩 금지·CLAUDE.md·감사 확정)."""
    from portfolio_universe import load_portfolio_tickers
    expected = {t.split(".")[0].upper() for t in load_portfolio_tickers()}
    assert N._PORTFOLIO == expected      # 하드코딩 재도입 시 실패


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
