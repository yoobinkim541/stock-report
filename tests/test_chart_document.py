from __future__ import annotations

import pytest

from dashboard import chart_document as cd


def test_default_document_is_valid_and_renderer_neutral():
    doc = cd.default_chart_document("AAPL")
    errors, warnings = cd.validate_chart_document(doc)
    assert errors == []
    assert warnings == []
    assert doc["version"] == 1
    assert doc["symbol"] == "AAPL"
    assert doc["chart"]["type"] == "candlestick"
    assert doc["session"]["policy"] == "regular"
    assert doc["renderer"] == {"preferred": "plotly"}


def test_old_workspace_panel_migrates_without_dropping_settings():
    panel = {
        "id": "p1",
        "ticker": "MSFT",
        "timeframe": "1d",
        "period": "1y",
        "chart_kind": "heikin_ashi",
        "top_indicators": ["이동평균선"],
        "bottom_indicators": ["거래량", "RSI"],
        "compare": ["QQQ"],
        "log_scale": True,
    }
    doc = cd.document_from_panel(panel, workspace_id="growth")
    assert doc["chart"]["type"] == "heikin_ashi"
    assert doc["series"][1]["symbol"] == "QQQ"
    assert doc["scale"]["type"] == "log"
    restored = cd.panel_from_document(doc, panel)
    assert restored["chart_kind"] == "heikin_ashi"
    assert restored["compare"] == ["QQQ"]


def test_patch_rejects_unknown_paths_and_invalid_chart_parameters():
    doc = cd.default_chart_document("MSFT")
    with pytest.raises(ValueError, match="box_size"):
        cd.apply_chart_document_patch(doc, {"chart.params.box_size": 0})
    with pytest.raises(ValueError, match="unsupported patch path"):
        cd.apply_chart_document_patch(doc, {"python.eval": "open('/etc/passwd')"})


def test_normalization_keeps_requested_invalid_timeframe_for_validation():
    doc = cd.normalize_chart_document({"timeframe": "13m"})
    errors, _warnings = cd.validate_chart_document(doc)
    assert doc["timeframe"] == "13m"
    assert "unsupported timeframe: 13m" in errors


def test_validation_rejects_duplicate_series_ids():
    doc = cd.default_chart_document("MSFT")
    doc["series"].append({
        "id": "primary",
        "kind": "benchmark",
        "symbol": "QQQ",
        "axis": "primary",
        "normalization": "raw",
        "visible": True,
    })
    errors, _warnings = cd.validate_chart_document(doc)
    assert "duplicate series id: primary" in errors
