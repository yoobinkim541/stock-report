import json

import pytest

from agent_console import qmd_search, shared_memory, wiki, wiki_publication as p
from reports import wiki_publication_effects as effects, wiki_source_pipeline
from reports.wiki_source_journal import Journal
from test_qmd_generation_search import Runner, capability
from test_wiki_publication_activation import snapshot


@pytest.fixture
def committed(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_CONSOLE_SHARED_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_ARTIFACTS_DIR', str(tmp_path / 'wiki'))
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', '1')
    monkeypatch.setattr(wiki, '_debounced_rebuild', lambda: None)
    monkeypatch.setattr(shared_memory, '_schedule_context_memory_summary', lambda: None)
    wiki._CACHE.clear()
    wiki.upsert_page({'id': 'one', 'title': 'One', 'body': 'Preserved knowledge.',
                      'kind': 'concept', 'surface': 'market'})
    journal = Journal(tmp_path / 'journal')
    revision, op = 'a' * 64, 'b' * 64
    journal.save_compilation({'revision_id': revision, 'role': 'support'},
                             {'revision_id': revision, 'body': 'Compiled'})
    rows = shared_memory.inspect_records()
    journal.prepare({'operation_id': op, 'revision_id': revision, 'target_ids': ['one'],
                     'expected_after': {'one': wiki._source_fingerprint('one', rows)},
                     'expected_before': {'one': None}, 'upserts': []})
    return journal, journal.transition(op, stage='committed')


def test_real_dispatch_public_health_and_search_share_root(committed, monkeypatch):
    journal, receipt = committed
    runner = Runner()
    monkeypatch.setenv('AGENT_CONSOLE_QMD_ENABLED', '1')
    monkeypatch.setattr(qmd_search, 'qmd_bin', lambda: '/bin/true')
    monkeypatch.setattr(effects, 'search_dependencies', lambda: (capability(), runner))
    result = wiki_source_pipeline._repair_effects(receipt, journal)
    assert result['search'] == 'done'
    assert qmd_search.health()['status'] == 'ready'
    assert [hit['page_id'] for hit in qmd_search.search('Preserved', runner=runner)] == ['one']


def test_rollback_history_crash_repair_after_later_activation(tmp_path, monkeypatch):
    first = p.publish_snapshot(snapshot(), root=tmp_path, operation_id='first')
    second = p.publish_snapshot(snapshot('second'), root=tmp_path, operation_id='second')
    original = p._fsync_directory
    def crash(path):
        if path.name == 'activation-history':
            raise OSError('history crash')
        return original(path)
    monkeypatch.setattr(p, '_fsync_directory', crash)
    with pytest.raises(OSError, match='history crash'):
        p.activate_generation(first['generation_id'], root=tmp_path, operation_id='rollback')
    monkeypatch.setattr(p, '_fsync_directory', original)
    p.activate_generation(second['generation_id'], root=tmp_path, operation_id='later')
    pointer = (tmp_path / 'current.json').read_bytes()
    p.activate_generation(first['generation_id'], root=tmp_path, operation_id='rollback')
    assert (tmp_path / 'current.json').read_bytes() == pointer
    events = [json.loads(path.read_bytes()) for path in (tmp_path / 'activation-history').glob('*.json')]
    assert len([event for event in events if event['operation_id'] == 'rollback']) == 1
    assert any(event['event'] == 'rollback' for event in events)
    assert list((tmp_path / 'activation-intents').glob('*.json'))


@pytest.mark.parametrize('later_activation', [None, 'second', 'same'])
def test_rollback_pointer_crash_repairs_only_current_intent_alias(tmp_path, monkeypatch, later_activation):
    first = p.publish_snapshot(snapshot(), root=tmp_path, operation_id='first')
    second = p.publish_snapshot(snapshot('second'), root=tmp_path, operation_id='second')
    original = p.safe_io.atomic_write_json
    def crash(path, value, **kwargs):
        result = original(path, value, **kwargs)
        if str(path).endswith('/current.json'):
            raise OSError('after pointer before alias')
        return result
    monkeypatch.setattr(p.safe_io, 'atomic_write_json', crash)
    with pytest.raises(OSError, match='after pointer before alias'):
        p.activate_generation(first['generation_id'], root=tmp_path, operation_id='rollback')
    assert json.loads((tmp_path / 'current.json').read_bytes())['generation_id'] == first['generation_id']
    assert (tmp_path / 'current').resolve() == tmp_path / 'generations' / second['generation_id']
    monkeypatch.setattr(p.safe_io, 'atomic_write_json', original)
    if later_activation:
        p.publish_snapshot(snapshot('second') if later_activation == 'second' else snapshot(),
                           root=tmp_path, operation_id='later')
        monkeypatch.setattr(p.os, 'replace', lambda *a, **k: pytest.fail('historical alias replacement'))
    pointer = (tmp_path / 'current.json').read_bytes()
    p.activate_generation(first['generation_id'], root=tmp_path, operation_id='rollback')
    assert (tmp_path / 'current.json').read_bytes() == pointer
    expected = second if later_activation == 'second' else first
    assert (tmp_path / 'current').resolve() == tmp_path / 'generations' / expected['generation_id']


@pytest.mark.parametrize('value', [None, '', 'true', 'yes', 'on', '0', 'false', ' 1 ', '1'])
def test_flag_readers_match_exact_opt_in(value, monkeypatch):
    if value is None:
        monkeypatch.delenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', raising=False)
    else:
        monkeypatch.setenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', value)
    assert qmd_search.generation_enabled() is (value == '1')


@pytest.mark.parametrize('boundary', ['intent', 'before_pointer', 'after_pointer', 'history'])
def test_rollback_crash_boundaries_never_reactivate_old_pointer(tmp_path, monkeypatch, boundary):
    first = p.publish_snapshot(snapshot(), root=tmp_path, operation_id='first')
    second = p.publish_snapshot(snapshot('second'), root=tmp_path, operation_id='second')
    original = p.safe_io.atomic_write_json
    def crash(path, value, **kwargs):
        from pathlib import Path
        target = Path(path)
        if ((boundary == 'intent' and target.parent.name == 'activation-intents')
                or (boundary == 'before_pointer' and target.name == 'current.json')
                or (boundary == 'history' and target.parent.name == 'activation-history')):
            raise OSError('crash')
        result = original(path, value, **kwargs)
        if boundary == 'after_pointer' and target.name == 'current.json':
            raise OSError('crash')
        return result
    monkeypatch.setattr(p.safe_io, 'atomic_write_json', crash)
    with pytest.raises(OSError, match='crash'):
        p.activate_generation(first['generation_id'], root=tmp_path, operation_id='rollback')
    monkeypatch.setattr(p.safe_io, 'atomic_write_json', original)
    # Ordinary publication also repairs history before replacing the pointer.
    p.publish_snapshot(snapshot('third'), root=tmp_path, operation_id='third')
    pointer = (tmp_path / 'current.json').read_bytes()
    if boundary != 'intent':
        p.activate_generation(first['generation_id'], root=tmp_path, operation_id='rollback')
        assert (tmp_path / 'current.json').read_bytes() == pointer
    events = list((tmp_path / 'activation-history').glob('*.json'))
    assert len(events) == (1 if boundary in {'after_pointer', 'history'} else 0)
    assert (tmp_path / 'generations' / second['generation_id']).is_dir()


@pytest.mark.parametrize('kind', ['source', 'topic'])
@pytest.mark.parametrize('value', ['', 'true', 'yes', 'on', '0', 'false', ' 1 '])
def test_disabled_dispatch_imports_only_lightweight_config(committed, monkeypatch, kind, value):
    import builtins
    from reports import wiki_topic_transaction
    journal, receipt = committed
    receipt = journal.transition(receipt['operation_id'], effects={
        key: 'done' for key in ('artifacts', 'search', 'log')})
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', value)
    original = builtins.__import__
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if 'wiki_publication_effects' in (fromlist or ()) or name.endswith('wiki_publication_effects'):
            pytest.fail('disabled dispatcher imported generation effects')
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', guarded)
    dispatch = wiki_source_pipeline._repair_effects if kind == 'source' else wiki_topic_transaction._effects
    assert dispatch(receipt, journal) == receipt
