import copy
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from agent_console import shared_memory, wiki, wiki_publication as publication
from reports.wiki_source_journal import Journal
from test_wiki_topic_transaction import prepare, recover


@pytest.fixture
def env(tmp_path, monkeypatch):
    from reports import wiki_publication_effects as effects
    monkeypatch.setenv('AGENT_CONSOLE_SHARED_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_ARTIFACTS_DIR', str(tmp_path / 'wiki'))
    monkeypatch.setenv('AGENT_CONSOLE_QMD_ENABLED', '0')
    monkeypatch.setattr(wiki, '_debounced_rebuild', lambda: None)
    monkeypatch.setattr(shared_memory, '_schedule_context_memory_summary', lambda: None)
    wiki._CACHE.clear()
    wiki.upsert_page({'id': 'one', 'title': 'One', 'body': 'Preserved knowledge.',
                      'kind': 'concept', 'surface': 'market'})
    journal = Journal(tmp_path / 'journal')
    revision = 'a' * 64
    journal.save_compilation({'revision_id': revision, 'role': 'support'},
                             {'revision_id': revision, 'body': 'Compiled'})
    rows = shared_memory._read_jsonl_checked(shared_memory._paths()['events'])
    batch = {'operation_id': 'b' * 64, 'revision_id': revision, 'target_ids': ['one'],
             'expected_after': {'one': wiki._source_fingerprint('one', rows)},
             'expected_before': {'one': None}, 'upserts': []}
    journal.prepare(batch)
    journal.transition(batch['operation_id'], stage='committed')
    monkeypatch.setattr(effects, 'publication_root', lambda: tmp_path / 'publication')
    budget = tmp_path / 'quota.json'
    budget.write_bytes(b'{"reserved":17}')
    yield effects, journal, batch['operation_id'], tmp_path / 'publication'
    assert budget.read_bytes() == b'{"reserved":17}'


def test_activation_history_failure_repairs_without_republishing(env, monkeypatch):
    effects, journal, op, root = env
    append = wiki._append_wiki_log
    monkeypatch.setattr(wiki, '_append_wiki_log', lambda *a, **k: (_ for _ in ()).throw(OSError('history')))
    first = effects.repair_publication(op, journal=journal)
    assert first['artifacts'] == 'done' and first['log'] == 'failed'
    pointer = (root / 'current.json').read_bytes()
    monkeypatch.setattr(wiki, '_append_wiki_log', append)
    monkeypatch.setattr(publication, 'publish_snapshot', lambda *a, **k: pytest.fail('republished'))
    second = effects.repair_publication(op, journal=journal)
    assert second['generation_id'] == first['generation_id'] and second['log'] == 'done'
    assert effects.repair_publication(op, journal=journal) == second
    assert (root / 'current.json').read_bytes() == pointer


def test_real_adapter_unsupported_is_pending(env):
    effects, journal, op, root = env
    result = effects.repair_publication(op, journal=journal)
    assert result['artifacts'] == result['log'] == 'done'
    assert result['search'] == 'failed'
    from agent_console.qmd_generation import generation_health
    health = generation_health(root=root)
    assert health['published']['generation_id'] == result['generation_id']
    assert health['status'] == 'search_pending'


def test_retained_generation_repair_never_rolls_pointer_back(env, monkeypatch):
    effects, journal, op, root = env
    first = effects.repair_publication(op, journal=journal)
    wiki.upsert_page({'id': 'one', 'title': 'One', 'body': 'Newer canonical knowledge.',
                      'kind': 'concept', 'surface': 'market'})
    snapshot = publication.capture_snapshot()
    snapshot['captured_at'] = '2026-10-17T00:00:00+00:00'
    snapshot['snapshot_sha256'] = publication._snapshot_hash(snapshot)
    publication.publish_snapshot(snapshot, root=root, operation_id='newer')
    pointer = (root / 'current.json').read_bytes()
    monkeypatch.setattr(effects.qmd_generation, 'build_index', lambda *a, **k: pytest.fail('old search activation'))
    assert effects.repair_publication(op, journal=journal)['generation_id'] == first['generation_id']
    assert (root / 'current.json').read_bytes() == pointer


