"""Shared preparation of Canvas or Plotly chart surfaces."""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

from dashboard import chart_backend, chart_telemetry, lightweight_embed


@dataclass(frozen=True)
class PreparedChartSurface:
    decision: chart_backend.RendererDecision
    html: str | None
    payload: dict[str, Any] | None
    component_height: int
    prepare_ms: float
    status: str


def prepare_chart_surface(
    rendered: Any,
    *,
    compare: bool = False,
    compact: bool = False,
    lower_panes: bool = False,
    editable_orders: bool = False,
    advanced_overlays: bool = False,
    height: int = 460,
    store_key: str | None = None,
    range_sync_key: str | None = None,
    live: bool = False,
    light: bool = False,
    force_plotly: bool = False,
) -> PreparedChartSurface:
    """Select a renderer and build Canvas HTML only for compatible decisions."""
    started = time.perf_counter()
    document = rendered.document
    if force_plotly:
        document = copy.deepcopy(rendered.document)
        document.setdefault("renderer", {})["preferred"] = "plotly"
    decision = chart_backend.select_renderer(
        document,
        rendered.frame,
        compare=compare,
        compact=compact,
        lower_panes=lower_panes,
        editable_orders=editable_orders,
        advanced_overlays=advanced_overlays,
        x_mode=str(getattr(rendered.transform, "x_mode", "time")),
    )
    component_height = max(120, int(height))
    if decision.backend != "canvas":
        prepare_ms = (time.perf_counter() - started) * 1000
        chart_telemetry.record_renderer_event(
            backend=decision.backend, reasons=decision.reasons, prepare_ms=prepare_ms,
        )
        return PreparedChartSurface(
            decision, None, None, component_height, prepare_ms,
            f"{decision.status} · 준비 {prepare_ms:.1f}ms",
        )
    try:
        payload = lightweight_embed.build_payload(rendered, compact=compact)
        html = lightweight_embed.lightweight_chart_html(
            payload,
            height=component_height,
            store_key=store_key,
            range_sync_key=range_sync_key,
            live=live,
            light=light,
        )
    except Exception as exc:
        prepare_ms = (time.perf_counter() - started) * 1000
        fallback = chart_backend.canvas_error_fallback(decision, type(exc).__name__)
        chart_telemetry.record_renderer_event(
            backend=fallback.backend,
            reasons=fallback.reasons,
            prepare_ms=prepare_ms,
            error=type(exc).__name__,
        )
        return PreparedChartSurface(
            fallback, None, None, component_height, prepare_ms,
            f"{fallback.status} · 준비 {prepare_ms:.1f}ms",
        )
    prepare_ms = (time.perf_counter() - started) * 1000
    chart_telemetry.record_renderer_event(
        backend=decision.backend, reasons=decision.reasons, prepare_ms=prepare_ms,
    )
    return PreparedChartSurface(
        decision, html, payload, component_height, prepare_ms,
        f"{decision.status} · 준비 {prepare_ms:.1f}ms",
    )
