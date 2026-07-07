#!/usr/bin/env python3
"""test_etf_data.py — ETF 데이터층 순수 로직 (무네트워크)."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers import etf_data as E


def test_premium_pct():
    assert E.premium_pct(55.82, 56.1) == -0.5
    assert E.premium_pct(56.1, 56.1) == 0.0
    assert E.premium_pct(None, 56.1) is None
    assert E.premium_pct(55.0, 0) is None
    assert E.premium_pct("x", "y") is None


def test_dividend_stats_monthly():
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    pairs = [((now - timedelta(days=30 * i)).isoformat(), 0.635) for i in range(12)]
    s = E.dividend_stats(pairs, price=55.66, now=now)
    assert s["count_12m"] == 12 and s["freq_label"] == "매월"
    assert abs(s["per_share_12m"] - 7.62) < 0.01
    assert abs(s["yield_pct"] - 13.69) < 0.05


def test_dividend_stats_excludes_old_and_handles_empty():
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    old = [((now - timedelta(days=400)).isoformat(), 1.0)]
    s = E.dividend_stats(old, price=100.0, now=now)
    assert s["count_12m"] == 0 and s["yield_pct"] is None and s["freq_label"] == "—"
    s2 = E.dividend_stats([], price=None, now=now)
    assert s2["per_share_12m"] == 0.0


def test_dividend_stats_quarterly_label():
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    pairs = [((now - timedelta(days=91 * i)).isoformat(), 0.5) for i in range(4)]
    assert E.dividend_stats(pairs, 100.0, now=now)["freq_label"] == "분기"


def test_parse_top_holdings():
    df = pd.DataFrame({"Name": ["NVIDIA Corp", "Apple Inc"],
                       "Holding Percent": [0.0765, 0.0663]},
                      index=["NVDA", "AAPL"])
    out = E.parse_top_holdings(df)
    assert out == [{"symbol": "NVDA", "name": "NVIDIA Corp", "pct": 7.65},
                   {"symbol": "AAPL", "name": "Apple Inc", "pct": 6.63}]
    assert E.parse_top_holdings(None) == []
    assert E.parse_top_holdings(pd.DataFrame()) == []


def test_is_etf_known_list_and_quote_type():
    assert E.is_etf("QQQI") is True                  # 보유 ETF — 오프라인 폴백
    assert E.is_etf("SGOV") is True
    assert E.is_etf("MSFT") is False
    assert E.is_etf("MSFT", quote_type="ETF") is True
    assert E.is_etf("QQQI", quote_type="EQUITY") is False   # 실판정 우선
