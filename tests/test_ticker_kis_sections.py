from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_broker_opinions_section_renders_table():
    """감사 후속 — 증권사별 투자의견 섹션이 예외 없이 렌더되고 데이터를 보여준다."""
    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard.pages import ticker

rows = [
    {{"date": "20260810", "broker": "키움", "opinion": "BUY",
      "target_price": 350000.0, "price_at_opinion": 231000.0, "deviation_pct": -21.57}},
]
ticker._kr_broker_opinions_section(rows)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "증권사별 투자의견" in body


def test_broker_opinions_section_noop_when_empty():
    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard.pages import ticker
ticker._kr_broker_opinions_section(None)
ticker._kr_broker_opinions_section([])
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)


def test_credit_short_section_renders_metrics():
    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard.pages import ticker

credit = [{{"date": "20260815", "loan_balance_rate_pct": 0.39}}]
short = [{{"date": "20260815", "short_ratio_pct": 5.88}}]
ticker._kr_credit_short_section(credit, short)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    assert "신용잔고" in body


def test_credit_short_section_noop_when_both_empty():
    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
from dashboard.pages import ticker
ticker._kr_credit_short_section(None, None)
ticker._kr_credit_short_section([], [])
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
