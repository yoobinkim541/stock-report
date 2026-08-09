from __future__ import annotations

import pandas as pd
import pytest

from dashboard import chart_series


def test_series_specs_enforce_primary_and_unique_ids():
    specs = chart_series.normalize_series_specs(
        [{"id": "peer", "kind": "peer", "symbol": "MSFT"}], primary_symbol="AAPL",
    )
    assert specs[0]["id"] == "primary"
    assert specs[0]["symbol"] == "AAPL"
    assert specs[1]["id"] == "peer"

    with pytest.raises(ValueError, match="duplicate series id"):
        chart_series.normalize_series_specs(
            [
                {"id": "primary", "kind": "price", "symbol": "AAPL"},
                {"id": "primary", "kind": "peer", "symbol": "MSFT"},
            ],
            primary_symbol="AAPL",
        )


def test_load_series_routes_price_fundamental_and_portfolio_without_zero_filling():
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=idx)
    fundamentals = {
        "quarterly": [
            {"date": "2025-06-30", "revenue": 100.0},
            {"date": "2025-09-30", "revenue": 120.0},
        ],
    }
    nav = pd.Series([1000.0, 1010.0], index=idx[:2])

    price = chart_series.load_series(
        {"kind": "peer", "symbol": "MSFT"},
        price_loader=lambda symbol: prices,
        fundamental_loader=lambda symbol: fundamentals,
        nav_loader=lambda spec: nav,
    )
    revenue = chart_series.load_series(
        {"kind": "fundamental", "symbol": "MSFT", "metric": "revenue"},
        price_loader=lambda symbol: prices,
        fundamental_loader=lambda symbol: fundamentals,
        nav_loader=lambda spec: nav,
    )
    portfolio = chart_series.load_series(
        {"kind": "portfolio", "symbol": "PAPER"},
        price_loader=lambda symbol: prices,
        fundamental_loader=lambda symbol: fundamentals,
        nav_loader=lambda spec: nav,
    )
    missing = chart_series.load_series(
        {"kind": "fundamental", "symbol": "MSFT", "metric": "net_income"},
        price_loader=lambda symbol: prices,
        fundamental_loader=lambda symbol: fundamentals,
        nav_loader=lambda spec: nav,
    )

    assert price.equals(prices["Close"])
    assert list(revenue.index) == list(pd.to_datetime(["2025-06-30", "2025-09-30"]))
    assert revenue.tolist() == [100.0, 120.0]
    assert portfolio.equals(nav)
    assert missing is None


@pytest.mark.parametrize("error", [RuntimeError("provider unavailable"), ValueError("bad payload")])
def test_optional_loader_failure_returns_none(error):
    def fail(*args, **kwargs):
        raise error

    assert chart_series.load_series(
        {"kind": "benchmark", "symbol": "QQQ"},
        price_loader=fail,
        fundamental_loader=fail,
        nav_loader=fail,
    ) is None


def test_visible_normalization_anchors_all_price_series_at_common_zero():
    primary_idx = pd.date_range("2026-01-01", periods=6, freq="D")
    benchmark_idx = pd.date_range("2026-01-03", periods=5, freq="D")
    primary = pd.Series([100, 102, 104, 106, 108, 110], index=primary_idx, dtype=float)
    benchmark = pd.Series([50, 51, 52, 53, 54], index=benchmark_idx, dtype=float)

    out = chart_series.normalize_visible_series(primary, {"QQQ": benchmark}, view_days=90)
    common = out["primary"].dropna().index.intersection(out["QQQ"].dropna().index)[0]

    assert out["primary"].loc[common] == pytest.approx(0.0)
    assert out["QQQ"].loc[common] == pytest.approx(0.0)
    assert primary.iloc[0] == 100


def test_series_export_outer_joins_sparse_sources_without_fabricating_values():
    primary = pd.Series([10.0, 11.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    revenue = pd.Series([100.0], index=pd.to_datetime(["2025-12-31"]))

    out = chart_series.series_export_frame(primary, {"revenue": revenue})

    assert list(out.columns) == ["primary", "revenue"]
    assert pd.isna(out.loc[pd.Timestamp("2025-12-31"), "primary"])
    assert pd.isna(out.loc[pd.Timestamp("2026-01-01"), "revenue"])
