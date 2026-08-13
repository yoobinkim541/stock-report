from __future__ import annotations

import pandas as pd
import pytest

from dashboard import chart_backend, chart_document


def _frame(size: int) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=size, freq="min")
    return pd.DataFrame({"Close": range(size)}, index=index)


def _document(preferred: str = "auto", chart_type: str = "candlestick") -> dict:
    document = chart_document.default_chart_document("AAPL")
    document["renderer"]["preferred"] = preferred
    document["chart"]["type"] = chart_type
    return document


def test_auto_selects_canvas_for_dense_compatible_chart():
    decision = chart_backend.select_renderer(_document(), _frame(1000))

    assert decision.backend == "canvas"
    assert decision.requested == "auto"
    assert decision.automatic is True
    assert decision.reasons == ()
    assert "Canvas" in decision.status


def test_auto_keeps_plotly_for_small_noncompact_chart():
    decision = chart_backend.select_renderer(_document(), _frame(999))

    assert decision.backend == "plotly"
    assert decision.reasons == ("below_auto_threshold",)


def test_auto_uses_canvas_for_compact_panel_below_threshold():
    decision = chart_backend.select_renderer(_document(), _frame(100), compact=True)

    assert decision.backend == "canvas"


def test_explicit_canvas_bypasses_threshold():
    decision = chart_backend.select_renderer(_document("canvas"), _frame(20))

    assert decision.backend == "canvas"
    assert decision.automatic is False


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"compare": True}, "comparison"),
        ({"x_mode": "sequence"}, "sequence_chart"),
        ({"lower_panes": True}, "lower_panes"),
        ({"editable_orders": True}, "editable_orders"),
        ({"advanced_overlays": True}, "advanced_overlays"),
    ],
)
def test_canvas_request_falls_back_for_unsupported_capability(kwargs, reason):
    decision = chart_backend.select_renderer(_document("canvas"), _frame(1000), **kwargs)

    assert decision.backend == "plotly"
    assert reason in decision.reasons
    assert "Plotly" in decision.status


def test_sequence_chart_type_falls_back_even_when_x_mode_is_wrongly_time():
    decision = chart_backend.select_renderer(_document("canvas", "renko"), _frame(1000))

    assert decision.backend == "plotly"
    assert decision.reasons == ("unsupported_chart_type",)


def test_explicit_plotly_ignores_canvas_capability_checks():
    decision = chart_backend.select_renderer(
        _document("plotly", "renko"), _frame(5000), compare=True, lower_panes=True,
    )

    assert decision.backend == "plotly"
    assert decision.reasons == ()
    assert decision.automatic is False


def test_empty_frame_never_selects_canvas():
    decision = chart_backend.select_renderer(_document("canvas"), _frame(0))

    assert decision.backend == "plotly"
    assert decision.reasons == ("empty_frame",)


def test_pending_limit_or_stop_orders_require_editable_plotly_lines():
    session = {
        "orders": [
            {"status": "filled", "type": "limit"},
            {"status": "pending", "type": "market"},
            {"status": "pending", "type": "stop"},
        ],
    }

    assert chart_backend.requires_editable_orders(session) is True
    assert chart_backend.requires_editable_orders({"orders": session["orders"][:2]}) is False
    assert chart_backend.requires_editable_orders(None) is False
