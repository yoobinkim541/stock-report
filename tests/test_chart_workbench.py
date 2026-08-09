from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import chart_document, chart_workbench, chart_workbench_ui  # noqa: E402


def _hist(n=320, *, start=100.0, step=0.2):
    index = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series([start + i * step for i in range(n)], index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1_000,
        },
        index=index,
    )


def test_analysis_snapshot_combines_all_analysis_sections():
    document = chart_document.default_chart_document("MSFT")
    document["source"] = {
        "name": "yfinance",
        "as_of": "2026-08-08T20:00:00Z",
        "freshness": "delayed",
        "quality": "indicative",
    }
    hist = _hist(step=0.4)

    def load_ohlc(symbol, timeframe):
        assert timeframe in {"5m", "1h", "1d", "1wk"}
        return _hist(step=0.1 if symbol == "QQQ" else 0.4)

    out = chart_workbench.build_analysis_snapshot(
        document,
        hist,
        ohlc_loader=load_ohlc,
        fundamental_loader=lambda symbol: {"symbol": symbol, "metrics": {"per": 31.2}},
        alert_loader=lambda symbol: [{"id": "a1", "symbol": symbol, "matched": False}],
    )

    assert out["symbol"] == "MSFT"
    assert out["benchmark"] == "QQQ"
    assert out["relative_strength"]["ok"] is True
    assert out["seasonality"]["ok"] is True
    assert len(out["seasonality"]["months"]) == 12
    assert out["multi_timeframe"]["ok"] is True
    assert len(out["multi_timeframe"]["rows"]) == 4
    assert out["trend"]["count"] >= 1
    assert {"support", "resistance", "channel"} <= set(out["trend"]["by_kind"])
    assert out["fundamentals"]["metrics"]["per"] == 31.2
    assert out["alerts"][0]["id"] == "a1"
    assert out["data_quality"]["source"] == "yfinance"
    assert out["errors"] == {}


def test_analysis_snapshot_uses_kr_benchmark_and_survives_optional_failures():
    document = chart_document.default_chart_document("005930.KS")
    hist = _hist()

    def load_ohlc(symbol, timeframe):
        if symbol == "^KS11":
            return _hist(step=0.1)
        if timeframe == "1h":
            raise RuntimeError("intraday provider unavailable")
        return _hist()

    def fail(_symbol):
        raise RuntimeError("optional provider unavailable")

    out = chart_workbench.build_analysis_snapshot(
        document,
        hist,
        ohlc_loader=load_ohlc,
        fundamental_loader=fail,
        alert_loader=fail,
    )

    assert out["benchmark"] == "^KS11"
    assert out["relative_strength"]["ok"] is True
    assert out["patterns"] == [] or isinstance(out["patterns"], list)
    rows = {row["timeframe"]: row for row in out["multi_timeframe"]["rows"]}
    assert rows["1h"]["ok"] is False
    assert rows["1d"]["ok"] is True
    assert out["fundamentals"] == {}
    assert out["alerts"] == []
    assert set(out["errors"]) == {"fundamentals", "alerts"}


def test_analysis_snapshot_prefers_explicit_benchmark_series():
    document = chart_document.default_chart_document("MSFT")
    document["series"].append(
        {
            "id": "peer",
            "kind": "peer",
            "symbol": "AAPL",
            "axis": "primary",
            "normalization": "visible_start",
            "visible": True,
        }
    )

    seen = []

    def load_ohlc(symbol, timeframe):
        seen.append((symbol, timeframe))
        return _hist()

    out = chart_workbench.build_analysis_snapshot(
        document,
        _hist(),
        ohlc_loader=load_ohlc,
        fundamental_loader=lambda _symbol: {},
        alert_loader=lambda _symbol: [],
    )

    assert out["benchmark"] == "AAPL"
    assert ("AAPL", "1d") in seen


def test_workbench_chart_groups_cover_every_document_chart_type_once():
    flattened = [item for values in chart_workbench_ui.CHART_TYPE_GROUPS.values() for item in values]

    assert set(flattened) == set(chart_document.CHART_TYPES)
    assert len(flattened) == len(set(flattened))


def test_condition_draft_builds_valid_canonical_tree():
    condition = chart_workbench_ui.condition_from_draft(
        symbol="msft",
        timeframe="1h",
        field="close",
        operator="crossing_up",
        value=320.0,
        confirmation="bar_close",
        session="extended",
    )

    assert condition == {
        "op": "all",
        "children": [
            {
                "type": "price",
                "symbol": "MSFT",
                "timeframe": "1h",
                "field": "close",
                "operator": "crossing_up",
                "value": 320.0,
                "confirmation": "bar_close",
                "session": "extended",
            }
        ],
    }


def test_condition_draft_supports_non_price_operands():
    condition = chart_workbench_ui.condition_from_draft(
        symbol="AAPL",
        timeframe="1d",
        field="forward_pe",
        operator="less_than",
        value=25,
        confirmation="bar_close",
        session="regular",
        leaf_type="fundamental",
    )

    assert condition["children"][0]["type"] == "fundamental"
    assert condition["children"][0]["field"] == "forward_pe"
