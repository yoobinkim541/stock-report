import os
import sys
import json
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import market_report as mr


def test_load_portfolio_tickers_uses_snapshot_holdings(tmp_path):
    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({
        "overseas_general": {"holdings_usd": [
            {"ticker": "ORCL", "shares": 6, "value_usd": 1400},
            {"ticker": "SGOV", "shares": 20, "value_usd": 2009},
        ]},
        "overseas_fractional": {"holdings_usd": []},
    }), encoding="utf-8")

    assert mr.load_portfolio_tickers(str(snap_path)) == ["ORCL", "SGOV"]


def test_section_2_includes_recent_source_cache_digest(monkeypatch):
    monkeypatch.setattr(mr, "fetch_saveticker_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(mr, "fetch_arca_stock_posts", lambda: [])
    monkeypatch.setattr(mr.requests, "get", lambda *args, **kwargs: type("Resp", (), {"status_code": 500, "text": ""})())
    monkeypatch.setattr(mr.yf, "Ticker", lambda sym: type("Ticker", (), {"news": []})())

    monkeypatch.setattr(mr, "load_cached_source_digest", lambda: "## 누적 수집 자료\n\n- saveticker 1건\n- [saveticker] AI chip demand · NVDA\n")

    text = mr.section_2_top_news()

    assert "누적 수집 자료" in text
    assert "AI chip demand" in text


def test_price_str_formats_kospi_as_index_points():
    assert mr.price_str(8051.33, "^KS11") == "8,051.33pt"
    assert mr.price_str(1512.75, "KRW=X") == "₩1,512.75"
    assert mr.price_str(747.71, "SPY") == "$747.71"


def test_section_7_uses_reference_language_and_skips_non_equity(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=80, freq="B")
    closes = pd.Series([100 + i for i in range(80)], index=dates)
    hist = pd.DataFrame({"Close": closes})

    class FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, period="6mo"):
            return hist

    monkeypatch.setattr(mr, "PORTFOLIO_TICKERS", ["SGOV", "ORCL"])
    monkeypatch.setattr(mr.yf, "Ticker", lambda sym: FakeTicker(sym))

    text = mr.section_7_buy_sell_signals()

    assert "참고 기술 신호" in text
    assert "역할형/현금성 ETF는 RSI 매매판정 제외: SGOV" in text
    assert "매수 기회 구간" not in text
    assert "매도 신호 구간" not in text


def test_section_10_uses_real_calendar_not_generic(monkeypatch):
    from providers import econ_calendar

    monkeypatch.setattr(econ_calendar, "upcoming_events", lambda *args, **kwargs: [])

    text = mr.section_10_economic_calendar()

    assert "실제 캘린더" in text
    assert "매월 둘째주" not in text
    assert "API 실패 시 일반 반복 일정을 오늘 일정처럼 표시하지 않습니다" in text


def test_section_8_major_investors_is_appendix_reference():
    text = mr.section_8_major_investors()

    assert "부록 A" in text
    assert "당일 신호가 아니라" in text
    assert "최근 동향" not in text