def test_old_generation_history_failure_repairs_after_new_publication(env, monkeypatch):
    effects, journal, op, root = env
    append = wiki._append_wiki_log
    monkeypatch.setattr(wiki, '_append_wiki_log', lambda *a, **k: (_ for _ in ()).throw(OSError('history')))
    first = effects.repair_publication(op, journal=journal)
    assert first['log'] == 'failed'
    monkeypatch.setattr(wiki, '_append_wiki_log', append)
    wiki.upsert_page({'id': 'one', 'title': 'One', 'body': 'Advanced after old commit.',
                      'kind': 'concept', 'surface': 'market'})
    publication.publish_snapshot(publication.capture_snapshot(), root=root, operation_id='newer')
    pointer = (root / 'current.json').read_bytes()
    monkeypatch.setattr(wiki, '_append_wiki_log', append)
    result = effects.repair_publication(op, journal=journal)
    assert result['generation_id'] == first['generation_id']
    assert result['artifacts'] == result['log'] == 'done'
    assert (root / 'current.json').read_bytes() == pointer


def test_legacy_done_receipt_has_no_invented_generation(env):
    effects, journal, op, root = env
    journal.transition(op, effects={k: 'done' for k in ('artifacts', 'search', 'log')})
    before = journal._path('operations', op).read_bytes()
    result = effects.repair_publication(op, journal=journal)
    assert result['generation_id'] is None and result['artifacts'] == 'failed'
    assert journal._path('operations', op).read_bytes() == before
    assert not root.exists()


@pytest.mark.parametrize('damage', ['snapshot', 'publication', 'binding', 'source', 'symlink'])
def test_forged_sidecar_fails_closed(env, damage):
    effects, journal, op, root = env
    effects.repair_publication(op, journal=journal)
    path = journal._path('publication-effects', op)
    sidecar = json.loads(path.read_text())
    if damage == 'snapshot':
        sidecar['snapshot']['records'][0]['body'] = 'forged'
    elif damage == 'publication':
        sidecar['publication']['manifest_sha256'] = '0' * 64
    elif damage == 'binding':
        sidecar['receipt_sha256'] = '0' * 64
    elif damage == 'source':
        source = journal._path('sources', 'a' * 64)
        value = json.loads(source.read_text())
        value['source']['title'] = 'forged'
        source.write_text(json.dumps(value))
    else:
        other = path.with_suffix('.other')
        path.rename(other)
        path.symlink_to(other)
    if damage not in {'source', 'symlink'}:
        path.write_text(json.dumps(sidecar))
    before = (root / 'current.json').read_bytes()
    with pytest.raises(ValueError):
        effects.repair_publication(op, journal=journal)
    assert (root / 'current.json').read_bytes() == before


def test_receipt_schema_and_commit_targets_remain_unchanged(env):
    effects, journal, op, _ = env
    before = copy.deepcopy(journal._read(journal._path('operations', op)))
    effects.repair_publication(op, journal=journal)
    after = journal._read(journal._path('operations', op))
    assert set(after) == set(before)
    assert after['schema_version'] == 1 and after['batch'] == before['batch']


def test_default_flag_dispatch_retains_legacy(env, monkeypatch):
    effects, journal, op, _ = env
    from reports import wiki_source_pipeline
    monkeypatch.delenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', raising=False)
    monkeypatch.setattr(effects, 'repair_publication', lambda *a, **k: pytest.fail('generation enabled'))
    monkeypatch.setattr(wiki, 'rebuild_artifacts', lambda **k: {'ok': True})
    monkeypatch.setattr(wiki, 'sync_qmd', lambda: {'ok': True})
    receipt = wiki_source_pipeline._repair_effects(journal._read(journal._path('operations', op)), journal)
    assert all(receipt[k] == 'done' for k in ('artifacts', 'search', 'log'))


