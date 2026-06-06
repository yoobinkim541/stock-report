import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml import data_sources


def test_fetch_price_history_uses_stooq_then_clips_as_of(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
    calls = []

    def fake_stooq(ticker, start, end):
        calls.append(("stooq", ticker, start, end))
        return frame

    def fake_yahoo(ticker, start, end):
        calls.append(("yahoo", ticker, start, end))
        return pd.DataFrame()

    monkeypatch.setattr(data_sources, "_stooq_fetcher", fake_stooq)
    monkeypatch.setattr(data_sources, "_yahoo_fetcher", fake_yahoo)
    out = data_sources.fetch_price_history("QQQ", start="2024-01-01", end="2024-01-04", as_of="2024-01-02")
    assert list(out["close"]) == [1.0, 2.0]
    assert calls == [("stooq", "QQQ", "2024-01-01", "2024-01-04")]


def test_fetch_price_history_falls_back_to_yahoo(monkeypatch):
    idx = pd.date_range("2024-01-01", periods=1, freq="D")
    yahoo_frame = pd.DataFrame({"close": [10.0]}, index=idx)

    monkeypatch.setattr(data_sources, "_stooq_fetcher", lambda ticker, start, end: pd.DataFrame())
    monkeypatch.setattr(data_sources, "_yahoo_fetcher", lambda ticker, start, end: yahoo_frame)
    out = data_sources.fetch_close("SPY", start="2024-01-01", end="2024-01-02")
    assert out.name == "SPY"
    assert out.iloc[0] == 10.0


def test_placeholders_raise_clear_errors():
    with pytest.raises(NotImplementedError):
        data_sources.fetch_fred_series("DFF")
    with pytest.raises(NotImplementedError):
        data_sources.fetch_cboe_putcall()
