"""Committed multi-source split topic through Markdown, chat and widgets."""
import pytest

pytest.importorskip('streamlit')
from streamlit.testing.v1 import AppTest

from agent_console import qmd_search, shared_memory, wiki
from dashboard import wiki_browser
from reports.wiki_topic_transaction import prepare_topic_batch, recover_topic_operation
from test_wiki_topic_transaction import transaction, evidence, proposal, validate, NOW


def test_split_topic_retains_evidence_and_child_is_readable(transaction):
    data, journal, rows = transaction
    payload = proposal(data)
    payload['targets'][0]['additions'][0]['body'] = (
        'Synthetic dated account, not adjudicated.\n\n' * 500 + 'TAIL_UNCERTAINTY [S1]')
    checked = validate(data, payload)
    batch = prepare_topic_batch(checked, journal=journal, records=rows, now=NOW)
    assert recover_topic_operation(batch['operation_id'], journal=journal, now=NOW)['stage'] == 'committed'
    parent_id = batch['target_ids'][0]
    snap = wiki.semantic_snapshot(parent_id)
    assert len(snap['children']) >= 2
    assert 'TAIL_UNCERTAINTY' in snap['body']
    assert len({c['source_revision_id'] for c in snap['claims']}) == 2
    assert set(checked['targets'][0]['limitations']) <= set(snap['source_knowledge']['limitations'])
    pages = [wiki.get_page(parent_id), *[wiki.get_page(c['id']) for c in snap['children']]]
    for page in pages:
        assert len(page['body']) <= wiki.WIKI_BODY_LIMIT
    markdown = '\n'.join(qmd_search._page_markdown(p, p['id']) for p in pages)
    assert 'TAIL_UNCERTAINTY' in markdown
    for claim in snap['claims']:
        assert claim['claim_id'] in markdown
        assert claim['source_revision_id'] in markdown
    assert len(wiki._context_body_excerpt(pages[0])) <= 1600
    child = next(c for c in snap['children'] if 'TAIL_UNCERTAINTY' in c['body'])
    wiki_browser._cached_wiki_snapshot.clear()
    wiki_browser._cached_context_section.clear()
    try:
        app = AppTest.from_string(f'''
import streamlit as st
from dashboard import wiki_browser
if "agent_wiki_selected_page_id" not in st.session_state:
    st.session_state["agent_wiki_selected_page_id"] = {parent_id!r}
wiki_browser.render_wiki_tab("market", {{"chat_rows": []}})
''', default_timeout=30).run()
        assert not app.exception, str(app.exception)
        assert any('Uncertainty' in str(m.value) for m in app.markdown)
        button = next(b for b in app.button if str(b.key).startswith(f"wiki_body_ref_{child['id']}_"))
        button.click().run()
        assert not app.exception, str(app.exception)
        assert app.session_state['agent_wiki_selected_page_id'] == child['id']
        assert any('TAIL_UNCERTAINTY' in str(m.value) for m in app.markdown)
    finally:
        wiki._CACHE.clear()
        wiki_browser._cached_wiki_snapshot.clear()
        wiki_browser._cached_context_section.clear()
