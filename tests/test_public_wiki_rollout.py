from scripts.run_public_wiki_pilot import is_public
import pytest
import json
from datetime import datetime, timedelta, timezone


NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def cached_event(identity, **changes):
    return {'id': identity, 'source': 'arca', 'collected_at': NOW.isoformat(),
            'url': f'https://arca.live/b/stock/{identity}',
            'raw_path': '/raw/source', 'text_path': '/text/source',
            'manifest_path': '/raw/source.manifest.json', **changes}


def write_cache(runner, cache, rows, now=NOW):
    cache.mkdir(parents=True, exist_ok=True)
    path = runner.source_collector._event_file(cache, now)
    with path.open('w') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')
    return path


def test_missing_provider_does_not_consume_quota(monkeypatch):
    runner = module()
    monkeypatch.setattr('sys.argv', ['rollout', '--apply'])
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '1')
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setattr('shutil.which', lambda command: None)
    monkeypatch.setattr(runner, 'load_source_events',
                        lambda **kw: pytest.fail('read sources before model preflight'))
    with pytest.raises(ValueError, match='structured_provider_unavailable'):
        runner.main()


def test_enabled_pipeline_accepts_verified_public_body_and_authorized_private(monkeypatch):
    from scripts.run_public_wiki_pilot import source_allowed
    private = {'provider': 'saveticker', 'scope': 'authorized_article',
               'canonical_url': 'https://saveticker.com/news/1'}
    public = {'provider': 'arca', 'scope': 'article_fulltext',
              'canonical_url': 'https://arca.live/b/stock/123'}
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '1')
    monkeypatch.delenv('WIKI_SOURCE_ALLOW_PRIVATE', raising=False)
    assert source_allowed(public)
    assert not source_allowed(private)
    monkeypatch.setenv('WIKI_SOURCE_ALLOW_PRIVATE', '1')
    assert source_allowed(private)
    assert not source_allowed({**private, 'scope': 'preview'})


@pytest.mark.parametrize('private', [False, True])
def test_discovery_loads_private_cache_only_with_explicit_opt_in(tmp_path, monkeypatch, private):
    runner = module()
    monkeypatch.setenv('WIKI_SOURCE_ALLOW_PRIVATE', '1' if private else '0')
    monkeypatch.setattr(runner.raw_archive, 'reports_root', lambda: tmp_path)
    public_cache = tmp_path / 'cache'
    monkeypatch.setattr(runner.source_collector, 'DEFAULT_CACHE_DIR', public_cache)
    write_cache(runner, public_cache, [cached_event('public')])
    write_cache(runner, tmp_path / 'private-sources' / 'cache',
                [cached_event('private', source='saveticker')])
    assert {row['id'] for row in runner.load_source_events(now=NOW)} == (
        {'public', 'private'} if private else {'public'})


def test_stream_preserves_recent_loader_cutoff_and_latest_delivery_dedupe(tmp_path, monkeypatch):
    runner = module()
    monkeypatch.delenv('WIKI_SOURCE_ALLOW_PRIVATE', raising=False)
    monkeypatch.setattr(runner.source_collector, 'DEFAULT_CACHE_DIR', tmp_path)
    old = NOW - timedelta(days=60, seconds=1)
    write_cache(runner, tmp_path, [cached_event('old', collected_at=old.isoformat())], now=old)
    write_cache(runner, tmp_path, [cached_event('same', title='older'),
        cached_event('shadowed'), cached_event('same', title='newer'),
        cached_event('shadowed', content_kind='macro_snapshot')])
    reference = runner.source_collector.load_recent_events(tmp_path, now=NOW, hours=24 * 60)
    expected = [row for row in reference if runner._full_body_candidate(row)]
    assert list(runner.load_source_events(now=NOW)) == list(reversed(expected))


