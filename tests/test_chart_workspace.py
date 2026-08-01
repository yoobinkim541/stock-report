from __future__ import annotations

import pytest

from dashboard import chart_workspace as cw


def test_default_workspace_has_one_valid_panel():
    ws = cw.default_workspace("NVDA")
    errors, warnings = cw.validate_workspace(ws)
    assert errors == []
    assert ws["panels"][0]["ticker"] == "NVDA"
    assert ws["sync"]["interval"] is True
    assert ws["layout"] == "1"


def test_patch_updates_nested_panel_without_clobbering_other_fields():
    ws = cw.default_workspace("MSFT")
    after = cw.apply_workspace_patch(
        ws,
        {
            "panels[0].timeframe": "5m",
            "panels[0].top_indicators": ["이동평균선", "VWAP(세션)", "매물대"],
        },
    )
    assert after["panels"][0]["timeframe"] == "5m"
    assert after["panels"][0]["ticker"] == "MSFT"
    assert after["panels"][0]["period"] == ws["panels"][0]["period"]
    diff = cw.diff_workspaces(ws, after)
    assert any(row["path"] == "panels[0].timeframe" for row in diff)


def test_patch_rejects_unknown_indicator_and_panel():
    ws = cw.default_workspace("MSFT")
    with pytest.raises(ValueError, match="unknown indicator"):
        cw.apply_workspace_patch(ws, {"panels[0].top_indicators": ["Pine Script"]})
    with pytest.raises(ValueError, match="panel index"):
        cw.apply_workspace_patch(ws, {"panels[5].ticker": "AAPL"})


def test_chart_workspace_storage_round_trip(tmp_path, monkeypatch):
    from agent_console import storage

    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    ws = cw.default_workspace("NVDA")
    saved = storage.save_chart_workspace(ws)
    assert saved["workspace"]["panels"][0]["ticker"] == "NVDA"

    versioned = storage.save_chart_workspace_version(
        saved["id"],
        {
            **saved["workspace"],
            "name": "Trend Layout",
        },
        note="rename",
    )
    assert versioned["version"] == 2

    rows = storage.list_chart_workspaces()
    assert rows[0]["name"] == "Trend Layout"
    assert storage.get_chart_workspace(saved["id"])["version"] == 2
    assert storage.get_chart_workspace(saved["id"], version=1)["version"] == 1
    assert storage.list_chart_workspace_versions(saved["id"])[0]["version"] == 2


def test_chart_template_storage_filters_kind(tmp_path, monkeypatch):
    from agent_console import storage

    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    storage.save_chart_template(
        {
            "id": "trend",
            "kind": "indicators",
            "name": "Trend",
            "payload": {"top_indicators": ["이동평균선"]},
        }
    )
    storage.save_chart_template(
        {
            "id": "style",
            "kind": "style",
            "name": "Noir",
            "payload": {"chart_kind": "candle"},
        }
    )
    assert [r["id"] for r in storage.list_chart_templates(kind="indicators")] == [
        "trend"
    ]


def test_dashboard_workspace_wrappers_forward_storage(monkeypatch):
    from dashboard import cached, views

    monkeypatch.setattr(
        views.storage,
        "list_chart_workspaces",
        lambda limit=50: [{"id": "w1", "name": "Main"}],
    )
    monkeypatch.setattr(
        views.storage,
        "get_chart_workspace",
        lambda workspace_id, version=None: {
            "id": workspace_id or "w1",
            "workspace": cw.default_workspace("AAPL"),
        },
    )
    monkeypatch.setattr(
        views.storage,
        "list_chart_workspace_versions",
        lambda workspace_id, limit=30: [{"version": 1}],
    )

    cached.chart_workspace_catalog.clear()
    assert views.chart_workspace_catalog()["count"] == 1
    assert views.chart_workspace_detail("w1")["id"] == "w1"
    assert cached.chart_workspace_catalog()["workspaces"][0]["id"] == "w1"


def test_workspace_ai_patch_heuristic_handles_intraday_vwap():
    ws = cw.default_workspace("NVDA")
    proposal = cw.propose_workspace_patch("5분봉으로 바꾸고 VWAP랑 거래량을 봐줘", ws)
    assert proposal["ok"] is True
    assert proposal["patch"]["panels[0].timeframe"] == "5m"
    assert "VWAP(세션)" in proposal["patch"]["panels[0].top_indicators"]
    assert "거래량" in proposal["after"]["panels"][0]["bottom_indicators"]
