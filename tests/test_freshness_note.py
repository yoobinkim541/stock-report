#!/usr/bin/env python3
"""test_freshness_note.py — 대시보드 신선도 한 줄 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers.market_data as md
import providers.realtime_quotes as rq


def test_yfinance_seconds(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)
    s = md.freshness_note(1000.0, now=1005.0)
    assert "5초 전" in s and "yfinance" in s and "KST" in s


def test_minutes_format(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)
    s = md.freshness_note(1000.0, now=1000.0 + 185)
    assert "3분 전" in s


def test_realtime_on(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "heartbeat_age", lambda: 3.0)
    assert "실시간 ON(3초)" in md.freshness_note(1000.0, now=1010.0)


def test_realtime_waiting_when_stale_or_none(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: True)
    monkeypatch.setattr(rq, "heartbeat_age", lambda: None)
    assert "실시간 대기" in md.freshness_note(1000.0, now=1001.0)
    monkeypatch.setattr(rq, "heartbeat_age", lambda: 9999.0)   # heartbeat 오래됨
    assert "실시간 대기" in md.freshness_note(1000.0, now=1001.0)


def test_missing_ts(monkeypatch):
    monkeypatch.setattr(rq, "enabled", lambda: False)
    assert "? KST (?)" in md.freshness_note(None, now=1000.0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
