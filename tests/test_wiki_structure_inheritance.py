import pytest

from agent_console import shared_memory, wiki, wiki_publication


@pytest.fixture(autouse=True)
def local_wiki(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_CONSOLE_SHARED_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_ARTIFACTS_DIR', str(tmp_path / 'artifacts'))
    monkeypatch.setenv('AGENT_CONSOLE_QMD_ENABLED', '0')
    monkeypatch.setattr(wiki, '_debounced_rebuild', lambda: None)
    monkeypatch.setattr(shared_memory, '_schedule_context_memory_summary', lambda: None)
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def save(identity, **fields):
    return wiki.upsert_page({'id': identity, 'title': identity,
        'body': 'Attributed synthetic evidence for ' + identity,
        'surface': 'market', 'kind': 'concept', 'status': 'draft',
        'source_refs': ['https://example.org/' + identity],
        'evidence_ids': [identity + '-evidence'], **fields})


def assert_publishable():
    files = wiki_publication.render_candidate(wiki_publication.capture_snapshot())
    result = wiki_publication.validate_candidate(files)
    assert result['ok'], result['errors']


@pytest.mark.parametrize('long_ids', [False, True])
def test_merge_absorbs_both_documents_and_retires_their_children(long_ids):
    target_id = 'source-topic-' + 'a' * 64 if long_ids else 'target'
    source_id = 'source-topic-' + 'b' * 64 if long_ids else 'source'
    target_child = 'source-topic-' + 'c' * 64 if long_ids else 'target-child'
    source_child = 'source-topic-' + 'd' * 64 if long_ids else 'source-child'
    target = save(target_id, tags=['target-theme', 'split_into:' + target_child])
    source = save(source_id, tags=['source-theme', 'split_into:' + source_child])
    target_child_body = save(target_child, parent_page_id=target_id, tags=['split_from:' + target_id])['body']
    source_child_body = save(source_child, parent_page_id=source_id, tags=['split_from:' + source_id])['body']
    assert_publishable()

    assert wiki._merge_pages([source_id], target_id, 'Combined attributed evidence')

    merged = wiki.get_page(target_id)
    snapshot = wiki.semantic_snapshot(target_id)
    archived = wiki.get_page(source_id)
    for text in (target['body'], source['body'], target_child_body, source_child_body):
        assert text in snapshot['body']
    assert not snapshot['children']
    assert not any(tag.startswith('split_into:') for tag in merged['tags'])
    assert 'source-theme' in merged['tags']
    assert set(source['source_refs']) <= set(merged['source_refs'])
    assert archived['status'] == 'archived'
    assert archived['body'] == source['body']
    assert 'split_into:' + source_child in archived['tags']
    for child_id, parent_id in ((target_child, target_id), (source_child, source_id)):
        child = wiki.get_page(child_id)
        assert child['status'] == 'archived' and child['parent_page_id'] == parent_id
    assert wiki.get_page(source_child)['merged_into'] == target_id
    assert_publishable()


def test_merge_does_not_inherit_source_ancestry_or_superseded_flag():
    save('root', tags=['split_into:source'])
    save('target')
    save('source', parent_page_id='root',
         tags=['split_from:root', 'split_superseded', 'ordinary-theme'])
    assert_publishable()

    assert wiki._merge_pages(['source'], 'target', 'Synthetic synthesis')

    target = wiki.get_page('target')
    assert not target['parent_page_id']
    assert 'split_from:root' not in target['tags']
    assert 'split_superseded' not in target['tags']
    assert 'ordinary-theme' in target['tags']
    assert wiki.get_page('source')['parent_page_id'] == 'root'
    assert 'split_from:root' in wiki.get_page('source')['tags']
    assert_publishable()


@pytest.mark.parametrize('long_ids', [False, True])
def test_merge_preserves_target_ancestry_without_adopting_source_parent(long_ids):
    target_root = 'source-topic-' + '1' * 64 if long_ids else 'target-root'
    source_root = 'source-topic-' + '2' * 64 if long_ids else 'source-root'
    target_id = 'source-topic-' + '3' * 64 if long_ids else 'target'
    source_id = 'source-topic-' + '4' * 64 if long_ids else 'source'
    save(target_root, tags=['split_into:' + target_id])
    save(source_root, tags=['split_into:' + source_id])
    target = save(target_id, parent_page_id=target_root,
                  tags=['split_from:' + target_root])
    source = save(source_id, parent_page_id=source_root,
                  tags=['split_from:' + source_root])
    assert_publishable()

    assert wiki._merge_pages([source_id], target_id, 'Synthetic combined evidence')

    merged = wiki.get_page(target_id)
    assert merged['parent_page_id'] == target_root
    assert [tag for tag in merged['tags'] if tag.startswith('split_from:')] == [
        'split_from:' + target_root]
    assert target['body'] in merged['body']
    assert source['body'] in merged['body']
    assert wiki.get_page(source_id)['parent_page_id'] == source_root
    assert wiki.get_page(source_id)['status'] == 'archived'
    assert_publishable()


@pytest.mark.parametrize('long_ids', [False, True])
def test_nested_manual_split_assigns_only_direct_parent_and_preserves_old_children(long_ids):
    root_id = 'source-topic-' + 'e' * 64 if long_ids else 'root'
    source_id = 'source-topic-' + 'f' * 64 if long_ids else 'source'
    save(root_id, tags=['split_into:' + source_id])
    source = save(source_id, parent_page_id=root_id,
        tags=['split_from:' + root_id, 'split_into:existing-child', 'ordinary-theme'])
    save('existing-child', parent_page_id=source_id, tags=['split_from:' + source_id])
    assert_publishable()

    result = wiki._split_page(source_id, ['First child', 'Second child'],
                             ['First attributed evidence', 'Second attributed evidence'])

    assert len(result['created']) == 2
    for identity in result['created']:
        child = wiki.get_page(identity)
        assert child['parent_page_id'] == source_id
        assert [tag for tag in child['tags'] if tag.startswith('split_from:')] == [
            'split_from:' + source_id]
        assert not any(tag.startswith('split_into:') for tag in child['tags'])
        assert 'ordinary-theme' in child['tags']
        assert child['source_refs'] == source['source_refs']
        assert child['evidence_ids'] == source['evidence_ids']
    archived = wiki.get_page(source_id)
    assert archived['body'] == source['body']
    assert archived['parent_page_id'] == root_id
    assert 'split_into:existing-child' in archived['tags']
    assert wiki.get_page('existing-child')['parent_page_id'] == source_id
    assert_publishable()
