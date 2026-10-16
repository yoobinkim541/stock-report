from agent_console import wiki


def page(identity, *, status='draft', links=None, backlinks=None):
    return {'id': identity, 'title': identity, 'status': status,
            'tags': ['ticker:NVDA'], 'source_refs': ['https://example.test/source'],
            'links': links or [], 'backlinks': backlinks or []}


def test_archived_pages_do_not_generate_active_relation_warnings():
    issues = wiki._lint_relational_issues([page('current'), page('historical', status='archived')])
    assert not any(issue['code'] == 'missing_cross_ref' for issue in issues)
    assert [issue['page_id'] for issue in issues if issue['code'] == 'orphan_page'] == ['current']


def test_link_only_to_archived_or_missing_page_does_not_hide_active_orphan():
    issues = wiki._lint_relational_issues([
        page('current', links=['historical', 'deleted']),
        page('historical', status='archived', links=['current']),
    ])
    assert any(issue['code'] == 'orphan_page' and issue['page_id'] == 'current' for issue in issues)


def test_active_links_still_suppress_duplicate_direct_link_warning():
    issues = wiki._lint_relational_issues([page('a', links=['b']), page('b')])
    assert not any(issue['code'] in {'missing_cross_ref', 'orphan_page'} for issue in issues)


def test_duplicate_tags_and_refs_do_not_inflate_cross_ref_group_cap():
    a, b = page('a'), page('b')
    a['tags'] *= 20
    b['tags'] *= 20
    a['source_refs'] *= 20
    b['source_refs'] *= 20
    issues = wiki._lint_relational_issues([a, b])
    assert len([issue for issue in issues if issue['code'] == 'missing_cross_ref']) == 1


def test_shared_ticker_or_source_does_not_recommend_merging_opposite_claims():
    a, b = page('bull'), page('bear')
    a.update(body='Capacity improved, demand still uncertain.')
    b.update(body='Orders fell; realized demand declined.')
    issue = next(item for item in wiki._lint_relational_issues([a, b])
                 if item['code'] == 'missing_cross_ref')
    assert issue['suggested'] == 'review_link'
    assert {issue['page_id'], issue['related_page_id']} == {'bull', 'bear'}
