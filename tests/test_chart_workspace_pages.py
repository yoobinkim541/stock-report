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
_orig_alert_run_once = chart_workspace_ui.views.chart_alert_run_once

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
            "result": {{
                "events": [
                    {{
                        "alert_id": "alert-1",
                        "symbol": "MSFT",
                        "name": "MSFT breakout",
                        "as_of": "2026-08-01T00:04:00+00:00",
                        "current_price": 321.25,
                        "previous_price": 319.0,
                        "matched_conditions": ["price:crossing_up"],
                        "message": "MSFT crossed 320",
                    }}
                ]
            }},
            "status": "ok",
            "created_at": "2026-08-01T00:05:00+00:00",
        }}
    ]
    chart_workspace_ui.render_chart_workspace(workspace, render_charts=False)
finally:
    chart_workspace_ui.cached.chart_workspace_catalog = _orig_catalog
    chart_workspace_ui.cached.chart_workspace_versions = _orig_versions
    chart_workspace_ui.views.chart_alert_runs = _orig_alert_runs
    chart_workspace_ui.views.chart_alert_run_once = _orig_alert_run_once
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    body += " ".join(str(c.value) for c in at.caption)
    body += " ".join(str(button.label) for button in at.button)
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
    assert "수동 실행" in body
    assert "최근 이벤트" in body
    assert "MSFT breakout" in body
    assert "price:crossing_up" in body


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


def test_replay_analysis_disables_live_orderflow_loader():
    from dashboard import chart_orderflow, chart_workspace_ui

    live_loader = chart_workspace_ui._analysis_orderflow_loader(None)
    replay_loader = chart_workspace_ui._analysis_orderflow_loader("2026-01-02T15:00:00Z")

    assert live_loader is chart_orderflow.load_snapshot
    assert replay_loader("AAPL") == {"ok": False, "reason": "replay_isolated"}


def test_workspace_drawing_sync_url_targets_agent_console(monkeypatch):
    from dashboard import chart_workspace_ui

    monkeypatch.setenv("AGENT_CONSOLE_URL", "http://agent.local")

    assert (
        chart_workspace_ui._drawing_sync_url({"id": "layout 1"}, "cw:layout_1:MSFT:1d:lin")
        == "http://agent.local/api/chart-workspaces/layout_1/drawings"
    )
    assert chart_workspace_ui._drawing_sync_url({"id": "layout 1"}, None) is None
    assert chart_workspace_ui._drawing_sync_url({"id": "layout 1"}, "cw:global:MSFT:1d:lin") is None


