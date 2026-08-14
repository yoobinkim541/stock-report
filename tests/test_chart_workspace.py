from __future__ import annotations

import pytest

from dashboard import chart_document as cd
from dashboard import chart_workspace as cw


def test_default_workspace_has_one_valid_panel():
    ws = cw.default_workspace("NVDA")
    errors, warnings = cw.validate_workspace(ws)
    assert errors == []
    assert ws["panels"][0]["ticker"] == "NVDA"
    assert ws["sync"]["interval"] is True
    assert ws["layout"] == "1"


def test_workspace_supports_four_by_four_and_preserves_parked_panels():
    expanded = cw.normalize_workspace({"layout": "4x4", "panels": []}, ticker="MSFT")
    assert len(expanded["panels"]) == 16
    assert [panel["id"] for panel in expanded["panels"]] == [f"p{i}" for i in range(1, 17)]

    expanded = cw.mutate_panel(
        expanded, "p8", {"ticker": "NVDA", "link_group": "blue"},
    )["workspace"]
    collapsed = cw.normalize_workspace({**expanded, "layout": "1"})
    assert len(collapsed["panels"]) == 1
    assert any(panel["id"] == "p8" for panel in collapsed["parked_panels"])

    restored = cw.normalize_workspace({**collapsed, "layout": "4x4"})
    assert restored["panels"][7]["id"] == "p8"
    assert restored["panels"][7]["ticker"] == "NVDA"
    assert restored["panels"][7]["link_group"] == "blue"


def test_panel_mutation_syncs_only_matching_link_group():
    ws = cw.normalize_workspace({
        "layout": "2x2",
        "sync": {"symbol": True, "interval": True, "range": True},
        "panels": [
            {"id": "p1", "ticker": "MSFT", "link_group": "red"},
            {"id": "p2", "ticker": "AAPL", "link_group": "red"},
            {"id": "p3", "ticker": "NVDA", "link_group": "blue"},
            {"id": "p4", "ticker": "AMD", "link_group": "blue"},
        ],
    })

    result = cw.mutate_panel(ws, "p1", {
        "ticker": "GOOGL",
        "timeframe": "1h",
        "period": "1y",
        "chart_kind": "line",
    })
    after = result["workspace"]

    assert [(p["ticker"], p["timeframe"], p["period"]) for p in after["panels"]] == [
        ("GOOGL", "1h", "1y"),
        ("GOOGL", "1h", "1y"),
        ("NVDA", "1d", "6mo"),
        ("AMD", "1d", "6mo"),
    ]
    assert after["panels"][0]["chart_kind"] == "line"
    assert after["panels"][1]["chart_kind"] == "candlestick"
    assert after["panels"][1]["document"]["symbol"] == "GOOGL"
    assert after["panels"][1]["document"]["timeframe"] == "1h"
    assert {row["panel_id"] for row in result["trace"] if row["field"] == "ticker"} == {"p1", "p2"}


def test_ungrouped_panel_syncs_globally_and_disabled_fields_stay_local():
    ws = cw.normalize_workspace({
        "layout": "2x2",
        "sync": {"symbol": True, "interval": False, "range": False},
        "panels": [
            {"id": "p1", "ticker": "MSFT"},
            {"id": "p2", "ticker": "AAPL", "link_group": "red"},
            {"id": "p3", "ticker": "NVDA", "link_group": "blue"},
            {"id": "p4", "ticker": "AMD"},
        ],
    })

    after = cw.mutate_panel(ws, "p1", {
        "ticker": "TSLA", "timeframe": "4h", "period": "5y",
    })["workspace"]

    assert {panel["ticker"] for panel in after["panels"]} == {"TSLA"}
    assert after["panels"][0]["timeframe"] == "4h"
    assert all(panel["timeframe"] == "1d" for panel in after["panels"][1:])
    assert after["panels"][0]["period"] == "5y"
    assert all(panel["period"] == "6mo" for panel in after["panels"][1:])


def test_panel_mutation_rejects_unknown_field_and_group():
    ws = cw.default_workspace("MSFT")
    with pytest.raises(ValueError, match="unsupported panel field"):
        cw.mutate_panel(ws, "p1", {"python": "eval"})
    with pytest.raises(ValueError, match="unsupported link group"):
        cw.mutate_panel(ws, "p1", {"link_group": "invisible"})


def test_maximized_panel_becomes_active_and_invalid_target_is_cleared():
    ws = cw.normalize_workspace({
        "layout": "2v", "active_panel": "p1", "maximized_panel": "p2",
    })
    assert ws["active_panel"] == "p2"
    assert ws["maximized_panel"] == "p2"

    restored = cw.normalize_workspace({**ws, "maximized_panel": "missing"})
    assert restored["maximized_panel"] is None


def test_workspace_migrates_legacy_candle_and_round_trips_chart_types():
    legacy = cw.normalize_workspace({"panels": [{"chart_kind": "candle"}]})
    assert legacy["panels"][0]["chart_kind"] == "candlestick"

    for chart_kind in cw.CHART_KINDS:
        workspace = cw.normalize_workspace({"panels": [{"chart_kind": chart_kind}]})
        assert workspace["panels"][0]["chart_kind"] == chart_kind
        assert workspace["panels"][0]["document"]["chart"]["type"] == chart_kind
        saved = cw.normalize_workspace(workspace)
        assert saved["panels"][0]["chart_kind"] == chart_kind


def test_legacy_candle_style_template_applies_as_candlestick():
    workspace = cw.normalize_workspace({"panels": [{"chart_kind": "line"}]})
    applied = cw.apply_chart_template(
        workspace,
        {"id": "legacy", "kind": "style", "payload": {"chart_kind": "candle"}},
    )
    assert applied["panels"][0]["chart_kind"] == "candlestick"