def test_large_irrelevant_records_are_not_accumulated_and_tail_is_discovered(tmp_path, monkeypatch):
    import tracemalloc
    runner = module()
    monkeypatch.delenv('WIKI_SOURCE_ALLOW_PRIVATE', raising=False)
    monkeypatch.setattr(runner.source_collector, 'DEFAULT_CACHE_DIR', tmp_path)
    large = 'x' * (512 * 1024)
    def rows():
        yield cached_event('head')
        for index in range(80):
            yield cached_event(f'snapshot-{index}', content_kind='prediction_market_snapshot', body_raw=large)
        yield cached_event('tail')
    write_cache(runner, tmp_path, rows())
    monkeypatch.setattr(runner.source_collector, 'load_recent_events',
                        lambda *a, **kw: pytest.fail('unbounded loader invoked'))
    stats = {}
    tracemalloc.start()
    try:
        retained = list(runner.load_source_events(now=NOW, stats=stats))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert [row['id'] for row in retained] == ['tail', 'head']
    assert stats == {'scanned_events': 82, 'duplicate_events': 0, 'full_body_events': 2}
    assert peak < 12 * 1024 * 1024


def test_streamed_discovery_bounds_candidates_counts_backlog_and_replays_evidence(tmp_path, monkeypatch):
    from reports.wiki_source_journal import Journal
    runner = module()
    monkeypatch.setattr(runner.source_collector, 'DEFAULT_CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '1')
    monkeypatch.delenv('WIKI_SOURCE_ALLOW_PRIVATE', raising=False)
    write_cache(runner, tmp_path / 'cache', [cached_event(str(i)) for i in range(9)])
    seen = []
    def discover(events, **kwargs):
        assert len(events) == 1
        event = events[0]
        seen.append(event['id'])
        return {'eligible': [{'revision_id': event['id'], 'role': 'support', 'provider': 'arca',
                             'scope': 'article_fulltext', 'body': 'a full paragraph of the article body',
                             'canonical_url': event['url'], 'published_at': event['id']}],
                'unavailable': [], 'pending_chunking': []}
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions', discover)
    completed = set()
    monkeypatch.setattr(runner, 'pending_public_source', lambda source, _: source['revision_id'] not in completed)
    journal = Journal(tmp_path / 'journal')
    with runner.discover_source_batch(journal, NOW, limit=2) as (sources, relevant, pending, stats):
        assert [source['revision_id'] for source in sources] == ['0', '1']
        assert all('body' not in source for source in sources)
        assert [row['id'] for row in relevant(sources[0])] == ['0']
        assert pending(sources[0])
        assert stats['pending_sources'] == 9
        assert stats['retained_candidates'] == 2
        assert stats['deferred_pending_sources'] == 7
        completed.update(source['revision_id'] for source in sources)
    assert set(seen) == {str(i) for i in range(9)}
    with runner.discover_source_batch(journal, NOW, limit=2) as (sources, _, _, stats):
        assert [source['revision_id'] for source in sources] == ['2', '3']
        assert stats['pending_sources'] == 7

def test_tail_full_body_passes_real_archive_discovery(tmp_path, monkeypatch):
    from reports.wiki_source_journal import Journal
    runner = module()
    monkeypatch.setenv('STOCK_REPORT_REPORTS_DIR', str(tmp_path))
    monkeypatch.delenv('WIKI_SOURCE_ALLOW_PRIVATE', raising=False)
    monkeypatch.setattr(runner.source_collector, 'DEFAULT_CACHE_DIR', tmp_path / 'cache')
    body = 'Reported capacity improved, but realized orders remain uncertain.'
    url = 'https://t.me/public_channel/7'
    record = runner.raw_archive.save_raw_artifact('telegram', 'text', NOW, 'Capacity', url, body, '.txt')
    record.update(body_coverage='full', text_complete=True, extraction_scope='public_message')
    runner.raw_archive.save_extracted_text(record, body)
    event = cached_event('tail', source='telegram:public_channel', url=url, title='Capacity',
                         content_kind='community_signal', extraction_scope='public_message',
                         **{key: record[key] for key in ('raw_path', 'text_path', 'manifest_path')})
    write_cache(runner, tmp_path / 'cache', [
        *[cached_event(f'noise-{i}', content_kind='macro_snapshot') for i in range(100)], event])
    monkeypatch.setattr(runner, 'pending_public_source', lambda *a: True)
    with runner.discover_source_batch(Journal(tmp_path / 'journal'), NOW, limit=1) as batch:
        sources, relevant, _, stats = batch
        assert len(sources) == stats['eligible_sources'] == stats['pending_sources'] == 1
        assert sources[0]['canonical_url'] == url
        assert list(relevant(sources[0])) == [event]
        assert stats['scanned_events'] == 101
        assert stats['full_body_events'] == 1



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
    # Previously compiled originals are enrolled for topic backfill, not lost.
    assert runner.pending_public_source({**source, 'revision_id': 'b' * 64}, journal)


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
    monkeypatch.setattr(runner, 'load_source_events', lambda **kw: iter([{}]))
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions',
                        lambda *a, **kw: {'eligible': [support], 'unavailable': []})
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