@pytest.mark.parametrize('sentinel_collision', [False, True])
def test_actual_adapter_ready_and_search_replay(env, monkeypatch, sentinel_collision):
    from test_qmd_generation_search import Runner, capability
    class LexicalRunner(Runner):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if argv[3] == 'search':
                # QMD lexical queries match tokens, not substrings in the sentinel.
                tokens = set(re.findall(r'\w+', argv[4].casefold()))
                result.stdout = json.dumps([
                    row for row in json.loads(result.stdout)
                    if tokens <= set(re.findall(r'\w+', row['content'].casefold()))
                ])
            return result

    effects, journal, op, root = env
    snapshot = publication.capture_snapshot()
    # Force both sides of "qmdgeneration" + "e..." containing the page ID "one".
    for attempt in range(4096):
        snapshot['captured_at'] = (datetime(2026, 10, 16, tzinfo=timezone.utc)
                                   + timedelta(seconds=attempt)).isoformat()
        snapshot['snapshot_sha256'] = publication._snapshot_hash(snapshot)
        identity = effects._identity_for(snapshot)
        sentinel = 'qmdgeneration' + identity['generation_id']
        if ('one' in sentinel) == sentinel_collision:
            break
    else:
        pytest.fail('could not construct the requested sentinel collision')
    monkeypatch.setattr(publication, 'capture_snapshot', lambda: copy.deepcopy(snapshot))
    pending = effects.repair_publication(op, journal=journal)
    assert pending['search'] == 'failed'
    runner = LexicalRunner()
    monkeypatch.setattr(wiki.qmd_search, 'qmd_bin', lambda: '/bin/true')
    monkeypatch.setattr(effects, 'search_dependencies', lambda: (capability(), runner))
    first = effects.repair_publication(op, journal=journal)
    assert first['search'] == 'done'
    assert first['generation_id'] == pending['generation_id']
    sidecar = json.loads(journal._path('publication-effects', op).read_text())
    assert sidecar['descriptor_sha256'] == effects._digest(sidecar['descriptor'])
    hits = effects.qmd_generation.search_generation(
        'one', descriptor=sidecar['descriptor'], limit=5, runner=runner)
    assert [hit['page_id'] for hit in hits] == ['one']
    descriptor = (root / 'search-current.json').read_bytes()
    monkeypatch.setattr(effects.qmd_generation, 'build_index', lambda *a, **k: pytest.fail('rebuild on replay'))
    assert effects.repair_publication(op, journal=journal) == first
    assert (root / 'search-current.json').read_bytes() == descriptor


def test_sidecar_intent_exists_before_activation_and_store_lock_released(env, monkeypatch):
    from contextlib import contextmanager
    effects, journal, op, root = env
    held = []
    original_store = shared_memory._events_lock
    original_publish = publication.publish_snapshot
    @contextmanager
    def store():
        with original_store():
            held.append(True)
            try:
                yield
            finally:
                held.pop()
    def publish(snapshot, **kwargs):
        assert not held
        intent = json.loads(journal._path('publication-effects', op).read_text())
        assert intent['snapshot'] == snapshot and not intent['activated']
        return original_publish(snapshot, **kwargs)
    monkeypatch.setattr(shared_memory, '_events_lock', store)
    monkeypatch.setattr(publication, 'publish_snapshot', publish)
    assert effects.repair_publication(op, journal=journal)['artifacts'] == 'done'


def test_slower_operation_cannot_activate_over_new_pointer(env, monkeypatch):
    effects, journal, op, root = env
    publish = publication.publish_snapshot
    newer = None
    def race(snapshot, **kwargs):
        nonlocal newer
        result = publish(snapshot, **kwargs)
        other = copy.deepcopy(snapshot)
        other['captured_at'] = '2026-10-18T00:00:00+00:00'
        other['snapshot_sha256'] = publication._snapshot_hash(other)
        newer = publish(other, root=root, operation_id='newer')
        return result
    monkeypatch.setattr(publication, 'publish_snapshot', race)
    result = effects.repair_publication(op, journal=journal)
    assert result['artifacts'] == 'failed'
    assert publication.pin_generation(root=root).generation_id == newer['generation_id']


