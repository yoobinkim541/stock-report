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


def test_liquidated_sgov_no_ghost(monkeypatch, tmp_path):
    """SGOV/QQQI 전량청산(holdings 에 없음) 시 유령 기본수량 평가가 0 이어야 함 (감사 확정 회귀).

    수정 전엔 holdings.get('SGOV', SGOV_SHARES_DEFAULT) 로 청산 후에도 10주·35.3주 유령 평가액이
    생겨 Phase 4/5 청산 국면서 '없는 SGOV 매도' 반복권고가 났다.
    """
    import json
    import pandas as pd
    snap = {"overseas_general": {"holdings_usd": [
        {"ticker": "MSFT", "shares": 10, "current_price_usd": 400.0, "cost_usd": 3500.0}]}}
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap))
    monkeypatch.setattr(md, "PORTFOLIO_PATH", str(p))
    monkeypatch.setattr(md, "load_leverage_state", lambda: {})
    monkeypatch.setattr(md.yf, "download", lambda *a, **k: pd.DataFrame())   # 네트워크 회피 → 스냅샷 폴백
    monkeypatch.setattr(md, "_realtime_spot_overlay", lambda tickers: {})
    monkeypatch.setattr(md, "_save_last_prices", lambda prices: None)
    monkeypatch.setattr(md, "_load_last_prices", lambda: {})

    out = md.fetch_portfolio_value()
    assert not out.get("data_missing")     # MSFT 보유 → 빈-스냅샷 폴백 아님
    assert out["sgov_usd"] == 0.0          # 청산된 SGOV → 유령 0
    assert out["qqqi_usd"] == 0.0
    assert out["qqqi_shares"] == 0.0
    assert out["total_usd"] == 4000.0      # MSFT 10주 × $400 (스냅샷 폴백가)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
