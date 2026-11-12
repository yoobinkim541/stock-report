"""주문 화면 티커 한 줄. 네트워크 없음."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard.ticker_strip import ticker_strip_text


def test_strip_uses_won_for_domestic_and_dollar_for_us():
    kr = ticker_strip_text("005930.KS", 70200)
    us = ticker_strip_text("MSFT", 410.5)
    assert "005930.KS" in kr and "₩70,200" in kr
    assert "MSFT" in us and "$410.50" in us
    assert "Microsoft" in us


def test_strip_preserves_macro_units_and_normalizes_domestic_code():
    assert ticker_strip_text("^GSPC", 6000).endswith("6,000.00 pt")
    assert ticker_strip_text("^VIX", 20).endswith("· 20.00")
    assert ticker_strip_text("GC=F", 3000).endswith("3,000.00 $/oz")
    assert ticker_strip_text("^TNX", 4.25).endswith("4.25%")
    assert ticker_strip_text("005930", 70200).endswith("005930.KS · ₩70,200")