def test_selection_caps_five_and_prefers_newest_pending_public_revisions():
    sources = [{'revision_id': format(i, '064x'), 'provider': 'telegram:public_channel',
                'scope': 'public_message', 'canonical_url': f'https://t.me/public_channel/{i}',
                'published_at': f'2026-10-{i:02d}', 'collected_at': ''} for i in range(1, 8)]
    private = {**sources[0], 'revision_id': 'f' * 64, 'provider': 'saveticker'}
    selected = module().select_pending([*reversed(sources), private], lambda source: source['revision_id'] != sources[0]['revision_id'])
    assert len(selected) == 5
    assert [source['revision_id'] for source in selected] == [source['revision_id'] for source in sources[:1:-1]]
    assert all(is_public(source) for source in selected)


def test_selection_takes_turns_between_publishers_and_starts_with_the_longest_waiting():
    def post(channel, day):
        return {'revision_id': f'{channel}-{day:02d}', 'provider': f'telegram:{channel}', 'scope': 'public_message',
                'canonical_url': f'https://t.me/{channel}/{day}', 'published_at': f'2026-10-{day:02d}', 'collected_at': ''}
    busy = [post('insidertracking', day) for day in range(1, 9)]
    sources = [*busy, post('FastStockNewsUSA', 20), post('FastStockNewsUSA', 21), post('thekobeissiletter', 22)]
    runner = module()

    # 글이 많은 채널도 한도 5건을 혼자 쓰지 못하고, 각 채널에서는 최신 글부터 나온다.
    assert [s['revision_id'] for s in runner.select_pending(sources, lambda source: True)] == [
        'thekobeissiletter-22', 'FastStockNewsUSA-21', 'insidertracking-08', 'FastStockNewsUSA-20',
        'insidertracking-07']
    # 방금 다룬 발행자는 뒤로 가고, 한 번도 다루지 못한 발행자가 먼저 온다.
    served = {publisher_name('insidertracking'): '2026-10-16T03:00:00',
              publisher_name('FastStockNewsUSA'): '2026-10-16T02:00:00'}
    assert [s['revision_id'] for s in runner.select_pending(sources, lambda source: True, limit=3, served=served)] == [
        'thekobeissiletter-22', 'FastStockNewsUSA-21', 'insidertracking-08']


def publisher_name(channel):
    from reports.source_publishers import publisher
    return publisher(f'telegram:{channel}')['id']


def channel_post(channel, day, **extra):
    return {'revision_id': f'{channel}-{day:02d}', 'provider': f'telegram:{channel}', 'scope': 'public_message',
            'canonical_url': f'https://t.me/{channel}/{day}', 'published_at': f'2026-10-{day:02d}',
            'collected_at': '', **extra}


