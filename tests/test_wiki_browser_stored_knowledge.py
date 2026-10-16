"""Full widget flow against an isolated real wiki store, without providers."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from agent_console import shared_memory, wiki
from dashboard import wiki_browser


def test_stored_knowledge_and_child_navigation(monkeypatch, tmp_path):
    for key, name in (
        ("AGENT_CONSOLE_SHARED_MEMORY_DIR", "memory"),
        ("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", "artifacts"),
        ("AGENT_CONSOLE_QMD_WIKI_DIR", "markdown"),
        ("STOCK_REPORT_REPORTS_DIR", "reports"),
    ):
        monkeypatch.setenv(key, str(tmp_path / name))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    wiki._CACHE.clear()
    wiki_browser._cached_wiki_snapshot.clear()
    wiki_browser._cached_context_section.clear()
    try:
        wiki.upsert_page({
            "id": "synthetic-child", "title": "Capacity details",
            "body": "CHILD_BODY_SENTINEL", "surface": "market", "status": "draft",
        })
        wiki.upsert_page({
            "id": "synthetic-parent", "title": "Capacity overview",
            "body": "[[synthetic-child|Capacity details]]", "surface": "market",
            "status": "draft", "source_refs": ["https://example.org/synthetic"],
            "source_knowledge": {
                "claims": [{"claim_id": "synthetic-claim", "text": "Capacity is not orders.",
                            "as_of": "2026-10-15", "scope": "Synthetic capacity",
                            "knowledge_state": "draft", "source_revision_id": "synthetic-revision"}],
                "limitations": ["Demand remains unverified."],
            },
        })
        app = AppTest.from_string('''
import streamlit as st
from dashboard import wiki_browser
if "agent_wiki_selected_page_id" not in st.session_state:
    st.session_state["agent_wiki_selected_page_id"] = "synthetic-parent"
wiki_browser.render_wiki_tab("market", {"chat_rows": []})
''', default_timeout=30).run()
        assert not app.exception, str(app.exception)
        rendered = " ".join(str(item.value) for item in app.markdown)
        assert "Capacity is not orders." in rendered
        assert "Demand remains unverified." in rendered
        app.button(key="wiki_body_ref_synthetic-child_0").click().run()
        assert not app.exception, str(app.exception)
        assert app.session_state["agent_wiki_selected_page_id"] == "synthetic-child"
        assert any("CHILD_BODY_SENTINEL" in str(item.value) for item in app.markdown)
    finally:
        wiki._CACHE.clear()
        wiki_browser._cached_wiki_snapshot.clear()
        wiki_browser._cached_context_section.clear()
