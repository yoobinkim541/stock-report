"""Pure renderer capability checks for ChartDocument chart surfaces."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dashboard import chart_document


AUTO_CANVAS_BAR_THRESHOLD = 1_000
CANVAS_CHART_TYPES = frozenset({
    "line", "area", "baseline", "candlestick", "hollow_candle",
    "heikin_ashi", "bars", "high_low",
})

_REASON_LABELS = {
    "empty_frame": "표시할 봉 없음",
    "comparison": "비교 모드",
    "sequence_chart": "가격 변환 차트",
    "lower_panes": "하단 분석 패널",
    "editable_orders": "편집 가능한 주문선",
    "advanced_overlays": "고급 오버레이",
    "unsupported_chart_type": "미지원 차트 유형",
    "below_auto_threshold": "고밀도 기준 미만",
    "canvas_prepare_error": "Canvas 준비 실패",
}


@dataclass(frozen=True)
class RendererDecision:
    backend: str
    requested: str
    automatic: bool
    reasons: tuple[str, ...]
    status: str


def canvas_error_fallback(decision: RendererDecision, message: str = "") -> RendererDecision:
    """Convert a selected Canvas backend into a typed, user-visible Plotly fallback."""
    detail = f" ({str(message)[:60]})" if message else ""
    return RendererDecision(
        "plotly",
        decision.requested,
        decision.automatic,
        ("canvas_prepare_error",),
        f"분석 Plotly · Canvas 준비 실패{detail}",
    )


def _frame_size(frame: Any) -> int:
    try:
        return len(frame)
    except TypeError:
        return 0


def requires_editable_orders(session: Mapping[str, Any] | None) -> bool:
    """Return whether replay state contains a draggable pending price order."""
    if not isinstance(session, Mapping):
        return False
    return any(
        isinstance(order, Mapping)
        and order.get("status") == "pending"
        and order.get("type") in {"limit", "stop"}
        for order in session.get("orders") or []
    )


def select_renderer(
    document: Mapping[str, Any],
    frame: Any,
    *,
    compare: bool = False,
    compact: bool = False,
    lower_panes: bool = False,
    editable_orders: bool = False,
    advanced_overlays: bool = False,
    x_mode: str = "time",
) -> RendererDecision:
    """Choose Canvas only when every requested capability has a supported path."""
    doc = chart_document.normalize_chart_document(document)
    requested = str((doc.get("renderer") or {}).get("preferred") or "auto")
    automatic = requested == "auto"
    if requested == "plotly":
        return RendererDecision("plotly", requested, False, (), "분석 Plotly")

    reasons: list[str] = []
    chart_type = str((doc.get("chart") or {}).get("type") or "")
    if _frame_size(frame) == 0:
        reasons.append("empty_frame")
    if compare:
        reasons.append("comparison")
    if x_mode != "time":
        reasons.append("sequence_chart")
    if lower_panes:
        reasons.append("lower_panes")
    if editable_orders:
        reasons.append("editable_orders")
    if advanced_overlays:
        reasons.append("advanced_overlays")
    if chart_type not in CANVAS_CHART_TYPES:
        reasons.append("unsupported_chart_type")
    if reasons:
        labels = ", ".join(_REASON_LABELS[reason] for reason in reasons)
        return RendererDecision(
            "plotly", requested, automatic, tuple(reasons), f"분석 Plotly · {labels}",
        )

    if automatic and not compact and _frame_size(frame) < AUTO_CANVAS_BAR_THRESHOLD:
        return RendererDecision(
            "plotly", requested, True, ("below_auto_threshold",),
            "분석 Plotly · 고밀도 기준 미만",
        )
    return RendererDecision("canvas", requested, automatic, (), "고성능 Canvas")