def test_untried_research_goes_ahead_of_relays_and_of_its_own_retries():
    sources = [channel_post('insidertracking', 25), channel_post('insidertracking', 24),
               channel_post('kwusa', 20), channel_post('kwusa', 21), channel_post('mirae_dm', 22),
               # 이미 실패한 증권사 글은 우선권이 없다: 다른 채널과 같은 순번을 기다린다.
               channel_post('mirae_dm', 23, last_attempt_at='2026-10-16T01:00:00+00:00')]
    # 방금 다룬 증권사도 새 글은 먼저 나온다. 이어서 발행자별 순번(가장 오래 기다린 채널부터).
    served = {publisher_name('kwusa'): '2026-10-16T03:00:00', publisher_name('mirae_dm'): '2026-10-16T02:00:00'}
    picked = module().select_pending(sources, lambda source: True, limit=6, served=served)
    assert [s['revision_id'] for s in picked] == [
        'mirae_dm-22', 'kwusa-21', 'kwusa-20', 'insidertracking-25', 'mirae_dm-23', 'insidertracking-24']


def test_a_post_that_just_failed_waits_before_it_is_tried_again():
    runner = module()
    sources = [channel_post('kwusa', 20, last_attempt_at='2026-10-16T09:00:00+00:00'),
               channel_post('kwusa', 21, last_attempt_at='2026-10-16T05:00:00+00:00'),
               channel_post('insidertracking', 22)]
    waiting = (NOW - runner.RETRY_AFTER).isoformat()   # 06:00 UTC
    assert [s['revision_id'] for s in runner.select_pending(sources, lambda source: True, retry_before=waiting)] == [
        'insidertracking-22', 'kwusa-21']


@pytest.mark.parametrize('body,contentless', [
    ('https://youtu.be/pJl3U24udLw 프리마켓 현황', True),
    ('코어위브 애프터 7% 상승 확대', True),
    ('코스트코는 $30 특별 배당 발표후 애프터마켓 상승.', False),
    ('공포 탐욕지수는 FEAR 33에서 37로 상승', False),
])
def test_a_bare_link_caption_or_price_tick_is_contentless(body, contentless):
    assert module()._contentless({'body': body}) is contentless


WINDOWS = [{'minutes': 300, 'used_percent': 6, 'resets_at': 1}, {'minutes': 10080, 'used_percent': 73, 'resets_at': 2}]


def account(short=6, weekly=73, **changes):
    return {'allowed': True, 'reached': None, 'plan': 'plus', 'windows': [
        {**WINDOWS[0], 'used_percent': short}, {**WINDOWS[1], 'used_percent': weekly}], **changes}


@pytest.mark.parametrize('usage,order,reason', [
    (account(), ['codex', 'hermes'], None),
    (account(short=69, weekly=89), ['codex'], None),
    (account(short=70), ['codex'], 'usage_window_300m_at_70'),
    (account(weekly=90), ['codex'], 'usage_window_10080m_at_90'),
    (account(allowed=False), ['codex'], 'usage_limit_reached'),
    (account(reached='primary'), ['codex'], 'usage_limit_reached'),
    # 측정값을 못 읽었거나 Codex 가 첫 제공자가 아니면 막지 않는다.
    (None, ['codex'], None),
    (account(short=99), ['openrouter', 'codex'], None),
])
def test_provider_is_busy_near_the_end_of_a_usage_window(usage, order, reason):
    assert module().provider_busy(usage, order) == reason


def test_apply_leaves_a_busy_provider_alone_without_scanning_or_reserving(tmp_path, monkeypatch, capsys):
    runner = module()
    root = _apply_setup(runner, tmp_path, monkeypatch)
    root.mkdir(parents=True)
    monkeypatch.delenv('WIKI_STRUCTURED_PROVIDERS', raising=False)
    monkeypatch.setattr(runner.llm_cli, 'codex_usage', lambda: account(short=88))
    monkeypatch.setattr(runner, 'load_source_events', lambda **kw: pytest.fail('scanned sources with a busy provider'))
    assert runner.main() == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (summary['skipped'], summary['reason'], summary['attempted']) == (
        'provider_busy', 'usage_window_300m_at_88', 0)
    assert summary['usage']['before']['windows'][0]['used_percent'] == 88
    assert not (root / 'automatic-budget.json').exists() and not (root / 'backups').exists()


