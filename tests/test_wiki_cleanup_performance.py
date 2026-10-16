from agent_console import wiki
from reports import wiki_cleanup


def test_cleanup_uses_supplied_corpus_without_reloading_wiki(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('cleanup must inspect its supplied snapshot, not reload all pages')

    monkeypatch.setattr(wiki, 'split_child_ids', forbidden)
    pages = [
        {'id': 'parent', 'kind': 'concept', 'status': 'draft', 'body': '', 'links': []},
        {'id': 'child', 'parent_page_id': 'parent', 'kind': 'concept', 'status': 'draft', 'body': ''},
        {'id': 'empty', 'kind': 'concept', 'status': 'draft', 'body': ''},
    ]
    assert [action['page_id'] for action in wiki_cleanup.plan_cleanup(pages)] == ['empty']