def test_v2_witness_and_schema_survive_generation_dispatch(transaction, tmp_path, monkeypatch):
    from reports import wiki_publication_effects as effects
    from reports import wiki_topic_transaction as topic
    _, journal, _ = transaction
    batch = prepare(transaction)
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', '1')
    monkeypatch.setattr(effects, 'publication_root', lambda: tmp_path / 'publication')
    result = recover(batch, journal)
    assert result['stage'] == 'committed'
    assert result['effects']['artifacts'] == 'done'
    receipt = journal.load_topic_operation(batch['operation_id'])
    assert receipt['schema_version'] == 2 and 'revision_id' not in receipt
    assert receipt['batch'] == batch
    witness = topic._commit_record(batch)
    rows = shared_memory.inspect_records()
    assert [r for r in rows if r['id'] == witness['id']] == [witness]
    originals = {str(p): p.read_bytes() for p in journal.root.parent.rglob('*')
                 if p.is_file() and p.suffix not in {'.lock'} and '/memory/' not in str(p)}
    assert not recover(batch, journal)['newly_committed']
    for name, content in originals.items():
        if '/publication-effects/' not in name and '/topic-operations/' not in name:
            from pathlib import Path
            assert Path(name).read_bytes() == content
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked([r for r in rows if r['id'] != witness['id']])
    with pytest.raises(ValueError, match='topic_commit_witness_mismatch'):
        effects.repair_publication(batch['operation_id'], journal=journal)


def test_forged_descriptor_and_activation_status_rejected(env):
    effects, journal, op, root = env
    effects.repair_publication(op, journal=journal)
    path = journal._path('publication-effects', op)
    sidecar = json.loads(path.read_text())
    sidecar['descriptor'] = {'generation_id': '0' * 64}
    sidecar['descriptor_sha256'] = effects._digest(sidecar['descriptor'])
    path.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match='publication_descriptor_mismatch'):
        effects.repair_publication(op, journal=journal)


def test_crash_after_pointer_repairs_without_reactivation(env, monkeypatch):
    effects, journal, op, root = env
    activate = publication._activate_locked
    def crash(*a, **k):
        result = activate(*a, **k)
        if a[1] == root:
            raise OSError('after activation')
        return result
    monkeypatch.setattr(publication, '_activate_locked', crash)
    first = effects.repair_publication(op, journal=journal)
    assert first['artifacts'] == 'failed'
    pointer = (root / 'current.json').read_bytes()
    monkeypatch.setattr(publication, '_activate_locked', lambda *a, **k: pytest.fail('reactivation'))
    assert effects.repair_publication(op, journal=journal)['artifacts'] == 'done'
    assert (root / 'current.json').read_bytes() == pointer


def test_repair_never_compiles_or_mutates_canonical_store(env, monkeypatch):
    from reports import wiki_source_compiler
    effects, journal, op, _ = env
    def forbidden(*a, **k):
        pytest.fail('model path invoked')
    monkeypatch.setattr(wiki_source_compiler, 'compile_source', forbidden)
    path = shared_memory._paths()['events']
    before = path.read_bytes()
    effects.repair_publication(op, journal=journal)
    assert path.read_bytes() == before


def test_crash_before_main_pointer_resumes_only_unchanged_predecessor(env, monkeypatch):
    effects, journal, op, root = env
    activate = publication._activate_locked
    def crash(*a, **k):
        if a[1] == root:
            raise OSError('before activation')
        return activate(*a, **k)
    monkeypatch.setattr(publication, '_activate_locked', crash)
    assert effects.repair_publication(op, journal=journal)['artifacts'] == 'failed'
    assert not (root / 'current.json').exists()
    monkeypatch.setattr(publication, '_activate_locked', activate)
    assert effects.repair_publication(op, journal=journal)['artifacts'] == 'done'


def test_enabled_source_dispatch_uses_verified_sidecar(env, monkeypatch):
    effects, journal, op, _ = env
    from reports import wiki_source_pipeline
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', '1')
    monkeypatch.setattr(wiki, 'rebuild_artifacts', lambda **k: pytest.fail('legacy rebuild'))
    monkeypatch.setattr(wiki, 'sync_qmd', lambda: pytest.fail('legacy search'))
    result = wiki_source_pipeline._repair_effects(journal._read(journal._path('operations', op)), journal)
    assert result['artifacts'] == result['log'] == 'done' and result['search'] == 'failed'
    assert journal._path('publication-effects', op).exists()


def test_forged_activated_bit_is_not_activation_proof(env, monkeypatch):
    effects, journal, op, root = env
    activate = publication._activate_locked
    def crash(*a, **k):
        if a[1] == root:
            raise OSError('before main pointer')
        return activate(*a, **k)
    monkeypatch.setattr(publication, '_activate_locked', crash)
    assert effects.repair_publication(op, journal=journal)['artifacts'] == 'failed'
    path = journal._path('publication-effects', op)
    intent = json.loads(path.read_text())
    intent['activated'] = True
    path.write_text(json.dumps(intent))
    monkeypatch.setattr(publication, '_activate_locked', activate)
    result = effects.repair_publication(op, journal=journal)
    assert result['artifacts'] == 'failed'
    assert not (root / 'current.json').exists()


