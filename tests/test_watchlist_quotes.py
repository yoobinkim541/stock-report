"""tests/test_watchlist_quotes.py — 관심종목 시세 오버레이 (감사 후속).

관심종목 표는 티커/사유/추가일뿐 가격이 전혀 없어 "정보가 없다"는 불만의
핵심 원인이었다. 실시간 스트림은 관심종목을 구독하지 않으므로(dashboard/data.py
load_watchlist 주석) yfinance 배치 다운로드로 가볍게 가격·등락률을 채운다.
"""
from __future__ import annotations

import pandas as pd
import pytest

import providers.market_data as md


def _multi_ticker_close_frame():
    idx = pd.to_datetime(["2026-08-13", "2026-08-14"])
    cols = pd.MultiIndex.from_product([["Close"], ["PLTR", "NVDA"]])
    df = pd.DataFrame([[28.0, 180.0], [30.0, 176.0]], index=idx, columns=cols)
    return df


def test_batch_quote_change_returns_price_and_pct(monkeypatch):
    monkeypatch.setattr(md.yf, "download", lambda *a, **k: _multi_ticker_close_frame())
    out = md.batch_quote_change(["PLTR", "NVDA"])
    assert out["PLTR"]["price"] == pytest.approx(30.0)
    assert out["PLTR"]["chg_pct"] == pytest.approx((30.0 / 28.0 - 1) * 100)
    assert out["NVDA"]["price"] == pytest.approx(176.0)
    assert out["NVDA"]["chg_pct"] == pytest.approx((176.0 / 180.0 - 1) * 100)


def test_batch_quote_change_single_ticker_flat_columns(monkeypatch):
    idx = pd.to_datetime(["2026-08-13", "2026-08-14"])
    df = pd.DataFrame({"Close": [100.0, 105.0]}, index=idx)
    monkeypatch.setattr(md.yf, "download", lambda *a, **k: df)
    out = md.batch_quote_change(["ORCL"])
    assert out["ORCL"]["price"] == pytest.approx(105.0)
    assert out["ORCL"]["chg_pct"] == pytest.approx(5.0)


def test_batch_quote_change_empty_input_makes_no_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("빈 입력인데 네트워크 호출 발생")
    monkeypatch.setattr(md.yf, "download", _boom)
    assert md.batch_quote_change([]) == {}
    assert md.batch_quote_change(None) == {}


def test_batch_quote_change_graceful_on_empty_frame(monkeypatch):
    monkeypatch.setattr(md.yf, "download", lambda *a, **k: pd.DataFrame())
    assert md.batch_quote_change(["ZZZZ"]) == {}


def test_batch_quote_change_graceful_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(md.yf, "download", _boom)
    assert md.batch_quote_change(["PLTR"]) == {}


def test_batch_quote_change_skips_missing_ticker_column(monkeypatch):
    """요청한 티커 일부가 응답에 없어도(상장폐지 등) 나머지는 정상 반환."""
    idx = pd.to_datetime(["2026-08-13", "2026-08-14"])
    cols = pd.MultiIndex.from_product([["Close"], ["PLTR"]])
    df = pd.DataFrame([[28.0], [30.0]], index=idx, columns=cols)
    monkeypatch.setattr(md.yf, "download", lambda *a, **k: df)
    out = md.batch_quote_change(["PLTR", "DEADCO"])
    assert set(out.keys()) == {"PLTR"}
