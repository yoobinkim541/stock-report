from agent_console import wiki
from copy import deepcopy


def test_chat_context_reads_structured_counterevidence_within_excerpt_budget(monkeypatch):
    page = {'id': 'capacity', 'title': 'Capacity', 'body': 'Positive prose. ' * 2000,
            'source_knowledge': {
                'claims': [{'text': 'Capacity rose', 'as_of': '2026-10-09',
                            'scope': 'Capacity, not orders', 'knowledge_state': 'draft',
                            'source_revision_id': 'revision-one'}],
                'limitations': ['Realized orders are not established.'],
                'relations': [{'type': 'compete', 'scope_explanation': 'Demand remains uncertain.'}],
                'private_notes': ['DO NOT SEND THIS'],
            }}
    monkeypatch.setattr(wiki, '_title_lookup_for', lambda _: {})
    context = wiki.build_context_section(pages=[page])
    assert 'Realized orders are not established.' in context
    assert 'Demand remains uncertain.' in context
    assert 'Capacity, not orders' in context and '2026-10-09' in context
    assert 'draft' in context and 'revision-one' in context
    assert 'DO NOT SEND THIS' not in context
    excerpt = wiki._context_body_excerpt(page)
    assert len(excerpt) <= 1600
    assert 'Positive prose.' in excerpt
    assert '발췌' in excerpt


def test_structured_only_page_supplies_knowledge_to_chat(monkeypatch):
    page = {'id': 'only', 'source_knowledge': {'limitations': ['Uncertainty retained.']}}
    monkeypatch.setattr(wiki, '_title_lookup_for', lambda _: {})
    assert 'Uncertainty retained.' in wiki.build_context_section(pages=[page])


def test_prose_only_excerpt_retains_original_contract():
    assert wiki._context_body_excerpt({'body': 'A' * 2000}) == 'A' * 1600


def test_large_structured_excerpt_is_bounded_redacted_and_nonmutating():
    page = {'body': 'B' * 10000, 'source_knowledge': {
        'claims': [{'text': 'token=x ' + 'Claim ' * 1000, 'scope': 'Scope ' * 1000,
                    'knowledge_state': 'draft'}] * 20,
        'limitations': ['password=secret ' + 'Caveat ' * 1000] * 20,
        'relations': [{'type': 'compete', 'scope_explanation': 'Uncertain ' * 1000}] * 20,
        'private_notes': ['DO NOT SEND'],
    }}
    original = deepcopy(page)
    excerpt = wiki._context_body_excerpt(page)
    assert len(excerpt) <= 1600
    assert 'token=x' not in excerpt and 'password=secret' not in excerpt
    assert 'DO NOT SEND' not in excerpt
    assert '전체 검토 아님' in excerpt
    assert 'knowledge_state' in excerpt and 'draft' in excerpt
    assert 'scope' in excerpt
    assert page == original


def test_malformed_knowledge_does_not_expand_or_crash_excerpt():
    for knowledge in (None, 'bad data', [], {'claims': [None], 'limitations': [object()]}):
        assert wiki._context_body_excerpt({'body': 'A' * 2000, 'source_knowledge': knowledge}) == 'A' * 1600


def test_nested_private_values_and_empty_claims_are_not_context():
    page = {'body': 'Prose', 'source_knowledge': {'claims': [
        {'text': {'private_notes': 'DO NOT SEND'}}, {}, {'private_notes': 'DO NOT SEND'}]}}
    assert wiki._context_body_excerpt(page) == 'Prose'


def test_relation_can_identify_the_visible_claim():
    page = {'source_knowledge': {
        'claims': [{'claim_id': 'claim-one', 'text': 'Attributed capacity claim', 'knowledge_state': 'draft'}],
        'relations': [{'type': 'compete', 'from_claim_id': 'claim-one',
                       'to_claim_id': 'claim-other', 'scope_explanation': 'Different time window'}]}}
    excerpt = wiki._context_body_excerpt(page)
    assert '"claim_id": "claim-one"' in excerpt
    assert '"from_claim_id": "claim-one"' in excerpt


def test_missing_claim_state_is_explicitly_unknown():
    excerpt = wiki._context_body_excerpt({'source_knowledge': {'claims': [{'text': 'Attributed report'}]}})
    assert '"knowledge_state": "unknown"' in excerpt


def test_conflict_disposition_fits_real_revision_identity():
    excerpt = wiki._context_body_excerpt({'body': 'Prose ' * 2000, 'source_knowledge': {
        'integrations': [{'revision_id': 'a' * 64, 'role': 'conflict',
                          'relation_state': 'unresolved', 'relation_note': 'Conflict is not adjudicated.'}]}})
    assert 'unresolved' in excerpt
    assert 'a' * 64 in excerpt
    assert len(excerpt) <= 1600


def test_unresolved_conflict_is_not_hidden_by_older_support_integrations():
    integrations = [{'revision_id': str(i) * 64, 'role': 'support'} for i in range(5)]
    integrations.append({'revision_id': 'a' * 64, 'role': 'conflict', 'relation_state': 'unresolved'})
    excerpt = wiki._context_body_excerpt({'source_knowledge': {'integrations': integrations}})
    assert 'unresolved' in excerpt
    assert 'a' * 64 in excerpt
