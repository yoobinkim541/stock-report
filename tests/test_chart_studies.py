from __future__ import annotations

import math

import pandas as pd
import pytest

from dashboard import chart_studies


@pytest.fixture
def hist():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    close = pd.Series(range(100, 140), index=idx, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": range(1000, 1040),
        },
        index=idx,
    )


def test_study_catalog_is_registered_data_not_runtime_source():
    catalog = chart_studies.study_catalog()
    ids = {definition.id for definition in catalog}

    assert {"sma", "ema", "bollinger", "rsi", "macd", "volume", "vwap"} <= ids
    assert all(callable(definition.compute) for definition in catalog)
    assert all("type" in spec and "default" in spec for definition in catalog for spec in definition.parameters.values())


def test_study_registry_rejects_unknown_and_invalid_parameters(hist):
    with pytest.raises(ValueError, match="unknown study"):
        chart_studies.run_study("python_eval", hist, {})
    with pytest.raises(ValueError, match="period"):
        chart_studies.run_study("sma", hist, {"period": 0})
    with pytest.raises(ValueError, match="unknown parameter"):
        chart_studies.run_study("sma", hist, {"source_code": "lambda x: x"})


def test_registered_studies_return_typed_outputs(hist):
    sma = chart_studies.run_study("sma", hist, {"period": 5})
    rsi = chart_studies.run_study("rsi", hist)

    assert sma.placement == "overlay"
    assert sma.series["SMA 5"].iloc[-1] == pytest.approx(137.0)
    assert rsi.placement == "bottom"
    assert set(rsi.series) == {"RSI 14"}
    assert sma.metadata["study_id"] == "sma"


def test_strategy_preview_accepts_only_safe_plot_event_data():
    preview = {
        "plots": [
            {
                "name": "Signal MA",
                "dates": ["2026-01-01", "2026-01-02"],
                "values": [100.0, 101.5],
                "placement": "overlay",
            },
        ],
        "events": [{"date": "2026-01-02", "kind": "entry", "price": 101.5, "label": "Buy"}],
        "metadata": {"strategy": "safe-preview", "version": 1},
    }

    out = chart_studies.study_output_from_strategy_preview(preview)

    assert out.series["Signal MA"].iloc[-1] == pytest.approx(101.5)
    assert out.events[0]["kind"] == "entry"
    assert out.metadata["strategy"] == "safe-preview"


@pytest.mark.parametrize(
    "preview",
    [
        {"plots": [], "events": [], "metadata": {}, "code": "import os"},
        {"plots": [{"name": "x", "dates": ["2026-01-01"], "values": [1, 2], "placement": "overlay"}]},
        {"plots": [{"name": "x", "dates": ["2026-01-01"], "values": [math.inf], "placement": "overlay"}]},
        {"plots": [], "metadata": {"source": "lambda x: x"}},
    ],
)
def test_strategy_preview_rejects_code_shapes_and_invalid_values(preview):
    with pytest.raises(ValueError):
        chart_studies.study_output_from_strategy_preview(preview)