def test_relays_wait_for_the_days_pace_while_untried_research_does_not(tmp_path, monkeypatch, capsys):
    runner = module()
    _apply_setup(runner, tmp_path, monkeypatch)
    channels = {1: 'insidertracking', 2: 'kwusa', 3: 'thekobeissiletter', 4: 'mirae_dm'}
    monkeypatch.setattr(runner, 'load_source_events', lambda **kw: iter([{'n': n} for n in channels]))
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions', lambda events, **kw: {
        'eligible': [{'revision_id': format(events[0]['n'], '064x'), 'role': 'evidence',
                      'provider': f"telegram:{channels[events[0]['n']]}", 'scope': 'public_message',
                      'canonical_url': f"https://t.me/{channels[events[0]['n']]}/{events[0]['n']}",
                      'published_at': f"2026-10-0{events[0]['n']}"}], 'unavailable': []})
    monkeypatch.setattr(runner.pipeline, '_pending_source', lambda *args: True)
    seen = []
    monkeypatch.setattr(runner, 'process',
                        lambda source, *args, **kw: seen.append(source['revision_id']) or {'data_committed': 1})
    # 하루의 이 시각까지 허용된 몫은 3건: 증권사 글 2건은 그와 무관하게 들어가고, 전달 채널은 1건만 들어간다.
    monkeypatch.setattr(runner.quota, 'paced_limit', lambda: 3)

    assert runner.main() == 0
    assert seen == [format(4, '064x'), format(2, '064x'), format(3, '064x')]
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (summary['quota']['count'], summary['quota']['paced_limit']) == (3, 3)


def test_selection_compares_instants_and_retries_after_untouched_posts():
    def post(name, **stamps):
        return {'revision_id': name, 'provider': 'telegram:kwusa', 'scope': 'public_message',
                'canonical_url': f'https://t.me/kwusa/{len(name)}', 'published_at': '', 'collected_at': '', **stamps}
    sources = [
        # 수집 시각은 KST, 발행 시각은 UTC 다: 글자 순서로는 morning 이 더 늦어 보인다.
        post('morning', collected_at='2026-11-16T09:05:00+09:00'),      # 00:05 UTC
        post('afternoon', published_at='2026-11-16T05:10:00+00:00'),
        post('retried', published_at='2026-11-16T08:00:00+00:00', last_attempt_at='2026-11-16T08:30:00+00:00'),
        post('undated'),
    ]
    assert [s['revision_id'] for s in module().select_pending(sources, lambda source: True)] == [
        'afternoon', 'morning', 'undated', 'retried']