def test_workspace_document_validation_errors_are_panel_prefixed():
    errors, _warnings = cw.validate_workspace({
        "panels": [{"document": {"timeframe": "13m"}}],
    })
    assert "panel[0].document: unsupported timeframe: 13m" in errors


def test_style_and_legacy_patches_preserve_document_owned_content():
    document = cd.default_chart_document("MSFT")
    document["series"].extend([
        {"id": "portfolio", "kind": "portfolio", "symbol": "PORT", "axis": "primary", "normalization": "raw", "visible": True, "weight": 0.4},
        {"id": "fundamentals", "kind": "fundamental", "symbol": "MSFT", "axis": "primary", "normalization": "raw", "visible": True, "metric": "pe"},
        {"id": "analysts", "kind": "analyst", "symbol": "MSFT", "axis": "primary", "normalization": "raw", "visible": True, "provider": "consensus"},
    ])
    document["studies"].append({"id": "custom-study", "kind": "custom", "pane": "overlay", "config": {"window": 20}})
    workspace = cw.normalize_workspace({"panels": [{"document": document}]})

    styled = cw.apply_chart_template(
        workspace,
        {"id": "intraday", "kind": "style", "payload": {"timeframe": "1h"}},
    )
    patched = cw.apply_workspace_patch(styled, {"panels[0].period": "1y"})
    restored = patched["panels"][0]["document"]

    assert restored["series"][1:] == document["series"][1:]
    assert {study["id"] for study in restored["studies"]} == {"custom-study"}
    assert restored["timeframe"] == "1h"
    assert restored["period"] == "1y"


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
    assert style_tpl["payload"]["chart_kind"] == "candlestick"
    assert style_tpl["payload"]["timeframe"] == "1h"
    assert style_tpl["payload"]["period"] == "1y"
    assert style_tpl["payload"]["log_scale"] is True
    assert style_tpl["payload"]["document_style"]["chart"]["type"] == "candlestick"

    ind_tpl = cw.chart_template_payload(ws, kind="indicators", name="Momentum Pack")
    assert ind_tpl["payload"]["top_indicators"] == ["이동평균선", "볼린저 밴드"]
    assert ind_tpl["payload"]["bottom_indicators"] == ["거래량", "RSI"]
    assert ind_tpl["payload"]["studies"]

    series_tpl = cw.chart_template_payload(ws, kind="series", name="NVDA Focus")
    assert series_tpl["payload"]["ticker"] == "NVDA"
    assert series_tpl["payload"]["compare"] == ["AMD"]
    assert [series["symbol"] for series in series_tpl["payload"]["series"]] == ["NVDA", "AMD"]

    applied = cw.apply_chart_template(cw.default_workspace("AAPL"), style_tpl)
    assert applied["panels"][0]["chart_kind"] == "candlestick"
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
    assert applied_all["panels"][1]["chart_kind"] == "candlestick"


def test_document_backed_templates_round_trip_custom_series_studies_and_style():
    document = cd.default_chart_document("MSFT")
    document["chart"] = {"type": "renko", "params": {"box_size": 2.5}}
    document["session"]["policy"] = "extended"
    document["studies"].append({
        "id": "sma-fast", "kind": "registered", "name": "sma", "pane": "top",
        "visible": True, "params": {"period": 10},
    })
    document["series"].append({
        "id": "paper-nav", "kind": "portfolio", "symbol": "PAPER", "axis": "secondary",
        "normalization": "visible_start", "visible": True,
    })
    source = cw.normalize_workspace({"panels": [{"document": document}]})

    style = cw.chart_template_payload(source, kind="style", name="Renko")
    indicators = cw.chart_template_payload(source, kind="indicators", name="Studies")
    series = cw.chart_template_payload(source, kind="series", name="Series")
    target = cw.default_workspace("AAPL")
    target = cw.apply_chart_template(target, style)
    target = cw.apply_chart_template(target, indicators)
    target = cw.apply_chart_template(target, series)
    restored = target["panels"][0]["document"]

    assert restored["chart"]["params"] == {"box_size": 2.5}
    assert restored["session"]["policy"] == "extended"
    assert any(study.get("id") == "sma-fast" for study in restored["studies"])
    assert any(item.get("id") == "paper-nav" for item in restored["series"])


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
    assert proposal["patch"]["timeframe"] == "5m"
    assert all(not path.startswith("panels[") for path in proposal["patch"])
    assert any(study["name"] == "VWAP(세션)" for study in proposal["patch"]["studies"])
    assert "거래량" in proposal["after"]["panels"][0]["bottom_indicators"]


def test_workspace_ai_patch_targets_active_panel_not_always_first():
    """감사 #18 — AI 채팅 패치가 항상 panels[0] 만 편집해, 다중 패널
    워크스페이스에서 사용자가 보고 있는(active_panel) 패널이 아닌 다른
    종목 패널이 조용히 수정되던 문제."""
    ws = cw.normalize_workspace({
        "layout": "2x2",
        "active_panel": "p3",
        "panels": [
            {"id": "p1", "ticker": "MSFT"},
            {"id": "p2", "ticker": "AAPL"},
            {"id": "p3", "ticker": "NVDA"},
            {"id": "p4", "ticker": "AMD"},
        ],
    })

    proposal = cw.propose_workspace_patch("macd 추가해줘", ws)

    assert proposal["ok"] is True
    assert proposal["after"]["panels"][2]["ticker"] == "NVDA"
    assert "MACD" in proposal["after"]["panels"][2]["bottom_indicators"]
    assert "MACD" not in (proposal["after"]["panels"][0].get("bottom_indicators") or [])
