"""Shared preparation of Canvas or Plotly chart surfaces."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from dashboard import chart_backend, lightweight_embed


@dataclass(frozen=True)
class PreparedChartSurface:
    decision: chart_backend.RendererDecision
    html: str | None
    payload: dict[str, Any] | None
    component_height: int


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
        return PreparedChartSurface(decision, None, None, component_height)
    payload = lightweight_embed.build_payload(rendered, compact=compact)
    html = lightweight_embed.lightweight_chart_html(
        payload,
        height=component_height,
        store_key=store_key,
        range_sync_key=range_sync_key,
        live=live,
        light=light,
    )
    return PreparedChartSurface(decision, html, payload, component_height)
