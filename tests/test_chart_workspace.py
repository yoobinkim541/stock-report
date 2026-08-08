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


def test_chart_template_payload_and_apply_across_scopes():
    ws = cw.normalize_workspace({
        "id": "w1",
        "layout": "2v",
        "active_panel": "p1",
        "panels": [
            {
                "id": "p1",
                "ticker": "NVDA",
                "timeframe": "1h",
                "period": "1y",
                "chart_kind": "candle",
                "top_indicators": ["이동평균선", "볼린저 밴드"],
                "bottom_indicators": ["거래량", "RSI"],
                "compare": ["AMD"],
                "log_scale": True,
            },
            {
                "id": "p2",
                "ticker": "MSFT",
                "timeframe": "1d",
                "period": "6mo",
                "chart_kind": "line",
                "top_indicators": ["이동평균선"],
                "bottom_indicators": ["MACD"],
                "compare": [],
                "log_scale": False,
            },
        ],
    }, ticker="NVDA")

    style_tpl = cw.chart_template_payload(ws, kind="style", name="Clean Style")
    assert style_tpl["id"] == "style-clean-style"
    assert style_tpl["payload"] == {
        "chart_kind": "candle",
        "timeframe": "1h",
        "period": "1y",
        "log_scale": True,
    }

    ind_tpl = cw.chart_template_payload(ws, kind="indicators", name="Momentum Pack")
    assert ind_tpl["payload"] == {
        "top_indicators": ["이동평균선", "볼린저 밴드"],
        "bottom_indicators": ["거래량", "RSI"],
    }

    series_tpl = cw.chart_template_payload(ws, kind="series", name="NVDA Focus")
    assert series_tpl["payload"] == {"ticker": "NVDA", "compare": ["AMD"]}

    applied = cw.apply_chart_template(cw.default_workspace("AAPL"), style_tpl)
    assert applied["panels"][0]["chart_kind"] == "candle"
    assert applied["panels"][0]["timeframe"] == "1h"
    assert applied["panels"][0]["period"] == "1y"
    assert applied["panels"][0]["log_scale"] is True
    assert applied["panels"][0]["style_template_id"] == "style-clean-style"

    applied = cw.apply_chart_template(cw.default_workspace("AAPL"), ind_tpl)
    assert applied["panels"][0]["top_indicators"] == ["이동평균선", "볼린저 밴드"]
    assert applied["panels"][0]["bottom_indicators"] == ["거래량", "RSI"]
    assert applied["panels"][0]["indicator_template_id"] == "indicators-momentum-pack"

    applied = cw.apply_chart_template(cw.default_workspace("AAPL"), series_tpl)
    assert applied["panels"][0]["ticker"] == "NVDA"
    assert applied["panels"][0]["compare"] == ["AMD"]
    assert applied["panels"][0]["series_template_id"] == "series-nvda-focus"

    applied_all = cw.apply_chart_template(ws, style_tpl, apply_to_all=True)
    assert applied_all["panels"][0]["timeframe"] == "1h"
    assert applied_all["panels"][1]["timeframe"] == "1h"
    assert applied_all["panels"][1]["chart_kind"] == "candle"


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
