from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import chart_analysis as ca  # noqa: E402


def _hist(n=260, start=100.0, step=0.2):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [x + 1 for x in close],
            "Low": [x - 1 for x in close],
            "Close": close,
            "Volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


def test_seasonality_summary_returns_month_rows():
    out = ca.seasonality_summary(_hist(520))
    assert out["ok"] is True
    assert len(out["months"]) == 12
    assert {"month", "avg_return", "win_rate", "sample"} <= set(out["months"][0])


def test_relative_strength_summary_classifies_quadrant():
    hist = _hist(step=0.4)
    bench = _hist(step=0.1)
    out = ca.relative_strength_summary(hist, bench)
    assert out["ok"] is True
    assert out["quadrant"] in {"leading", "weakening", "lagging", "improving"}


def test_pattern_candidates_handles_short_data():
    assert ca.pattern_candidates(_hist(20)) == []


def test_pattern_candidates_detects_channel_breakout():
    hist = _hist(90, start=100, step=0.05)
    hist.iloc[-1, hist.columns.get_loc("Close")] = float(hist["High"].iloc[-30:-1].max()) + 2
    hist.iloc[-1, hist.columns.get_loc("High")] = float(hist["Close"].iloc[-1]) + 1

    out = ca.pattern_candidates(hist)

    assert any(row["kind"] == "channel_breakout" and row["direction"] == "up" for row in out)


def test_multi_timeframe_summary_reports_rows():
    out = ca.multi_timeframe_summary(lambda ticker, tf: _hist(120), "MSFT")

    assert out["ok"] is True
    assert out["ticker"] == "MSFT"
    assert [row["timeframe"] for row in out["rows"]] == ["5m", "1h", "1d", "1wk"]
