import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest_multi import build_report, fill_with_scaled_synthetic, make_synthetic, sim_qld_spmo_sgov, sim_spy_schd_top10, run_all


def test_make_synthetic_preserves_shape_and_starts_at_100():
    base = pd.Series([100.0, 110.0, 121.0], index=pd.date_range("2024-01-01", periods=3))

    synth = make_synthetic(base, mult=2.0, annual_drag=0.04)

    assert synth.index.equals(base.index)
    assert len(synth) == len(base)
    assert synth.iloc[0] == 100.0
    assert synth.notna().all()
    assert (synth > 0).all()
    assert synth.iloc[-1] > synth.iloc[0]


def test_fill_with_scaled_synthetic_avoids_price_scale_discontinuity():
    dates = pd.date_range("2024-01-01", periods=4)
    actual = pd.Series([None, 200.0, 220.0, None], index=dates)
    synth = pd.Series([50.0, 100.0, 110.0, 120.0], index=dates)

    filled = fill_with_scaled_synthetic(actual, synth)

    assert filled.iloc[0] == 100.0
    assert filled.iloc[1] == 200.0
    assert filled.iloc[2] == 220.0
    assert filled.iloc[3] == 240.0


def test_build_report_renders_longest_period_chart_without_name_error():
    dates = pd.date_range("2024-01-01", periods=30)
    logs = {
        "①QQQ DCA": [{"date": d.strftime("%Y-%m-%d"), "value": 10000 + i} for i, d in enumerate(dates)],
        "④IB v2.2 VIX": [{"date": d.strftime("%Y-%m-%d"), "value": 10000 + i * 2} for i, d in enumerate(dates)],
    }

    report = build_report({"1년 (2025-01-01~2026-01-01)": ("1년 (2025-01-01~2026-01-01)", logs)})

    assert "1년 포트폴리오 추이" in report


def test_spy_schd_top10_rebalances_to_current_top_market_caps():
    dates = pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31"])
    tickers = ["SPY", "SCHD"] + [f"T{i}" for i in range(1, 12)]
    data = {t: [100.0, 100.0, 100.0] for t in tickers}
    for i in range(1, 12):
        data[f"T{i}_MKT_CAP"] = [1000.0 - i, 1000.0 - i, 1000.0 - i]
    data["T11_MKT_CAP"] = [1.0, 2000.0, 2000.0]
    df = pd.DataFrame(data, index=dates)

    log, holdings = sim_spy_schd_top10(df, return_holdings=True)

    assert len(log) == 3
    assert "SPY" in holdings[dates[0]]
    assert "SCHD" in holdings[dates[0]]
    assert "T11" not in holdings[dates[0]]
    assert "T11" in holdings[dates[1]]


def test_qld_spmo_sgov_uses_sgov_until_qqq_mdd_reaches_minus_10():
    dates = pd.date_range("2024-01-01", periods=3)
    df = pd.DataFrame({
        "QQQ": [100.0, 95.0, 90.0],
        "QLD": [100.0, 90.0, 80.0],
        "SPMO": [100.0, 97.0, 95.0],
        "SGOV": [100.0, 100.1, 100.2],
        "VIX": [18.0, 24.0, 30.0],
        "drawdown": [0.0, -5.0, -10.0],
        "rsi": [55.0, 45.0, 35.0],
        "mom_1m": [1.0, -4.0, -8.0],
    }, index=dates)

    _, allocs = sim_qld_spmo_sgov(df, return_allocations=True)

    assert allocs[dates[1]]["SGOV"] >= 0.70
    assert allocs[dates[1]]["QLD"] == 0.0
    assert allocs[dates[2]]["QLD"] > 0.0


def test_qld_spmo_sgov_increases_qld_as_drawdown_deepens():
    dates = pd.date_range("2024-01-01", periods=4)
    df = pd.DataFrame({
        "QQQ": [100.0, 90.0, 80.0, 70.0],
        "QLD": [100.0, 80.0, 62.0, 45.0],
        "SPMO": [100.0, 92.0, 85.0, 78.0],
        "SGOV": [100.0, 100.1, 100.2, 100.3],
        "VIX": [20.0, 30.0, 35.0, 40.0],
        "drawdown": [0.0, -10.0, -20.0, -30.0],
        "rsi": [55.0, 35.0, 28.0, 22.0],
        "mom_1m": [1.0, -8.0, -15.0, -25.0],
    }, index=dates)

    _, allocs = sim_qld_spmo_sgov(df, return_allocations=True)

    assert allocs[dates[1]]["QLD"] < allocs[dates[2]]["QLD"] < allocs[dates[3]]["QLD"]


def test_run_all_includes_qld_spmo_sgov_strategy():
    dates = pd.date_range("2024-01-01", periods=30)
    df = pd.DataFrame({
        "QQQ": [100.0] * 30,
        "QLD": [100.0] * 30,
        "SPMO": [100.0] * 30,
        "SGOV": [100.0] * 30,
        "SPY": [100.0] * 30,
        "SCHD": [100.0] * 30,
        "TLT": [100.0] * 30,
        "TQQQ": [100.0] * 30,
        "UPRO": [100.0] * 30,
        "TMF": [100.0] * 30,
        "IEF": [100.0] * 30,
        "EFA": [100.0] * 30,
        "GLD": [100.0] * 30,
        "DBC": [100.0] * 30,
        "DBMF": [100.0] * 30,
        "VBR": [100.0] * 30,
        "SHY": [100.0] * 30,
        "VIX": [20.0] * 30,
        "phase": [0] * 30,
    }, index=dates)

    logs = run_all(df)

    assert "⑩QLD/SPMO/SGOV" in logs
    assert len(logs["⑩QLD/SPMO/SGOV"]) == 30
