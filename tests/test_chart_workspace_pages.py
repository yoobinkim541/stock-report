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

_orig_catalog = chart_workspace_ui.cached.chart_workspace_catalog
_orig_versions = chart_workspace_ui.cached.chart_workspace_versions
_orig_alert_runs = chart_workspace_ui.views.chart_alert_runs

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
try:
    chart_workspace_ui.cached.chart_workspace_catalog = lambda: {{
        "ok": True,
        "count": 1,
        "workspaces": [{{"id": "w1", "name": "Saved Layout", "layout": "2v", "version": 3}}],
        "latest": {{"id": "w1", "name": "Saved Layout", "layout": "2v", "version": 3}},
    }}
    chart_workspace_ui.cached.chart_workspace_versions = lambda workspace_id: [
        {{"id": workspace_id, "version": 3, "created_at": "2026-08-01T00:00:00+00:00"}},
        {{"id": workspace_id, "version": 2, "created_at": "2026-07-31T00:00:00+00:00"}},
    ]
    chart_workspace_ui.views.chart_alert_runs = lambda workspace_id, limit=5: [
        {{
            "id": 7,
            "workspace_id": workspace_id,
            "rule_count": 2,
            "event_count": 1,
            "missing_bars": [],
            "notification": {{"attempted": 1, "delivered": 1, "failed": 0, "failures": []}},
            "status": "ok",
            "created_at": "2026-08-01T00:05:00+00:00",
        }}
    ]
    chart_workspace_ui.render_chart_workspace(workspace, render_charts=False)
finally:
    chart_workspace_ui.cached.chart_workspace_catalog = _orig_catalog
    chart_workspace_ui.cached.chart_workspace_versions = _orig_versions
    chart_workspace_ui.views.chart_alert_runs = _orig_alert_runs
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
    assert "분석 레일" in body
    assert "저장된 레이아웃" in body
    assert "Saved Layout" in body
    assert "알림 매니저" in body
    assert "최근 실행" in body
    assert "발송 1/1" in body


def test_workspace_drawing_store_key_respects_sync_scope():
    from dashboard import chart_workspace_ui

    ws = {
        "id": "layout-1",
        "sync": {"drawings": "layout_symbol"},
    }
    panel = {"id": "p1", "ticker": "MSFT", "timeframe": "1d"}

    assert chart_workspace_ui._drawing_store_key(ws, panel, compare=False) == "cw:layout-1:MSFT:1d:lin"

    ws["sync"]["drawings"] = "global_symbol"
    assert chart_workspace_ui._drawing_store_key(ws, panel, compare=True) == "cw:global:MSFT:1d:pct"

    ws["sync"]["drawings"] = "off"
    assert chart_workspace_ui._drawing_store_key(ws, panel, compare=False) is None


def test_workspace_drawing_sync_url_targets_agent_console(monkeypatch):
    from dashboard import chart_workspace_ui

    monkeypatch.setenv("AGENT_CONSOLE_URL", "http://agent.local")

    assert (
        chart_workspace_ui._drawing_sync_url({"id": "layout 1"}, "cw:layout_1:MSFT:1d:lin")
        == "http://agent.local/api/chart-workspaces/layout_1/drawings"
    )
    assert chart_workspace_ui._drawing_sync_url({"id": "layout 1"}, None) is None
    assert chart_workspace_ui._drawing_sync_url({"id": "layout 1"}, "cw:global:MSFT:1d:lin") is None


def test_workspace_crosshair_store_key_respects_sync_toggle():
    from dashboard import chart_workspace_ui

    ws = {"id": "layout-1", "sync": {"crosshair": True}}
    assert chart_workspace_ui._crosshair_store_key(ws) == "cw:layout-1:xh"

    ws["sync"]["crosshair"] = False
    assert chart_workspace_ui._crosshair_store_key(ws) is None
