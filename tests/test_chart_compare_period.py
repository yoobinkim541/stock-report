"""Bounded comparison history must preserve the main chart's pannable window."""
from __future__ import annotations

import pandas as pd
import pytest

from dashboard import charts
from dashboard.chart_workspace_ui import compare_history_period


@pytest.mark.parametrize("frequency", ["B", "D"])
@pytest.mark.parametrize("view_days", [90, 180, 365, 1825, None])
def test_bounded_compare_retains_main_pan_window(frequency, view_days):
    index = pd.date_range("1980-01-01", "2026-11-01", freq=frequency)
    full = pd.Series(range(1, len(index) + 1), index=index, dtype=float)
    period = compare_history_period(view_days)
    bounded = full if period == "max" else full.loc[index[-1] - pd.DateOffset(years=int(period[:-1])):]
    expected = charts.view_window(full, view_days)
    actual = charts.view_window(bounded, view_days)
    pd.testing.assert_series_equal(actual, expected)


def test_ticker_compare_fetch_returns_prices_without_changing_timeframe(monkeypatch):
    from dashboard.pages import ticker as page

    frame = pd.DataFrame({"Close": [100., 110.]}, index=pd.date_range("2026-01-01", periods=2))
    requests = []

    def daily(symbol, period):
        requests.append((symbol, period))
        return frame

    def intraday(symbol, timeframe):
        requests.append((symbol, timeframe))
        return frame

    monkeypatch.setattr(page.cached, "ohlc", daily)
    monkeypatch.setattr(page.cached, "ohlc_tf", intraday)
    pd.testing.assert_frame_equal(page._compare_close("AAPL", "1d", 90), frame)
    pd.testing.assert_frame_equal(page._compare_close("MSFT", "1wk", 90), frame)
    assert requests == [("AAPL", "5y"), ("MSFT", "1wk")]
