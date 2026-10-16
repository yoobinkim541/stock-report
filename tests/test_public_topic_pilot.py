"""Synthetic checks for the explicitly approved two-attempt topic pilot."""
import importlib
import hashlib
import json
import pytest

from agent_console import shared_memory, wiki
from reports import wiki_public_budget as quota
from test_wiki_topic_transaction import transaction, evidence, NOW


def api():
    return importlib.import_module('scripts.run_public_topic_pilot')


@pytest.fixture
def pilot_env(transaction):
    data, journal, rows = transaction
    current = data['source']['revision_id']
    rows = [*rows, {'id': 'current-note', 'title': 'Shipping current source', 'body': 'Read only',
                   'tags': ['wiki', 'surface:market', 'kind:note', 'status:draft', 'compiled_source']}]
    operation = hashlib.sha256(b'pilot-source-note').hexdigest()
    journal._path('operations', operation).write_text(json.dumps({'schema_version': 1,
        'operation_id': operation, 'stage': 'committed', 'revision_id': current,
        'artifacts': 'done', 'search': 'done', 'log': 'done',
        'batch': {'target_ids': ['current-note'], 'expected_after': {
            'current-note': wiki._source_fingerprint('current-note', rows)}}}))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    return data, journal, rows


def revisions(transaction):
    data, journal, _ = transaction
    current = data['source']['revision_id']
    context = data['context']['notes'][0]['revision_id']
    follow = next(p.stem for p in (journal.root / 'sources').glob('*.json') if p.stem not in {current, context})
    return (context, current, follow)


def test_pilot_dry_run_never_reserves_or_calls_model(pilot_env):
    _, journal, _ = pilot_env
    before = list(journal.root.rglob('*'))
    result = api().run(journal, revisions=revisions(pilot_env), now=NOW)
    assert result['status'] == 'ready'
    assert result['attempts_used'] == 0
    assert list(journal.root.rglob('*')) == before


def test_exhausted_daily_budget_leaves_pilot_untouched(pilot_env):
    _, journal, _ = pilot_env
    for index in range(20):
        quota.reserve(journal.root / 'automatic-budget.json', format(index, '064x'), NOW)
    result = api().run(journal, revisions=revisions(pilot_env), now=NOW, apply=True,
                       llm_fn=lambda _: (_ for _ in ()).throw(AssertionError('model called at cap')))
    assert result['status'] == 'quota_exhausted'
    assert result['attempts_used'] == 0
    assert not (journal.root / 'topic-pilot.json').exists()
    assert quota.status(journal.root / 'automatic-budget.json', NOW)['count'] == 20


def test_refused_seed_is_one_attempt_not_silent_retry(pilot_env):
    _, journal, _ = pilot_env
    calls = []
    def refuse(prompt):
        calls.append(prompt)
        return json.dumps({'targets': [], 'refusal': 'Related reports need more verification'})
    result = api().run(journal, revisions=revisions(pilot_env), now=NOW, apply=True, llm_fn=refuse)
    assert result['status'] == 'refused'
    assert result['attempts_used'] == 1
    assert len(calls) == 1
    again = api().run(journal, revisions=revisions(pilot_env), now=NOW, apply=True, llm_fn=refuse)
    assert again['status'] == 'refused'
    assert len(calls) == 1
    assert quota.status(journal.root / 'automatic-budget.json', NOW)['count'] == 1


def test_success_uses_exactly_two_shared_reservations_and_replay_is_noop(pilot_env, monkeypatch):
    _, journal, _ = pilot_env
    calls = []
    def model(prompt):
        calls.append(prompt)
        data = json.loads(prompt.rsplit('\n', 1)[1])
        current = data['compiled']
        target = next(iter(data['editable_topics']), None)
        units = [(current['revision_id'], current['claims'][0])]
        if target is None:
            context = data['read_only_source_context']['notes'][0]
            units.append((context['revision_id'], context['claims'][0]))
        labels = {v['revision_id']: k for k, v in data['source_map'].items()}
        proposed = {'page_id': target['page_id'] if target else None,
            'before_hash': target['revision_hash'] if target else None,
            'contributing_revision_ids': [r for r, _ in units], 'scope_explanation': 'Dated reports',
            'relations': [], 'additions': [{'claim_ids': [c['claim_id']], 'revision_ids': [r],
                'as_of': c['as_of'], 'scope': c['scope'], 'body': f'Attributed [{labels[r]}].'}
                for r, c in units]}
        if target is None:
            proposed.update(title='Shipping topic', kind='concept', surface='market', purpose='topic_synthesis')
        return json.dumps({'targets': [proposed], 'refusal': None})
    result = api().run(journal, revisions=revisions(pilot_env), now=NOW, apply=True, llm_fn=model)
    assert result['status'] == 'awaiting_semantic_review'
    assert result['attempts_used'] == 2
    assert result['quota']['count'] == 2
    assert len(calls) == 2
    before = shared_memory.inspect_records()
    repeated = api().run(journal, revisions=revisions(pilot_env), now=NOW, apply=True, llm_fn=model)
    assert repeated['status'] == 'awaiting_semantic_review'
    assert repeated['quota']['count'] == 2
    assert len(calls) == 2
    assert shared_memory.inspect_records() == before
    load = journal.load_topic_operation
    def unrelated(identity):
        receipt = load(identity)
        receipt['batch']['proposal']['revision_id'] = 'f' * 64
        return receipt
    monkeypatch.setattr(journal, 'load_topic_operation', unrelated)
    with pytest.raises(ValueError, match='pilot_commit_unverified'):
        api().run(journal, revisions=revisions(pilot_env), now=NOW)


def test_interrupted_intent_never_silently_calls_again(pilot_env):
    _, journal, _ = pilot_env
    selected = revisions(pilot_env)
    (journal.root / 'topic-pilot.json').write_text(json.dumps({'schema_version': 1,
        'revisions': list(selected), 'attempts': [{'stage': 'seed', 'status': 'interrupted',
            'revision_id': selected[1], 'reservation': None}],
        'topic_ids': [], 'baseline': None}))
    result = api().run(journal, revisions=selected, now=NOW, apply=True,
        llm_fn=lambda _: pytest.fail('interrupted pilot replayed model'))
    assert result['status'] == 'interrupted'
    assert result['attempts_used'] == 1
    assert quota.status(journal.root / 'automatic-budget.json', NOW)['count'] == 0


def test_fabricated_committed_state_cannot_report_success(pilot_env):
    _, journal, _ = pilot_env
    selected = revisions(pilot_env)
    (journal.root / 'topic-pilot.json').write_text(json.dumps({'schema_version': 1,
        'revisions': list(selected), 'attempts': [{'status': 'committed'}] * 2,
        'topic_ids': ['invented'], 'baseline': None}))
    with pytest.raises(ValueError, match='pilot_state_invalid'):
        api().run(journal, revisions=selected, now=NOW)
