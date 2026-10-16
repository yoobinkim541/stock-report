import pytest

from reports.wiki_cleanup import plan_cleanup


def page(identity, **fields):
    return {'id': identity, 'title': 'Same', 'surface': 'market', 'kind': 'note', 'status': 'draft',
            'body': '', 'summary': '', 'source_refs': [], 'evidence_ids': [], **fields}


@pytest.mark.parametrize('field,value', [
    ('claims', [{'claim_id': 'one', 'text': 'Retained claim'}]),
    ('limitations', ['Missing audited demand data']),
    ('relations', [{'type': 'compete', 'scope_explanation': 'Counterevidence'}]),
    ('integrations', [{'revision_id': 'a' * 64, 'role': 'conflict'}]),
])
def test_cleanup_keeps_metadata_only_compiled_knowledge(field, value):
    assert plan_cleanup([page('keep', source_knowledge={field: value})]) == []


def test_cleanup_does_not_collapse_same_prose_with_distinct_claim_ledgers():
    pages = [page('one', body='Same prose', source_knowledge={'claims': [{'text': 'Capacity only'}]}),
             page('two', body='Same prose', source_knowledge={'claims': [{'text': 'Orders uncertain'}]})]
    assert plan_cleanup(pages) == []


def test_cleanup_keeps_knowledge_in_legacy_raw_metadata():
    assert plan_cleanup([page('keep', raw={'source_knowledge': {'limitations': ['OCR uncertainty']}})]) == []


def test_cleanup_still_allows_truly_empty_knowledge_metadata():
    actions = plan_cleanup([page('empty', source_knowledge={'claims': [], 'limitations': []})])
    assert len(actions) == 1 and actions[0]['reason'] == 'empty_draft'
