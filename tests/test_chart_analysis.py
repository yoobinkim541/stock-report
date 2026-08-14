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


def test_multi_timeframe_summary_pullback_within_uptrend_is_mixed_not_down():
    """감사 #33 — trend 분류가 비대칭: up 은 last>=ma20>=ma60 3중 조건을 요구하는데
    down 은 last<ma20 만 보고 ma20 vs ma60 관계를 전혀 확인하지 않았음. 상승추세
    구조(ma20>=ma60)가 여전히 유지되는 단기 눌림목도 무조건 'down' 으로 오분류."""
    hist = _hist(120, step=0.2)
    close_col = hist.columns.get_loc("Close")
    # 마지막 5봉만 큰 폭으로 눌림 — ma20(단기)는 끌려 내려가 last 보다 위로,
    # ma60(장기)는 대부분 상승추세 구간을 반영해 여전히 ma20 아래(상승구조 유지).
    base = float(hist["Close"].iloc[-6])
    for i in range(5):
        hist.iloc[-5 + i, close_col] = base - (i + 1) * 0.8

    out = ca.multi_timeframe_summary(lambda ticker, tf: hist, "MSFT")

    row = out["rows"][0]
    assert row["trend"] == "mixed", f"눌림목(ma20>=ma60 유지)인데 {row['trend']} 로 분류됨"