def test_discovery_puts_the_publisher_compiled_last_run_at_the_back(tmp_path, monkeypatch):
    """편입이 끝난 글은 다음 스캔에 나오지 않는다 — 누가 방금 뽑혔는지는 저널 원장으로 안다."""
    from reports.wiki_source_journal import Journal
    runner = module()
    monkeypatch.setattr(runner.source_collector, 'DEFAULT_CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '1')
    monkeypatch.delenv('WIKI_SOURCE_ALLOW_PRIVATE', raising=False)
    providers = {'i1': 'telegram:insidertracking', 'i2': 'telegram:insidertracking', 'k1': 'telegram:kwusa'}
    bodies = {'i2': 'https://youtu.be/pJl3U24udLw 프리마켓 현황'}
    write_cache(runner, tmp_path / 'cache', [cached_event(identity) for identity in providers])
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions', lambda events, **kwargs: {
        'eligible': [{'revision_id': events[0]['id'], 'role': 'support', 'provider': providers[events[0]['id']],
                      'scope': 'public_message_text', 'canonical_url': events[0]['url'],
                      'body': bodies.get(events[0]['id'], 'a full paragraph of the post body'),
                      'published_at': events[0]['id']}],
        'unavailable': [], 'pending_chunking': []})
    monkeypatch.setattr(runner, 'pending_public_source', lambda source, _: True)
    monkeypatch.setattr(runner, 'is_public', lambda source: True)
    journal = Journal(tmp_path / 'journal')
    for folder, name, payload in (
            ('automatic-attempts', 'a.json', {'reservation': {'id': 'a', 'revision_id': 'done', 'at': '2026-10-16T03:00:00'}}),
            ('sources', 'done.json', {'revision_id': 'done', 'source': {'provider': 'telegram:insidertracking'}})):
        (journal.root / folder).mkdir(parents=True, exist_ok=True)
        (journal.root / folder / name).write_text(json.dumps(payload))

    with runner.discover_source_batch(journal, NOW, limit=2) as (sources, _, already_pending, stats):
        assert [source['revision_id'] for source in sources] == ['k1', 'i1']
        assert stats['publisher_last_attempt'] == {'미국 주식 인사이더': '2026-10-16T03:00:00'}
        # 링크뿐인 글은 후보가 아니고, 밀린 글 수에도 들지 않는다.
        assert (stats['contentless_sources'], stats['pending_sources']) == (1, 2)
        assert not already_pending({'revision_id': 'i2', 'role': 'support'})


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
    monkeypatch.setattr(runner, 'load_source_events', lambda **kw: iter([{}]))
    source = {'revision_id': 'a' * 64, 'role': 'evidence', 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions',
                        lambda *args, **kw: {'eligible': [source], 'unavailable': []})
    monkeypatch.setattr(runner.pipeline, '_resume_compilations', lambda *args: ([], []))
    monkeypatch.setattr(runner.pipeline, '_pending_source', lambda *args: True)
    monkeypatch.setattr(runner, 'process', lambda *args: {'compile_failed': 1})
    assert runner.main() == 2


def test_unanswered_compile_gives_the_reservation_back_and_ends_the_batch(tmp_path, monkeypatch, capsys):
    runner = module()
    root = _apply_setup(runner, tmp_path, monkeypatch)
    monkeypatch.setattr(runner, 'load_source_events', lambda **kw: iter([{'n': 1}, {'n': 2}, {'n': 3}]))
    monkeypatch.setattr(runner.wiki_source_revision, 'discover_revisions', lambda events, **kw: {
        'eligible': [{'revision_id': format(events[0]['n'], '064x'), 'role': 'evidence',
                      'provider': 'telegram:public_channel', 'scope': 'public_message',
                      'canonical_url': f"https://t.me/public_channel/{events[0]['n']}",
                      'published_at': f"2026-10-0{events[0]['n']}"}], 'unavailable': []})
    monkeypatch.setattr(runner.pipeline, '_pending_source', lambda *args: True)
    outcomes = iter([{'data_committed': 1}, {'compile_failed': 1, 'compile_unanswered': 1}])
    seen = []
    monkeypatch.setattr(runner, 'process', lambda source, *args, **kw: seen.append(source['revision_id']) or next(outcomes))

    assert runner.main() == 2
    # 최신 글부터 두 건을 시도했고, 답을 못 받은 두 번째에서 멈췄다. 한도는 답을 받은 한 건만 쓴다.
    assert seen == [format(3, '064x'), format(2, '064x')]
    assert runner.quota.status(root / 'automatic-budget.json')['count'] == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['attempted'] == 2


