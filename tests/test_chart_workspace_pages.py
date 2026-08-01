from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_chart_workspace_renderer_surfaces_layout_and_sync_controls():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import chart_workspace_ui

workspace = {{
    "id": "w1",
    "name": "Main Workspace",
    "layout": "2v",
    "active_panel": "p1",
    "sync": {{"symbol": False, "interval": True, "range": True, "crosshair": True, "drawings": "layout_symbol"}},
    "panels": [
        {{"id": "p1", "ticker": "MSFT", "timeframe": "1d", "period": "6mo", "chart_kind": "candle", "top_indicators": ["이동평균선"], "bottom_indicators": ["거래량"], "compare": [], "log_scale": False}},
        {{"id": "p2", "ticker": "QQQ", "timeframe": "1d", "period": "6mo", "chart_kind": "line", "top_indicators": ["이동평균선"], "bottom_indicators": ["RSI"], "compare": [], "log_scale": False}},
    ],
}}
chart_workspace_ui.render_chart_workspace(workspace, render_charts=False)
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    body += " ".join(str(c.value) for c in at.caption)
    assert "Main Workspace" in body
    assert "동기화" in body
    assert "MSFT" in body
    assert "QQQ" in body
