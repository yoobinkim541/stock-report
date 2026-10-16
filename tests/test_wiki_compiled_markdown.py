import json

from agent_console import qmd_search, wiki


def test_existing_unsplit_long_record_keeps_tail_in_page_and_markdown():
    body = "## Evidence\n\n" + "Detailed paragraph with a caveat.\n\n" * 800 + "TAIL COUNTEREVIDENCE"
    record = {'id': 'legacy-long', 'title': 'Legacy knowledge', 'body': body,
              'tags': ['wiki'], 'artifacts': ['https://example.org/evidence']}
    page = wiki._record_to_page(record)
    assert page['body'] == body
    markdown = qmd_search._page_markdown(page, page['id'])
    assert body in markdown
    assert record['body'] == body


def test_full_document_read_does_not_expand_chat_excerpt(monkeypatch):
    body = 'A' * 14000 + 'TAIL COUNTEREVIDENCE'
    page = wiki._record_to_page({'id': 'long', 'body': body, 'tags': ['wiki']})
    monkeypatch.setattr(wiki, '_title_lookup_for', lambda _: {})
    context = wiki.build_context_section(pages=[page])
    assert 'TAIL COUNTEREVIDENCE' not in context
    assert '- 본문: ' + body[:1600] in context
    assert 'A' * 1601 not in context


def test_long_document_read_retains_existing_secret_redaction():
    body = 'Evidence. ' * 1600 + '\npassword=synthetic-private-token\nTAIL'
    page = wiki._record_to_page({'id': 'long', 'body': body, 'tags': ['wiki']})
    assert 'synthetic-private-token' not in page['body']
    assert 'password=<redacted>' in page['body']
    assert page['body'].endswith('TAIL')


def test_short_secret_redaction_expansion_cannot_cut_tail():
    body = 'Evidence. ' * 1600 + '\ntoken=x\nTAIL'
    page = wiki._record_to_page({'id': 'long', 'body': body, 'tags': ['wiki']})
    assert 'token=<redacted>' in page['body']
    assert page['body'].endswith('TAIL')


def test_compiled_claims_caveats_and_relations_survive_markdown_export():
    claim = {"claim_id": "claim-one", "source_revision_id": "revision-one",
             "text": "Complete attributed claim " * 500 + "TAIL EXCEPTION",
             "as_of": "2026-10-13", "scope": "Capacity, not orders", "knowledge_state": "draft"}
    knowledge = {"claims": [claim], "limitations": ["OCR must be checked against the original"],
                 "relations": [{"type": "compete", "from_claim_id": "claim-one",
                                "to_claim_id": "old-claim", "scope_explanation": "Conflicting demand report"}],
                 "integrations": [{"revision_id": "revision-one", "role": "conflict", "evidence_ids": ["e1"]}],
                 "private_notes": "DO NOT EXPORT PRIVATE METADATA"}
    markdown = qmd_search._page_markdown({"id": "provenance-child", "title": "Provenance",
        "body": "Claim and evidence provenance", "source_knowledge": knowledge}, "provenance-child")
    block = markdown.split("## Source Knowledge\n\n```json\n", 1)[1].split("\n```", 1)[0]
    loaded = json.loads(block)
    assert loaded == {key: knowledge[key] for key in ("claims", "limitations", "relations", "integrations")}
    assert "DO NOT EXPORT PRIVATE METADATA" not in markdown
