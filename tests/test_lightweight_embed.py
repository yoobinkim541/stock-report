from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from dashboard import chart_document, chart_renderer, lightweight_embed


def _hist(size: int = 40) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=size, freq="h", tz="UTC")
    close = np.arange(size, dtype=float) + 100.0
    return pd.DataFrame({
        "Open": close - 0.5,
        "High": close + 1.0,
        "Low": close - 1.0,
        "Close": close,
        "Volume": np.arange(size, dtype=float) + 1_000.0,
    }, index=index)


def _rendered(size: int = 40, *, chart_type: str = "candlestick"):
    document = chart_document.default_chart_document("AAPL")
    document["chart"]["type"] = chart_type
    return chart_renderer.render_plotly_chart(
        document,
        _hist(size),
        chart_kwargs={
            "mas": (5,),
            "show_volume": True,
            "trades": [{
                "event_id": "buy-1", "side": "buy", "date": _hist(size).index[5],
                "price": 105.0, "qty": 2, "source": "paper",
            }],
        },
    )


def test_payload_contains_candles_volume_overlay_markers_and_price_lines():
    rendered = _rendered()
    payload = lightweight_embed.build_payload(rendered)

    assert payload["version"] == 2
    assert payload["symbol"] == "AAPL"
    assert payload["series_type"] == "candlestick"
    assert len(payload["bars"]["time"]) == 40
    assert {key: payload["bars"][key][0] for key in ("time", "open", "high", "low", "close")} == {
        "time": int(_hist().index[0].timestamp()),
        "open": 99.5, "high": 101.0, "low": 99.0, "close": 100.0,
    }
    assert len(payload["volume"]["time"]) == 40
    assert payload["volume"]["value"][0] == 1000.0
    assert any(row["name"] == "MA5" for row in payload["overlays"])
    assert payload["markers"][0]["id"] == "buy-1"
    assert payload["markers"][0]["shape"] == "arrowUp"
    assert any(line["id"] == "tn-last" and line["price"] == 139.0 for line in payload["price_lines"])
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize(
    ("chart_type", "series_type"),
    [
        ("line", "line"),
        ("area", "area"),
        ("baseline", "baseline"),
        ("candlestick", "candlestick"),
        ("hollow_candle", "candlestick"),
        ("heikin_ashi", "candlestick"),
        ("bars", "bar"),
        ("high_low", "bar"),
    ],
)
def test_payload_maps_supported_chart_types(chart_type, series_type):
    assert lightweight_embed.build_payload(_rendered(chart_type=chart_type))["series_type"] == series_type


def test_payload_sorts_deduplicates_filters_nonfinite_and_bounds_tail():
    frame = _hist(20_010)
    duplicate = frame.iloc[[10]].copy()
    duplicate["Close"] = 777.0
    bad = frame.iloc[[11]].copy()
    bad.index = pd.DatetimeIndex([pd.Timestamp("2024-01-01", tz="UTC")])
    bad["High"] = np.inf
    frame = pd.concat([frame.iloc[::-1], duplicate, bad])
    document = chart_document.default_chart_document("AAPL")
    rendered = SimpleNamespace(
        frame=frame,
        document=document,
        figure=go.Figure(),
        transform=SimpleNamespace(x_mode="time"),
    )

    payload = lightweight_embed.build_payload(rendered)
    times = payload["bars"]["time"]

    assert len(times) == 20_000
    assert times == sorted(set(times))
    assert payload["truncated"] is True
    assert payload["source_bar_count"] == 20_011
    assert payload["bars"]["close"][-1] == 20109.0
    json.dumps(payload, allow_nan=False)


def test_payload_rejects_empty_or_sequence_frame():
    rendered = _rendered()
    rendered.frame.drop(rendered.frame.index, inplace=True)
    with pytest.raises(ValueError, match="no finite bars"):
        lightweight_embed.build_payload(rendered)

    sequence = _rendered()
    object.__setattr__(sequence.transform, "x_mode", "sequence")
    with pytest.raises(ValueError, match="time-based"):
        lightweight_embed.build_payload(sequence)


def test_html_pins_library_and_contains_canvas_runtime_contract():
    html = lightweight_embed.lightweight_chart_html(
        lightweight_embed.build_payload(_rendered()),
        height=480,
        store_key="AAPL:1h:lin:candlestick",
        range_sync_key="cw:main:range",
        live=True,
        light=False,
    )

    for token in (
        "lightweight-charts@5.1.0",
        "LightweightCharts.createChart",
        "LightweightCharts.CandlestickSeries",
        "LightweightCharts.createSeriesMarkers",
        "attributionLogo: true",
        'localization: {locale:"en-US"}',
        "new ResizeObserver",
        "series.update",
        "subscribeVisibleLogicalRangeChange",
        "Plotly로 다시 열기",
        "tnrenderer:AAPL:1h:lin:candlestick",
    ):
        assert token in html
    assert "@@" not in html