@pytest.mark.parametrize('kind', ['source', 'topic'])
def test_disabled_dispatch_does_not_import_generation_dependencies(env, monkeypatch, kind):
    import builtins
    from reports import wiki_source_pipeline, wiki_topic_transaction
    _, journal, op, _ = env
    receipt = journal.transition(op, effects={k: 'done' for k in ('artifacts', 'search', 'log')})
    monkeypatch.delenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', raising=False)
    original = builtins.__import__
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'reports' and 'wiki_publication_effects' in fromlist:
            pytest.fail('disabled path imported generation dependencies')
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', guarded)
    dispatch = wiki_source_pipeline._repair_effects if kind == 'source' else wiki_topic_transaction._effects
    assert dispatch(receipt, journal) == receipt


def test_ready_foreign_descriptor_cannot_complete_search(env, monkeypatch):
    effects, journal, op, root = env
    def build(identity, *, root, capability, runner):
        descriptor = {**identity, 'root': str(root / 'foreign')}
        (root / 'search-current.json').write_text(json.dumps(descriptor))
        return {**identity, 'status': 'ready', 'descriptor': descriptor}
    def health(*, root: object):
        assert root == effects.publication_root()
        reader = publication.pin_generation(root=root)
        return {'status': 'ready', 'indexed': {key: getattr(reader, key) for key in (
            'generation_id', 'snapshot_sha256', 'manifest_sha256')}}
    monkeypatch.setattr(effects.qmd_generation, 'build_index', build)
    monkeypatch.setattr(effects.qmd_generation, 'generation_health', health)
    result = effects.repair_publication(op, journal=journal)
    assert result['artifacts'] == 'done' and result['search'] == 'failed'


def test_activation_between_snapshot_and_predecessor_read_is_rejected(env, monkeypatch):
    effects, journal, op, root = env
    capture = publication.capture_snapshot
    newer = {}
    def interleave():
        frozen = capture()
        advanced = copy.deepcopy(frozen)
        advanced['captured_at'] = '2026-10-19T00:00:00+00:00'
        advanced['records'][0]['body'] = 'Newer published knowledge.'
        advanced['snapshot_sha256'] = publication._snapshot_hash(advanced)
        newer.update(publication.publish_snapshot(advanced, root=root, operation_id='newer'))
        return frozen
    monkeypatch.setattr(publication, 'capture_snapshot', interleave)
    result = effects.repair_publication(op, journal=journal)
    assert result['artifacts'] == 'failed'
    assert publication.pin_generation(root=root).generation_id == newer['generation_id']


def test_durable_activation_proof_recovers_after_sidecar_save_failure_and_new_pointer(env, monkeypatch):
    effects, journal, op, root = env
    save = effects._save
    path = journal._path('publication-effects', op)
    def fail_activated_save(target, value):
        if target == path and value.get('activated') is True:
            raise OSError('activated sidecar save failed')
        return save(target, value)
    monkeypatch.setattr(effects, '_save', fail_activated_save)
    first = effects.repair_publication(op, journal=journal)
    assert first['artifacts'] == 'failed'
    assert json.loads(path.read_bytes())['activated'] is False
    assert (root / 'publication-activations' / (op + '.json')).is_file()
    advanced = publication.capture_snapshot()
    advanced['captured_at'] = '2026-10-20T00:00:00+00:00'
    advanced['snapshot_sha256'] = publication._snapshot_hash(advanced)
    publication.publish_snapshot(advanced, root=root, operation_id='newer')
    pointer = (root / 'current.json').read_bytes()
    monkeypatch.setattr(effects, '_save', save)
    monkeypatch.setattr(publication, '_activate_locked', lambda *a, **k: pytest.fail('historical reactivation'))
    result = effects.repair_publication(op, journal=journal)
    assert result['generation_id'] == first['generation_id']
    assert result['artifacts'] == result['log'] == 'done'
    assert (root / 'current.json').read_bytes() == pointer
    assert effects.repair_publication(op, journal=journal) == result
