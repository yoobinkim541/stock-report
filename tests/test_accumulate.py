"""주식 모으기 — order_generator.build() 구조화 + 대시보드 사이드바 레일 (무네트워크)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")


def _patched_og(monkeypatch):
    from bot import order_generator as og
    monkeypatch.setattr(og, "fetch_qqq_data", lambda: {"drawdown_pct": -3.2})
    monkeypatch.setattr(og, "fetch_rsi", lambda t: 55.0)
    monkeypatch.setattr(og, "fetch_vix", lambda: 18.0)
    monkeypatch.setattr(og, "fetch_exchange_rate", lambda: 1400.0)
    monkeypatch.setattr(og, "classify_market", lambda q, r, v: ("neutral", 0))
    monkeypatch.setattr(og, "calculate_dca", lambda mt, pk, fx, drawdown_pct=None: {
        "total_krw": 42_000, "multiplier": 1.0,
        "by_ticker": {"MSFT": 28_000, "NVDA": 14_000}})
    monkeypatch.setattr(og, "fetch_prices", lambda tks: {"MSFT": 400.0, "NVDA": 0})
    return og


def test_build_structured(monkeypatch):
    og = _patched_og(monkeypatch)
    plan = og.build()
    assert plan["total_krw"] == 42_000 and plan["mult"] == 1.0
    by = {r["ticker"]: r for r in plan["rows"]}
    assert by["MSFT"]["qty"] == pytest.approx(28_000 / 1400.0 / 400.0, abs=1e-4)
    assert by["NVDA"]["qty"] is None                       # 가격 조회 실패 행
    assert plan["total_usd"] == pytest.approx(20.0)
    assert plan["fx"] == 1400.0 and "emoji" in plan


def test_generate_formats_from_build(monkeypatch):
    """generate() = build() 포맷팅 레이어 — 주문서 골격·행 포맷 회귀."""
    og = _patched_og(monkeypatch)
    text = og.generate(send=False)
    assert "📋 소수점 매수 주문서" in text
    assert "28,000원" in text and "@$ 400.00" in text
    assert "(가격 조회 실패)" in text                       # NVDA 행
    assert "42,000원" in text and "키움증권" in text


def test_sidebar_rail_apptest():
    from streamlit.testing.v1 import AppTest
    script = f'''
import sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import cached, accumulate
cached.accumulation = lambda: {{"rows": [{{"ticker": "MSFT", "krw_amt": 28000,
    "qty": 0.05, "price": 400.0, "precision_warn": False}}],
    "total_krw": 42000, "mult": 1.5, "emoji": "🟡", "label": "Phase 1",
    "dd": -6.0, "fx": 1400.0, "total_usd": 20.0, "now": "2026-07-08"}}
accumulate.sidebar_rail()
'''
    at = AppTest.from_string(script, default_timeout=15)
    at.run()
    assert not at.exception, at.exception
    body = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "주식 모으기" in body and "42,000원" in body and "1.5×" in body
    assert any("모으기 관리" in str(b.label) for b in at.button)


def test_sidebar_rail_empty_graceful():
    from streamlit.testing.v1 import AppTest
    script = f'''
import sys
sys.path.insert(0, {ROOT!r})
from dashboard import cached, accumulate
cached.accumulation = lambda: {{}}
accumulate.sidebar_rail()
'''
    at = AppTest.from_string(script, default_timeout=15)
    at.run()
    assert not at.exception and not at.button                 # 조용히 생략
