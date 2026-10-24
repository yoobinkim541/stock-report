import pytest

from agent_console import wiki_publication as publication


def snapshot(*records):
    result = {'records': list(records), 'captured_at': '2026-10-22T12:00:00+00:00',
              'schema_text': 'Schema\n', 'schema_sha256': publication._sha(b'Schema\n')}
    result['snapshot_sha256'] = publication._snapshot_hash(result)
    return result


def row(identity, **fields):
    return {'id': identity, 'title': identity, 'body': 'Synthetic evidence',
            'tags': ['wiki'], 'source': {'surface': 'wiki'}, **fields}


@pytest.mark.parametrize('title', ['Evidence [0001]', '[Draft] Evidence',
                                  'Evidence [one] and [two]'])
def test_balanced_brackets_in_unique_title_round_trip_without_record_rewrite(title):
    body = 'See [[' + title + ']].'
    original = snapshot(row('source', body=body), row('target', title=title))
    files = publication.render_candidate(original)
    prose = files['notes/source.md'].decode().split('\n## Canonical Record\n', 1)[0]

    assert '[[target|' + title + ']]' in prose
    assert publication.validate_candidate(files) == {'ok': True, 'errors': []}
    assert original['records'][0]['body'] == body


@pytest.mark.parametrize('duplicate', [False, True])
def test_bracket_title_still_requires_one_exact_existing_target(duplicate):
    records = [row('source', body='[[Unknown [0001]]]')]
    if duplicate:
        records.extend([row('a', title='Unknown [0001]'),
                        row('b', title='Unknown [0001]')])
    files = publication.render_candidate(snapshot(*records))
    result = publication.validate_candidate(files)
    assert not result['ok']
    assert any(error['code'] == 'missing_markdown_link' for error in result['errors'])


def test_unbalanced_open_bracket_does_not_evade_missing_link_validation():
    files = publication.render_candidate(snapshot(
        row('source', body='[[Unknown [0001]]')))
    result = publication.validate_candidate(files)
    assert not result['ok']
    assert any(error['code'] == 'missing_markdown_link' for error in result['errors'])
