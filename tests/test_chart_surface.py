from __future__ import annotations

from pathlib import Path

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
    assert "고성능 Canvas · 준비" in prepared.status


def test_prepare_surface_returns_plotly_without_building_canvas_when_incompatible():
    prepared = chart_surface.prepare_chart_surface(
        _rendered(), compare=True, height=480,
    )

    assert prepared.decision.backend == "plotly"
    assert prepared.decision.reasons == ("comparison",)
    assert prepared.html is None
    assert "분석 Plotly" in prepared.status


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


def test_canvas_prepare_error_falls_back_to_plotly_and_records_telemetry(monkeypatch):
    rendered = _rendered(1_200)
    events = []
    monkeypatch.setattr(
        chart_surface.lightweight_embed,
        "build_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad bars")),
    )
    monkeypatch.setattr(
        chart_surface.chart_telemetry,
        "record_renderer_event",
        lambda **kwargs: events.append(kwargs) or {},
    )

    prepared = chart_surface.prepare_chart_surface(rendered)

    assert prepared.decision.backend == "plotly"
    assert prepared.decision.reasons == ("canvas_prepare_error",)
    assert prepared.html is None
    assert prepared.payload is None
    assert prepared.prepare_ms >= 0
    assert "Canvas 준비 실패" in prepared.status
    assert events[-1]["error"] == "ValueError"


def test_chart_call_sites_surface_preparation_status():
    root = Path(__file__).resolve().parent.parent
    for rel in ("dashboard/pages/ticker.py", "dashboard/chart_workspace_ui.py"):
        body = (root / rel).read_text(encoding="utf-8")
        assert "st.caption(prepared.status" in body
