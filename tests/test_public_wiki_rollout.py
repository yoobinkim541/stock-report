from scripts.run_public_wiki_pilot import is_public
import pytest


def test_missing_model_executable_does_not_consume_quota(monkeypatch):
    runner = module()
    monkeypatch.setattr('sys.argv', ['rollout', '--apply'])
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '0')
    monkeypatch.setattr('shutil.which', lambda command: None)
    monkeypatch.setattr(runner.source_collector, 'load_recent_events',
                        lambda **kw: pytest.fail('read sources before model preflight'))
    with pytest.raises(ValueError, match='codex_executable_unavailable'):
        runner.main()


def test_apply_rejects_unrestricted_flag_before_reading_sources(monkeypatch):
    runner = module()
    monkeypatch.setattr('sys.argv', ['rollout', '--apply'])
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '1')
    monkeypatch.setattr(runner.source_collector, 'load_recent_events',
                        lambda **kw: pytest.fail('read sources before policy check'))
    with pytest.raises(ValueError, match='unrestricted_pipeline_enabled'):
        runner.main()


def test_supplied_private_snapshot_rejected_even_when_live_page_is_public(monkeypatch):
    from scripts import run_public_wiki_pilot as pilot
    monkeypatch.setattr(pilot.shared_memory, 'inspect_records', lambda: [])
    monkeypatch.setattr(pilot, 'public_target_ids', lambda *args, **kwargs: {'page'})
    monkeypatch.setattr(pilot.wiki, 'semantic_snapshot',
                        lambda identity, **kwargs: {'revision_hash': 'public-hash',
                                                   'page': {'title': 'Public', 'summary': ''}})
    with pytest.raises(ValueError, match='public_target_changed'):
        pilot.validate_public_targets([{'page_id': 'page', 'revision_hash': 'private-hash',
                                        'summary': 'synthetic private content'}], object())


@pytest.mark.parametrize('summary,accepted', [('Public', True), ('Private', False)])
def test_target_projection_must_match_supplied_hash_and_content(monkeypatch, summary, accepted):
    from scripts import run_public_wiki_pilot as pilot
    monkeypatch.setattr(pilot.shared_memory, 'inspect_records', lambda: [])
    monkeypatch.setattr(pilot, 'public_target_ids', lambda *args, **kwargs: {'page'})
    monkeypatch.setattr(pilot.wiki, 'semantic_snapshot', lambda *args, **kwargs:
                        {'page_id': 'page', 'revision_hash': 'hash',
                         'page': {'title': 'Title', 'summary': 'Public'}})
    targets = [{'page_id': 'page', 'revision_hash': 'hash', 'title': 'Title', 'summary': summary}]
    if accepted:
        pilot.validate_public_targets(targets, object())
    else:
        with pytest.raises(ValueError, match='public_target_changed'):
            pilot.validate_public_targets(targets, object())


def module():
    from scripts import run_public_wiki_rollout
    return run_public_wiki_rollout


@pytest.mark.parametrize('enabled', [False, True])
def test_topic_stage_runs_after_legacy_stage_only_when_enabled(tmp_path, monkeypatch, enabled):
    from reports.wiki_source_journal import Journal
    runner = module()
    source = {'revision_id': 'a' * 64, 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    monkeypatch.setenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '1' if enabled else '0')
    order = []
    monkeypatch.setattr(runner.pipeline, 'run', lambda **kw: order.append('legacy') or {'data_committed': 1})
    def topic(supplied, **kwargs):
        assert supplied is source
        assert order == ['legacy']
        assert kwargs['reserved_revision_id'] == source['revision_id']
        order.append('topic')
        return {'enabled': True, 'stage': 'committed'}
    monkeypatch.setattr(runner.pipeline, 'run_public_topic', topic)
    result = runner.process(source, [], Journal(tmp_path), {'id': 'attempt', 'revision_id': source['revision_id']})
    assert order == (['legacy', 'topic'] if enabled else ['legacy'])
    assert ('topic' in result) == enabled