def test_workspace_drawing_sync_url_requires_explicit_browser_reachable_base(monkeypatch):
    from dashboard import chart_workspace_ui

    monkeypatch.delenv("AGENT_CONSOLE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("AGENT_CONSOLE_URL", raising=False)
    monkeypatch.setenv("AGENT_CONSOLE_PORT", "8797")

    assert chart_workspace_ui._drawing_sync_url(
        {"id": "layout-1"}, "cw:layout-1:MSFT:1d:lin",
    ) is None

    monkeypatch.setenv("AGENT_CONSOLE_URL", "http://127.0.0.1:8797")
    assert chart_workspace_ui._drawing_sync_url(
        {"id": "layout-1"}, "cw:layout-1:MSFT:1d:lin",
    ) is None


def test_workspace_crosshair_store_key_respects_sync_toggle():
    from dashboard import chart_workspace_ui

    ws = {"id": "layout-1", "sync": {"crosshair": True}}
    assert chart_workspace_ui._crosshair_store_key(ws) == "cw:layout-1:xh"

    ws["sync"]["crosshair"] = False
    assert chart_workspace_ui._crosshair_store_key(ws) is None


def test_workspace_range_store_key_respects_group_and_sync_toggle():
    from dashboard import chart_workspace_ui

    ws = {"id": "layout-1", "sync": {"range": True}}
    assert chart_workspace_ui._range_sync_store_key(ws, {"link_group": "red"}) == "cw:layout-1:range:red"
    assert chart_workspace_ui._range_sync_store_key(ws, {"link_group": ""}) == "cw:layout-1:range:all"

    ws["sync"]["range"] = False
    assert chart_workspace_ui._range_sync_store_key(ws, {"link_group": "red"}) is None


def test_dense_workspace_uses_compact_background_and_maximize_visibility():
    from dashboard import chart_workspace, chart_workspace_ui

    ws = chart_workspace.normalize_workspace({"layout": "4x4", "active_panel": "p1"})
    assert chart_workspace_ui._panel_render_profile(ws, ws["panels"][0]) == {
        "visible": True, "compact": True, "height": 260,
    }
    assert chart_workspace_ui._panel_render_profile(ws, ws["panels"][1]) == {
        "visible": True, "compact": True, "height": 260,
    }

    ws["maximized_panel"] = "p2"
    assert chart_workspace_ui._panel_render_profile(ws, ws["panels"][0])["visible"] is False
    assert chart_workspace_ui._panel_render_profile(ws, ws["panels"][1]) == {
        "visible": True, "compact": False, "height": 760,
    }


def test_chart_workspace_renderer_surfaces_template_library():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import chart_workspace_ui

workspace = {{
    "id": "w1",
    "name": "Template Workspace",
    "layout": "1",
    "active_panel": "p1",
    "sync": {{"symbol": False, "interval": True, "range": True, "crosshair": True, "drawings": "layout_symbol"}},
    "panels": [
        {{"id": "p1", "ticker": "MSFT", "timeframe": "1d", "period": "6mo", "chart_kind": "candle", "top_indicators": ["이동평균선"], "bottom_indicators": ["거래량"], "compare": [], "log_scale": False}},
    ],
}}
orig_templates = chart_workspace_ui.cached.chart_templates
try:
    chart_workspace_ui.cached.chart_templates = lambda kind=None, limit=50: [
        {{
            "id": "style-clean",
            "kind": "style",
            "name": "Clean Style",
            "template": {{
                "id": "style-clean",
                "kind": "style",
                "name": "Clean Style",
                "payload": {{"chart_kind": "candle", "timeframe": "1d", "period": "6mo", "log_scale": False}},
                "source": {{"ticker": "MSFT"}},
            }},
            "payload": {{"chart_kind": "candle", "timeframe": "1d", "period": "6mo", "log_scale": False}},
            "created_at": "2026-08-01T00:00:00+00:00",
            "updated_at": "2026-08-01T00:00:00+00:00",
        }}
    ] if kind == "style" else []
    chart_workspace_ui.render_chart_workspace(workspace, render_charts=False)
finally:
    chart_workspace_ui.cached.chart_templates = orig_templates
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    body = " ".join(str(m.value) for m in at.markdown)
    body += " ".join(str(c.value) for c in at.caption)
    assert "템플릿 라이브러리" in body
    assert "현재 패널" in body
    assert "Clean Style" in body


def test_four_by_four_workspace_renders_sixteen_unique_panel_controls():
    script = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
from dashboard import chart_workspace, chart_workspace_ui

workspace = chart_workspace.normalize_workspace({{"id": "grid-16", "layout": "4x4"}})
orig_catalog = chart_workspace_ui.cached.chart_workspace_catalog
orig_versions = chart_workspace_ui.cached.chart_workspace_versions
orig_templates = chart_workspace_ui.cached.chart_templates
orig_rules = chart_workspace_ui.views.chart_alert_rules
orig_runs = chart_workspace_ui.views.chart_alert_runs
try:
    chart_workspace_ui.cached.chart_workspace_catalog = lambda: {{"ok": True, "count": 0, "workspaces": []}}
    chart_workspace_ui.cached.chart_workspace_versions = lambda workspace_id: []
    chart_workspace_ui.cached.chart_templates = lambda kind=None, limit=50: []
    chart_workspace_ui.views.chart_alert_rules = lambda workspace_id, limit=20: []
    chart_workspace_ui.views.chart_alert_runs = lambda workspace_id, limit=5: []
    chart_workspace_ui.render_chart_workspace(workspace, render_charts=False)
finally:
    chart_workspace_ui.cached.chart_workspace_catalog = orig_catalog
    chart_workspace_ui.cached.chart_workspace_versions = orig_versions
    chart_workspace_ui.cached.chart_templates = orig_templates
    chart_workspace_ui.views.chart_alert_rules = orig_rules
    chart_workspace_ui.views.chart_alert_runs = orig_runs
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()
    assert not at.exception, str(at.exception)
    panel_buttons = [button for button in at.button if str(button.label).startswith("MSFT ·")]
    assert len(panel_buttons) == 16
    link_groups = [box for box in at.selectbox if box.label == "링크 그룹"]
    assert len(link_groups) == 16


def test_alert_event_markers_follow_panel_symbol():
    from dashboard import chart_workspace_ui

    runs = [
        {
            "result": {
                "events": [
                    {
                        "alert_id": "a1",
                        "symbol": "MSFT",
                        "name": "MSFT breakout",
                        "as_of": "2026-08-01T00:04:00+00:00",
                        "current_price": 321.25,
                        "matched_conditions": ["price:crossing_up", "indicator:rsi_14:less_than"],
                        "message": "MSFT crossed 320",
                    },
                    {
                        "alert_id": "a2",
                        "symbol": "QQQ",
                        "name": "QQQ fade",
                        "as_of": "2026-08-01T00:05:00+00:00",
                        "current_price": 550.0,
                        "matched_conditions": ["price:crossing_down"],
                    },
                ]
            }
        }
    ]

    markers = chart_workspace_ui._alert_event_markers_for_panel(runs, {"ticker": "msft"})

    assert markers == [
        {
            "event_id": "a1",
            "date": "2026-08-01T00:04:00+00:00",
            "timestamp": "2026-08-01T00:04:00+00:00",
            "ticker": "MSFT",
            "side": "alert",
            "qty": None,
            "price": 321.25,
            "avg_price": None,
            "account": "chart-alerts",
            "source": "MSFT breakout",
            "currency": "",
            "note": "price:crossing_up, indicator:rsi_14:less_than · MSFT crossed 320",
        }
    ]


def test_alert_condition_payload_can_include_rsi_filter():
    from dashboard import chart_workspace_ui

    condition = chart_workspace_ui._alert_condition_payload(
        price_operator="crossing_up",
        price_value=320.0,
        rsi_enabled=True,
        rsi_operator="less_than",
        rsi_value=70.0,
    )

    assert condition == {
        "all": [
            {"type": "price", "operator": "crossing_up", "value": 320.0},
            {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 70.0},
        ]
    }


def test_alert_condition_payload_can_include_selected_indicator_filter():
    from dashboard import chart_workspace_ui

    condition = chart_workspace_ui._alert_condition_payload(
        price_operator="crossing_down",
        price_value=120.0,
        indicator_enabled=True,
        indicator_field="macd_hist",
        indicator_operator="greater_than",
        indicator_value=0.0,
    )

    assert condition == {
        "all": [
            {"type": "price", "operator": "crossing_down", "value": 120.0},
            {"type": "indicator", "field": "macd_hist", "operator": "greater_than", "value": 0.0},
        ]
    }


def test_alert_rule_label_summarizes_multi_condition():
    from dashboard import chart_workspace_ui

    label = chart_workspace_ui._alert_rule_label({
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_up", "value": 320.0},
                {"type": "indicator", "field": "rsi_14", "operator": "less_than", "value": 70.0},
            ]
        }
    })

    assert label == "상향 돌파 320 · RSI(14) 미만 70"


def test_alert_rule_label_uses_indicator_display_names():
    from dashboard import chart_workspace_ui

    label = chart_workspace_ui._alert_rule_label({
        "condition": {
            "all": [
                {"type": "price", "operator": "crossing_down", "value": 120.0},
                {"type": "indicator", "field": "macd_hist", "operator": "greater_than", "value": 0.0},
            ]
        }
    })

    assert label == "하향 이탈 120 · MACD 히스토그램 초과 0"
