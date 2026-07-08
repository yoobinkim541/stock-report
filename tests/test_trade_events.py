#!/usr/bin/env python3
"""test_trade_events.py — chart trade-event ledger."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import trade_events as T  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_DB", str(tmp_path / "stock_report.db"))
    import store
    store._initialized.clear()
    return tmp_path


def test_record_trade_dedupes_and_lists_by_ticker(isolated_db):
    rec = T.record_trade(
        ticker="NVDA", side="buy", qty=2, price=100, avg_price=100,
        account="manual", source="manual_holding", timestamp="2026-07-07T09:30:00",
        event_id="manual-1")
    T.record_trade(
        ticker="NVDA", side="buy", qty=2, price=100, avg_price=100,
        account="manual", source="manual_holding", timestamp="2026-07-07T09:30:00",
        event_id="manual-1")

    rows = T.trades_for_ticker("NVDA")
    assert len(rows) == 1
    assert rows[0]["event_id"] == rec["event_id"]
    assert rows[0]["qty"] == 2


def test_ticker_matching_handles_kr_suffix(isolated_db):
    T.record_trade(
        ticker="005930.KS", side="sell", qty=3, price=70000,
        account="domestic", source="kiwoom_sync", market="KR",
        timestamp="2026-07-07T15:30:00", event_id="kr-1")
    assert T.trades_for_ticker("005930")[0]["ticker"] == "005930.KS"
    assert T.trades_for_ticker("005930.KS")[0]["symbol"] == "005930"


def test_include_mock_filter(isolated_db):
    T.record_trade(
        ticker="MSFT", side="buy", qty=1, price=420,
        account="us_mock", source="kis_mock", timestamp="2026-07-07T10:00:00",
        event_id="mock-1")
    T.record_trade(
        ticker="MSFT", side="buy", qty=1, price=421,
        account="manual", source="manual_holding", timestamp="2026-07-07T11:00:00",
        event_id="manual-2")
    assert len(T.trades_for_ticker("MSFT")) == 2
    rows = T.trades_for_ticker("MSFT", include_mock=False)
    assert len(rows) == 1
    assert rows[0]["source"] == "manual_holding"


def test_holding_manager_buy_sell_records_chart_events(isolated_db, tmp_path, monkeypatch):
    import json
    import holding_manager as H

    snap = tmp_path / "portfolio_snapshot.json"
    snap.write_text(json.dumps({
        "overseas_general": {"holdings_usd": []},
        "overseas_fractional": {"holdings": []},
    }), encoding="utf-8")
    monkeypatch.setattr(H, "PORTFOLIO_PATH", str(snap))
    monkeypatch.setattr(H, "refresh_portfolio_prices", lambda: "가격 갱신 생략")

    H.buy_holding("MSFT", 2, 400.0, note="DCA 매주 ₩100,000")
    H.sell_holding("MSFT", 1, price_usd=420.0)

    rows = T.trades_for_ticker("MSFT", include_mock=False)
    assert [r["side"] for r in rows] == ["buy", "sell"]
    assert rows[0]["avg_price"] == 400.0
    assert rows[0]["note"] == "DCA 매주 ₩100,000"
    assert rows[1]["price"] == 420.0


def test_kiwoom_sync_position_delta_records_api_trade(isolated_db, tmp_path, monkeypatch):
    import json

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crons"))
    import kiwoom_sync_rest as K

    snap = tmp_path / "portfolio_snapshot.json"
    snap.write_text(json.dumps({
        "last_domestic_sync": "2026-07-06T15:40:00",
        "domestic": {"holdings": [
            {"ticker": "005930", "name": "삼성전자", "shares": 10, "avg_price_krw": 70000,
             "current_price_krw": 72000, "value_krw": 720000, "return_pct": 2.8},
        ]},
    }), encoding="utf-8")
    monkeypatch.setattr(K, "PORTFOLIO_PATH", str(snap))

    K.update_portfolio([{
        "ticker": "005930", "name": "삼성전자", "shares": 12,
        "avg_price_krw": 71000, "current_price_krw": 73000,
        "cost_krw": 852000, "value_krw": 876000, "pnl_krw": 24000, "return_pct": 2.8,
    }])

    rows = T.trades_for_ticker("005930")
    assert len(rows) == 1
    assert rows[0]["source"] == "kiwoom_sync"
    assert rows[0]["side"] == "buy"
    assert rows[0]["qty"] == 2
    assert rows[0]["confirmed"] is True


def test_normalize_rejects_invalid_trade():
    with pytest.raises(ValueError):
        T.normalize_trade(ticker="MSFT", side="hold", qty=1)
    with pytest.raises(ValueError):
        T.normalize_trade(ticker="MSFT", side="buy", qty=0)