def test_topic_failure_is_reported_and_environment_restored(tmp_path, monkeypatch):
    from reports.wiki_source_journal import Journal
    runner = module()
    source = {'revision_id': 'a' * 64, 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    monkeypatch.setenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '1')
    monkeypatch.setenv('AGENT_CONSOLE_QMD_ENABLED', 'sentinel')
    original = runner.pipeline.discover_targets
    monkeypatch.setattr(runner.pipeline, 'run', lambda **kw: {'data_committed': 1})
    def fail(*args, **kwargs):
        raise ValueError('private diagnostic must not persist')
    monkeypatch.setattr(runner.pipeline, 'run_public_topic', fail)
    result = runner.process(source, [], Journal(tmp_path), {'id': 'attempt', 'revision_id': source['revision_id']})
    assert result['topic_failed'] == 1
    assert 'private diagnostic' not in str(result)
    assert runner.pipeline.discover_targets is original
    import os
    assert os.environ['AGENT_CONSOLE_QMD_ENABLED'] == 'sentinel'


@pytest.mark.parametrize('reservation', [{}, {'revision_id': 'b' * 64}])
def test_process_requires_matching_reserved_revision_before_pipeline(tmp_path, monkeypatch, reservation):
    from reports.wiki_source_journal import Journal
    runner = module()
    source = {'revision_id': 'a' * 64, 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    monkeypatch.setattr(runner.pipeline, 'run', lambda **kw: pytest.fail('unreserved source entered worker'))
    with pytest.raises(ValueError, match='unreserved_source'):
        runner.process(source, [], Journal(tmp_path), {'id': 'attempt', **reservation})


def test_topic_failure_remains_selectable_after_legacy_completion(tmp_path, monkeypatch):
    from reports.wiki_source_journal import Journal
    runner = module()
    journal = Journal(tmp_path)
    source = {'revision_id': 'a' * 64, 'role': 'support', 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    monkeypatch.setenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '1')
    monkeypatch.setattr(runner.pipeline, 'run', lambda **kw: {'data_committed': 1})
    monkeypatch.setattr(runner.pipeline, '_pending_source', lambda *a: False)
    def fail(*a, **kw):
        raise ValueError('synthetic provider failure')
    monkeypatch.setattr(runner.pipeline, 'run_public_topic', fail)
    assert runner.process(source, [], journal, {'id': 'one', 'revision_id': source['revision_id']})['topic_failed'] == 1
    selected = runner.select_pending([source], lambda item: runner.pending_public_source(item, journal))
    assert selected == [source]
    monkeypatch.setattr(runner.pipeline, 'run_public_topic', lambda *a, **kw:
                        {'enabled': True, 'stage': 'committed', 'effects': {'log': 'done'}})
    runner.process(selected[0], [], journal, {'id': 'two', 'revision_id': source['revision_id']})
    assert not runner.pending_public_source(source, journal)
    # Old completed originals without explicit topic-stage enrollment stay done.
    assert not runner.pending_public_source({**source, 'revision_id': 'b' * 64}, journal)


def test_crash_after_legacy_commit_keeps_topic_enrollment(tmp_path, monkeypatch):
    from reports.wiki_source_journal import Journal
    runner = module()
    journal = Journal(tmp_path)
    source = {'revision_id': 'a' * 64, 'role': 'support', 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    monkeypatch.setenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '1')
    monkeypatch.setattr(runner.pipeline, '_pending_source', lambda *a: False)
    def crash(**kwargs):
        raise RuntimeError('synthetic worker crash after commit')
    monkeypatch.setattr(runner.pipeline, 'run', crash)
    with pytest.raises(RuntimeError):
        runner.process(source, [], journal, {'id': 'one', 'revision_id': source['revision_id']})
    assert runner.pending_public_source(source, journal)


@pytest.mark.parametrize('apply', [False, True])
def test_dry_and_apply_keep_pending_role_when_recent_role_completed(tmp_path, monkeypatch, capsys, apply):
    import json
    runner = module()
    support = {'revision_id': 'a' * 64, 'role': 'support', 'provider': 'telegram:public_channel',
               'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    conflict = {**support, 'role': 'conflict'}
    monkeypatch.setattr('sys.argv', ['rollout', *(['--apply'] if apply else [])])
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '0')
    monkeypatch.setattr(runner.shutil, 'which', lambda _: '/synthetic/codex')
    monkeypatch.setattr(runner.raw_archive, 'reports_root', lambda: tmp_path)
    monkeypatch.setattr(runner.source_collector, 'load_recent_events', lambda **kw: [])
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions', lambda *a, **kw: {'eligible': [support]})
    monkeypatch.setattr(runner, 'resume_topic_sources', lambda *a: ([conflict], []))
    monkeypatch.setattr(runner.pipeline, '_resume_compilations', lambda *a: ([], []))
    monkeypatch.setattr(runner, 'public_revisions', lambda *a: {support['revision_id']})
    monkeypatch.setattr(runner, 'pending_public_source', lambda source, journal: source['role'] == 'conflict')
    selected = []
    monkeypatch.setattr(runner, 'process', lambda source, *a: selected.append(source) or {})
    assert runner.main() == 0
    if apply:
        assert selected == [conflict]
    else:
        assert json.loads(capsys.readouterr().out)['pending_public_sources'] == 1


def test_process_rejects_private_source_before_pipeline(tmp_path, monkeypatch):
    from reports.wiki_source_journal import Journal
    runner = module()
    monkeypatch.setattr(runner.pipeline, 'run', lambda **kw: pytest.fail('private source entered pipeline'))
    with pytest.raises(ValueError, match='private_source_disallowed'):
        runner.process({'revision_id': 'a' * 64, 'provider': 'saveticker',
                        'scope': 'authorized_article', 'canonical_url': 'https://saveticker.com/news/1'},
                       [], Journal(tmp_path), {'id': 'synthetic-attempt'})


def test_selection_caps_five_and_prefers_oldest_pending_public_revisions():
    sources = [{'revision_id': format(i, '064x'), 'provider': 'telegram:public_channel',
                'scope': 'public_message', 'canonical_url': f'https://t.me/public_channel/{i}',
                'published_at': f'2026-10-{i:02d}', 'collected_at': ''} for i in range(1, 8)]
    private = {**sources[0], 'revision_id': 'f' * 64, 'provider': 'saveticker'}
    selected = module().select_pending([*reversed(sources), private], lambda source: source['revision_id'] != sources[0]['revision_id'])
    assert len(selected) == 5
    assert [source['revision_id'] for source in selected] == [source['revision_id'] for source in sources[1:6]]
    assert all(is_public(source) for source in selected)


def test_dry_plan_never_reserves_daily_quota(tmp_path):
    result = module().plan_status(tmp_path / 'budget.json', candidates=7)
    assert result['apply'] is False and result['planned_attempts'] == 5
    assert not (tmp_path / 'budget.json').exists()


def test_failed_source_batch_reports_nonzero_service_exit(tmp_path, monkeypatch):
    runner = module()
    monkeypatch.setattr('sys.argv', ['rollout', '--apply'])
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '0')
    monkeypatch.setattr(runner.shutil, 'which', lambda command: '/synthetic/codex')
    monkeypatch.setattr(runner.raw_archive, 'reports_root', lambda: tmp_path)
    monkeypatch.setattr(runner.source_collector, 'load_recent_events', lambda **kw: [])
    source = {'revision_id': 'a' * 64, 'role': 'evidence', 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions',
                        lambda *args, **kw: {'eligible': [source]})
    monkeypatch.setattr(runner.pipeline, '_resume_compilations', lambda *args: ([], []))
    monkeypatch.setattr(runner.pipeline, '_pending_source', lambda *args: True)
    monkeypatch.setattr(runner, 'process', lambda *args: {'compile_failed': 1})
    assert runner.main() == 2