def test_process_marks_a_compile_no_provider_answered_and_records_each_model_call(tmp_path, monkeypatch):
    from reports.wiki_source_journal import Journal
    runner = module()
    monkeypatch.delenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', raising=False)
    source = {'revision_id': 'a' * 64, 'role': 'evidence', 'provider': 'telegram:public_channel',
              'scope': 'public_message', 'canonical_url': 'https://t.me/public_channel/1'}
    prompt = 'Compile one source.\n' + json.dumps({'revision_id': source['revision_id']}) + \
        '\nBEGIN UNTRUSTED SOURCE TEXT\nbody'
    answers = iter([None, '{"claims":[]}'])
    options = []

    def provider(text, **kwargs):
        options.append(kwargs)
        kwargs['trace'].append({'provider': 'codex', 'seconds': 120.0, 'limit': 120.0, 'outcome': 'no_text'})
        return next(answers)
    monkeypatch.setattr(runner, 'structured_llm_call', provider)
    monkeypatch.setattr(runner.pipeline, 'run',
                        lambda **kw: {'compile_failed': int(kw['llm_fn'](prompt) is None)})
    journal = Journal(tmp_path)
    (journal.root / 'automatic-attempts').mkdir(parents=True, exist_ok=True)

    silent = runner.process(source, [], journal, {'id': 'one', 'revision_id': source['revision_id']})
    answered = runner.process(source, [], journal, {'id': 'two', 'revision_id': source['revision_id']})

    assert silent == {'compile_failed': 1, 'compile_unanswered': 1}
    assert answered == {'compile_failed': 0}
    assert options[0]['provider_timeout'] == runner.COMPILE_PROVIDER_TIMEOUT
    saved = json.loads((journal.root / 'automatic-attempts' / 'one.json').read_text())
    assert saved['model_calls'][0]['answered'] is False
    assert saved['model_calls'][0]['providers'] == [
        {'provider': 'codex', 'seconds': 120.0, 'limit': 120.0, 'outcome': 'no_text'}]
    assert saved['model_calls'][0]['seconds'] >= 0


def _apply_setup(runner, tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['rollout', '--apply'])
    monkeypatch.setenv('WIKI_SOURCE_PIPELINE_ENABLED', '0')
    monkeypatch.delenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', raising=False)
    monkeypatch.setattr(runner.shutil, 'which', lambda command: '/synthetic/codex')
    monkeypatch.setattr(runner.raw_archive, 'reports_root', lambda: tmp_path)
    monkeypatch.setattr(runner.pipeline, '_resume_compilations', lambda *args: ([], []))
    return tmp_path / 'public-wiki-pilot-20261013'


def test_apply_with_exhausted_quota_skips_discovery_and_backup(tmp_path, monkeypatch, capsys):
    runner = module()
    root = _apply_setup(runner, tmp_path, monkeypatch)
    root.mkdir(parents=True)
    for i in range(runner.quota.configured_limit()):
        assert runner.quota.reserve(root / 'automatic-budget.json', format(i, '064x'))
    monkeypatch.setattr(runner, 'load_source_events',
                        lambda **kw: pytest.fail('scanned sources with exhausted quota'))
    assert runner.main() == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary['attempted'] == 0 and summary['quota']['count'] == summary['quota']['limit']
    assert not (root / 'backups').exists()


def test_apply_prunes_event_backups_to_retention(tmp_path, monkeypatch):
    runner = module()
    root = _apply_setup(runner, tmp_path, monkeypatch)
    backups = root / 'backups'
    backups.mkdir(parents=True)
    for i in range(runner.BACKUP_RETENTION + 5):
        (backups / f'20261001T{i:06d}000000Z-events.jsonl').write_text('old')
    monkeypatch.setattr(runner, 'load_source_events', lambda **kw: iter([]))
    monkeypatch.setattr(runner.shared_memory, '_paths', lambda: {'events': str(tmp_path / 'no-events.jsonl')})
    assert runner.main() == 0
    names = sorted(path.name for path in backups.iterdir())
    assert len(names) == runner.BACKUP_RETENTION
    assert names[0] == f'20261001T{5:06d}000000Z-events.jsonl'
