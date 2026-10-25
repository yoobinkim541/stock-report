"""Synthetic checks for the explicitly approved two-attempt topic pilot."""
import importlib
import hashlib
import json
import pytest

from agent_console import shared_memory, wiki
from reports import wiki_public_budget as quota
from reports import wiki_topic_pilot_state as pilot
from reports.wiki_source_notes import _bytes, _sha
from test_wiki_topic_transaction import transaction, evidence, NOW


def api():
    return importlib.import_module('scripts.run_public_topic_pilot')


def authorize(journal, selected):
    """Synthetic retained operator approval for a schema-1 pilot (never a production initializer)."""
    approval = {'schema_version': 1, 'approval_ref': 'synthetic-test-approval', 'revision_ids': list(selected),
                'max_attempts': 2, 'daily_limit': 20, 'decision': 'approved'}
    identity = pilot.authorization_identity(approval)
    state_path = journal.root / 'topic-pilot.json'
    state_path.write_bytes(_bytes({'schema_version': 1, 'revisions': list(selected), 'attempts': [],
                                   'topic_ids': [], 'baseline': None}))
    proof = journal.root / 'topic-pilot-history' / 'synthetic-approval.json'
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_bytes(_bytes({'approval': approval, 'state_path': str(state_path)}))
    budget = journal.root / 'automatic-budget.json'
    if not budget.exists():
        budget.write_bytes(_bytes({'schema': 1, 'days': {}}))
    guard = {'schema_version': 1, 'authorization_id': identity, 'approval': approval,
             'state_path': str(state_path), 'state_sha256': _sha(state_path.read_bytes()),
             'history_proofs': {'topic-pilot-history/synthetic-approval.json': _sha(proof.read_bytes())},
             'lifecycle': 'initialized', 'attempts': []}
    guard_path = journal.root / 'topic-pilot-authorizations' / f'{identity}.json'
    guard_path.parent.mkdir(parents=True, exist_ok=True)
    guard_path.write_bytes(_bytes(guard))
    for name in ('automatic-run', 'topic-pilot-authorizations', 'topic-pilot.json'):
        (journal.root / (name + '.lock')).touch()


def record_attempts(journal, attempts, **changes):
    """Persist pilot history through the production state API (intents, guard, then state)."""
    with pilot.pilot_locks(journal):
        guard = pilot._guard(journal)
        state = json.loads(pilot._read(journal, 'topic-pilot.json'))
        state.update(attempts=attempts, **changes)
        pilot.persist_pilot_state(journal, state, guard)


@pytest.fixture
def pilot_env(transaction, monkeypatch):
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
    # The production constant names the real approved revisions; this synthetic
    # journal is approved for its own three revisions under the same guard.
    selected = revisions((data, journal, rows))
    monkeypatch.setattr(api(), 'APPROVED_REVISIONS', selected)
    authorize(journal, selected)
    return data, journal, rows


def revisions(transaction):
    data, journal, _ = transaction
    current = data['source']['revision_id']
    context = data['context']['notes'][0]['revision_id']
    follow = next(p.stem for p in (journal.root / 'sources').glob('*.json') if p.stem not in {current, context})
    return (context, current, follow)


def test_unapproved_revisions_are_refused_before_any_state_access(pilot_env):
    _, journal, _ = pilot_env
    context, current, follow = revisions(pilot_env)
    before = list(journal.root.rglob('*'))
    result = api().run(journal, revisions=(current, context, follow), now=NOW, apply=True,
                       llm_fn=lambda _: pytest.fail('unapproved pilot called model'))
    assert result == {'status': 'needs_review', 'reason': 'pilot_revisions_invalid', 'attempts_used': None}
    assert list(journal.root.rglob('*')) == before


def test_missing_authorization_never_initializes_pilot(pilot_env):
    _, journal, _ = pilot_env
    for path in (journal.root / 'topic-pilot-authorizations').glob('*.json'):
        path.unlink()
    result = api().run(journal, revisions=revisions(pilot_env), now=NOW, apply=True,
                       llm_fn=lambda _: pytest.fail('unauthorized pilot called model'))
    assert result['status'] == 'needs_review' and result['reason'] == 'pilot_authorization_ambiguous'
    assert quota.status(journal.root / 'automatic-budget.json', NOW)['count'] == 0


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
    state_before = (journal.root / 'topic-pilot.json').read_bytes()
    result = api().run(journal, revisions=revisions(pilot_env), now=NOW, apply=True,
                       llm_fn=lambda _: (_ for _ in ()).throw(AssertionError('model called at cap')))
    assert result['status'] == 'quota_exhausted'
    assert result['attempts_used'] == 0
    assert (journal.root / 'topic-pilot.json').read_bytes() == state_before
    assert not (journal.root / 'topic-pilot-intents').exists()
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
    unverified = api().run(journal, revisions=revisions(pilot_env), now=NOW)
    assert unverified['status'] == 'needs_review' and unverified['attempts_used'] is None
    assert unverified['reason'] == 'pilot_commit_unverified'


def test_interrupted_intent_never_silently_calls_again(pilot_env):
    _, journal, _ = pilot_env
    selected = revisions(pilot_env)
    record_attempts(journal, [{'stage': 'seed', 'status': 'interrupted', 'revision_id': selected[1],
                               'reservation': None, 'intent_id': 'a' * 64}])
    result = api().run(journal, revisions=selected, now=NOW, apply=True,
        llm_fn=lambda _: pytest.fail('interrupted pilot replayed model'))
    assert result['status'] == 'interrupted'
    assert result['attempts_used'] == 1
    assert quota.status(journal.root / 'automatic-budget.json', NOW)['count'] == 0


def test_fabricated_committed_state_cannot_report_success(pilot_env):
    _, journal, _ = pilot_env
    selected = revisions(pilot_env)
    attempts = [{'stage': stage, 'status': 'committed', 'revision_id': revision, 'intent_id': intent * 64,
                 'reservation': {'id': intent * 32, 'revision_id': revision, 'at': NOW.isoformat()},
                 'operation_id': operation * 64, 'protocol_version': 2}
                for stage, revision, intent, operation in (('seed', selected[1], 'b', 'c'),
                                                           ('followup', selected[2], 'd', 'e'))]
    # Internally consistent state and guard, but no reservations, operations or witnesses exist.
    record_attempts(journal, attempts, topic_ids=['invented'], baseline=[{'fabricated': True}])
    result = api().run(journal, revisions=selected, now=NOW, apply=True,
                       llm_fn=lambda _: pytest.fail('fabricated pilot called model'))
    assert result['status'] == 'needs_review' and result['attempts_used'] is None
    assert result['reason'].startswith('pilot_')
    assert quota.status(journal.root / 'automatic-budget.json', NOW)['count'] == 0
