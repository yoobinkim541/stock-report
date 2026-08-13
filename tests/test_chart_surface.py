from __future__ import annotations

import pandas as pd

from dashboard import chart_document, chart_renderer, chart_surface


def _rendered(size: int = 1200):
    index = pd.date_range("2025-01-01", periods=size, freq="h", tz="UTC")
    frame = pd.DataFrame({
        "Open": [100.0 + i * 0.01 for i in range(size)],
        "High": [101.0 + i * 0.01 for i in range(size)],
        "Low": [99.0 + i * 0.01 for i in range(size)],
        "Close": [100.5 + i * 0.01 for i in range(size)],
        "Volume": [1000.0] * size,
    }, index=index)
    document = chart_document.default_chart_document("AAPL")
    return chart_renderer.render_plotly_chart(document, frame, chart_kwargs={"mas": (20,)})


def test_prepare_surface_builds_canvas_html_for_dense_auto_chart():
    prepared = chart_surface.prepare_chart_surface(
        _rendered(), height=480, store_key="AAPL:1h:lin:candlestick",
        range_sync_key="layout:range", live=True,
    )

    assert prepared.decision.backend == "canvas"
    assert prepared.html is not None
    assert "lightweight-charts@5.1.0" in prepared.html
    assert prepared.component_height == 480


def test_prepare_surface_returns_plotly_without_building_canvas_when_incompatible():
    prepared = chart_surface.prepare_chart_surface(
        _rendered(), compare=True, height=480,
    )

    assert prepared.decision.backend == "plotly"
    assert prepared.decision.reasons == ("comparison",)
    assert prepared.html is None


def test_force_plotly_supports_legacy_runtime_without_mutating_document():
    rendered = _rendered()
    before = rendered.document["renderer"].copy()

    prepared = chart_surface.prepare_chart_surface(rendered, force_plotly=True)

    assert prepared.decision.backend == "plotly"
    assert prepared.decision.requested == "plotly"
    assert rendered.document["renderer"] == before


def test_compact_surface_selects_canvas_below_dense_threshold():
    prepared = chart_surface.prepare_chart_surface(_rendered(80), compact=True)

    assert prepared.decision.backend == "canvas"
    assert prepared.payload["compact"] is True
