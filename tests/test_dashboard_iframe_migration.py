from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_production_dashboard_uses_supported_iframe_api():
    legacy = []
    for path in sorted((ROOT / "dashboard").rglob("*.py")):
        if "st.components.v1.html" in path.read_text(encoding="utf-8"):
            legacy.append(str(path.relative_to(ROOT)))

    assert legacy == []


def test_embedded_html_call_sites_use_streamlit_iframe():
    expected = {
        "dashboard/app.py",
        "dashboard/auth.py",
        "dashboard/pages/ticker.py",
        "dashboard/chart_workspace_ui.py",
    }
    found = set()
    for rel in expected:
        body = (ROOT / rel).read_text(encoding="utf-8")
        if "st.iframe(" in body:
            found.add(rel)

    assert found == expected


def test_iframe_call_sites_never_use_invalid_zero_height():
    invalid = []
    for path in sorted((ROOT / "dashboard").rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        if "st.iframe" in body and "height=0" in body:
            invalid.append(str(path.relative_to(ROOT)))

    assert invalid == []
