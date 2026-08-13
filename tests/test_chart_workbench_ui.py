from __future__ import annotations

import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_chart_toolbar_exposes_renderer_preference_and_returns_selection():
    script = f"""
import sys
sys.path.insert(0, {ROOT!r})
import streamlit as st
from dashboard import chart_document, chart_workbench_ui

out = chart_workbench_ui.render_chart_toolbar(
    chart_document.default_chart_document("AAPL"), key_prefix="contract",
)
st.write("renderer=" + out["renderer"]["preferred"])
"""
    at = AppTest.from_string(script, default_timeout=30)
    at.run()

    assert not at.exception, str(at.exception)
    renderer = next(
        control for control in at.segmented_control
        if str(getattr(control, "label", "")) == "렌더러"
    )
    assert renderer.value == "auto"
    renderer.set_value("canvas")
    at.run()
    assert any("renderer=canvas" in str(item.value) for item in at.markdown)
