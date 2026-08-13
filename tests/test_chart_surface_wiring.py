from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ticker_and_workspace_use_shared_chart_surface():
    ticker = (ROOT / "dashboard/pages/ticker.py").read_text(encoding="utf-8")
    workspace = (ROOT / "dashboard/chart_workspace_ui.py").read_text(encoding="utf-8")

    for source in (ticker, workspace):
        assert "chart_surface.prepare_chart_surface(" in source
        assert "prepared.decision.backend == \"canvas\"" in source
        assert "prepared.status" in source
        assert "prepared.html" in source
