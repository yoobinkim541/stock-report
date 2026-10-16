import pytest


def module():
    from scripts import run_public_wiki_pilot
    return run_public_wiki_pilot


def test_public_message_gate_rejects_private_channels_and_other_sources():
    public = {'provider': 'telegram:insidertracking', 'scope': 'public_message',
              'canonical_url': 'https://t.me/insidertracking/66112'}
    assert module().is_public(public)
    for changes in ({'canonical_url': 'https://t.me/c/123/4'},
                    {'canonical_url': 'https://t.me/+private'}, {'scope': 'authorized_article'},
                    {'provider': 'saveticker'}, {'canonical_url': 'https://t.me/insidertracking/1?token=secret'}):
        assert not module().is_public({**public, **changes})


def test_pilot_selects_only_frozen_approved_revisions_and_caps_five():
    revisions = [{'revision_id': identity, 'provider': 'telegram:insidertracking',
                  'scope': 'public_message', 'canonical_url': 'https://t.me/insidertracking/1'}
                 for identity in module().APPROVED_REVISIONS]
    extras = [{'revision_id': 'f' * 64, **{k: v for k, v in revisions[0].items() if k != 'revision_id'}}]
    assert len(module().select_sources(revisions + extras)) == 5
    assert all(source['revision_id'] in module().APPROVED_REVISIONS
               for source in module().select_sources(revisions + extras))
    with pytest.raises(ValueError, match='pilot_sources_unavailable'):
        module().select_sources(revisions[:4])


def test_privacy_filter_excludes_uncommitted_or_changed_live_targets():
    state = {'candidate_ids': ['public', 'personal', 'changed'], 'unexamined': ['public', 'personal', 'changed'],
             'cursor': 0, 'selected': [], 'examined': []}
    filtered = module().filter_targets(state, {'public'})
    assert filtered['candidate_ids'] == filtered['unexamined'] == ['public']
    assert state['candidate_ids'] == ['public', 'personal', 'changed']


def test_pilot_success_requires_actual_draft_pages_and_complete_repeat_effects():
    repeat = {'data_committed': 0, 'compile_failed': 0, 'routing_pending': 0,
              'effects_pending': 0, 'stale_base': 0}
    states = [{'compile_state': 'compiled'}] * 5
    assert not module().verify_repeat(repeat, [], states)
    assert module().verify_repeat(repeat, [{'status': 'draft'}], states)
    for field in ('effects_pending', 'stale_base'):
        assert not module().verify_repeat({**repeat, field: 1}, [{'status': 'draft'}], states)
    assert not module().verify_repeat(repeat, [None], states)
