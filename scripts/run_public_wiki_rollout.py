"""Bounded verified-full-body wiki worker; without --apply this is read-only."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

import safe_io
from agent_console import shared_memory
from reports import raw_archive, source_collector, wiki_public_budget as quota, wiki_source_revision
from reports import wiki_source_pipeline as pipeline
from reports.wiki_source_journal import Journal
from scripts.run_public_wiki_pilot import source_allowed as is_public, filter_targets, public_target_ids, validate_public_targets, hydrate_public_target
from reports.wiki_source_compiler import structured_llm_call


def _full_body_candidate(row):
    """Cheap prefilter only; discovery still verifies the immutable archive."""
    if not all(row.get(field) for field in ('raw_path', 'text_path', 'manifest_path')):
        return False
    classification = row.get('classification')
    kind = str(row.get('content_kind') or row.get('kind') or
               (classification.get('kind') if isinstance(classification, dict) else '') or '')
    scope = row.get('source_text_scope') or row.get('extraction_scope')
    return (scope != 'structured_snapshot'
            and 'snapshot' not in kind.lower()
            and row.get('record_kind') != 'market_snapshot'
            and kind.lower() not in {'prediction_market', 'prediction', 'market_data'})


def load_source_events(*, now=None, stats=None):
    """Stream newest unique provenance rows without retaining the source corpus.

    Match load_recent_events' cutoff, file order and latest-delivery dedupe,
    including a newer ineligible delivery shadowing an older eligible one.
    The ID set spills to disk, not an unbounded Python set. Private cache access
    remains conditional on the existing explicit opt-in.
    """
    now = now or datetime.now(source_collector.KST)
    cutoff = now.astimezone(source_collector.KST) - timedelta(days=int(os.getenv("WIKI_SOURCE_SCAN_DAYS", "7")))
    stats = stats if stats is not None else {}
    stats.update(scanned_events=0, duplicate_events=0, full_body_events=0)
    caches = [source_collector.DEFAULT_CACHE_DIR]
    if os.getenv('WIKI_SOURCE_ALLOW_PRIVATE', '0') == '1':
        caches.append(raw_archive.reports_root() / 'private-sources' / 'cache')
    with tempfile.TemporaryDirectory(prefix='wiki-source-scan-') as directory:
        with closing(sqlite3.connect(str(Path(directory) / 'seen.sqlite3'))) as database:
            database.execute('CREATE TABLE seen (cache INTEGER, id TEXT, PRIMARY KEY(cache, id))')
            for cache_number, cache in enumerate(caches):
                for days_back in range(63):
                    path = source_collector._event_file(Path(cache), now - timedelta(days=days_back))
                    if not path.exists():
                        continue
                    rows = source_collector._iter_recent_jsonl_rows(path, cutoff)
                    try:
                        for row in rows:
                            stats['scanned_events'] += 1
                            identity = row.get('id') or source_collector.event_id(row)
                            inserted = database.execute('INSERT OR IGNORE INTO seen VALUES (?, ?)',
                                                        (cache_number, str(identity))).rowcount
                            if not inserted:
                                stats['duplicate_events'] += 1
                                continue
                            if _full_body_candidate(row):
                                stats['full_body_events'] += 1
                                yield row
                    finally:
                        if hasattr(rows, 'close'):
                            rows.close()


def _selection_key(source):
    return (source.get('last_attempt_at', ''),
            source.get('published_at') or source.get('collected_at') or '',
            source['revision_id'])


@contextmanager
def discover_source_batch(journal, now, *, limit):
    """Scan the full window, retaining at most limit pending source metadata.

    Verified bodies are released after each discovery. Selected deliveries are
    replayed from a temporary disk spool, and every deferred unique revision is
    counted explicitly; the next run scans it again rather than losing a tail.
    """
    stats = {'eligible_sources': 0, 'pending_sources': 0, 'unavailable_events': 0}
    selected = []
    with tempfile.TemporaryDirectory(prefix='wiki-source-batch-') as directory:
        with closing(sqlite3.connect(str(Path(directory) / 'batch.sqlite3'))) as database:
            database.executescript(
                'CREATE TABLE revisions (id TEXT, role TEXT, pending INTEGER, PRIMARY KEY(id, role));'
                'CREATE TABLE events (revision TEXT, payload TEXT);'
                'CREATE INDEX event_revision ON events(revision);'
                'CREATE TABLE attempts (id TEXT PRIMARY KEY, at TEXT);')
            for path in (journal.root / 'automatic-attempts').glob('*.json'):
                reservation = json.loads(path.read_text())['reservation']
                database.execute('INSERT INTO attempts VALUES (?, ?) ON CONFLICT(id) DO UPDATE '
                                 'SET at = max(at, excluded.at)',
                                 (reservation['revision_id'], reservation['at']))
            events = load_source_events(now=now, stats=stats)
            try:
                for event in events:
                    discovered = wiki_source_revision.discover_revisions([event], now=now, allow_chunking=True)
                    stats['unavailable_events'] += len(discovered['unavailable'])
                    for source in discovered['eligible']:
                        if not is_public(source):
                            continue
                        identity, role = source['revision_id'], source.get('role', 'support')
                        database.execute('INSERT INTO events VALUES (?, ?)',
                                         (identity, json.dumps(event, ensure_ascii=False)))
                        existing = database.execute('SELECT 1 FROM revisions WHERE id=? AND role=?',
                                                    (identity, role)).fetchone()
                        if existing:
                            continue
                        pending = pending_public_source(source, journal)
                        database.execute('INSERT INTO revisions VALUES (?, ?, ?)', (identity, role, pending))
                        stats['eligible_sources'] += 1
                        stats['pending_sources'] += int(pending)
                        if pending:
                            attempted = database.execute('SELECT at FROM attempts WHERE id=?', (identity,)).fetchone()
                            source = {key: value for key, value in source.items() if key != 'body'}
                            source['role'] = role
                            source['last_attempt_at'] = attempted[0] if attempted else ''
                            selected.append(source)
                            selected.sort(key=_selection_key)
                            del selected[limit:]
            finally:
                if hasattr(events, 'close'):
                    events.close()
            stats['retained_candidates'] = len(selected)
            stats['deferred_pending_sources'] = stats['pending_sources'] - len(selected)

            def relevant(source):
                return (json.loads(row[0]) for row in database.execute(
                    'SELECT payload FROM events WHERE revision=?', (source['revision_id'],)))

            def already_pending(source):
                row = database.execute('SELECT pending FROM revisions WHERE id=? AND role=?',
                                       (source['revision_id'], source.get('role', 'support'))).fetchone()
                return bool(row and row[0])

            yield selected, relevant, already_pending, stats


def select_pending(sources, pending, *, limit=5):
    return sorted([source for source in sources if is_public(source) and pending(source)],
                  key=_selection_key)[:limit]


def plan_status(path, *, candidates, limit=5):
    budget = quota.status(path)
    return {'apply': False, 'pending_public_sources': candidates,
            'planned_attempts': max(0, min(limit, candidates, budget['limit'] - budget['count'])),
            'quota': budget}


def public_revisions(journal, *, excluded=()):
    return {path.stem for path in (journal.root / 'sources').glob('*.json')
            if path.stem not in excluded and is_public(journal.load_source(path.stem)['source'])}


def _topic_stage_path(source, journal):
    identity, role = source['revision_id'], source.get('role', 'support')
    if not re.fullmatch(r'[a-f0-9]{64}', identity) or role not in {'support', 'conflict'}:
        raise ValueError('topic_stage_identity_invalid')
    return journal.root / 'topic-stages' / f'{identity}-{role}.json'


def _read_topic_stage(path):
    data = json.loads(path.read_text())
    if type(data) is not dict:
        raise ValueError('topic_stage_corrupt')
    if 'schema_version' in data:
        if (type(data['schema_version']) is not int or data['schema_version'] != 3
                or set(data) != {'schema_version', 'revision_id', 'role', 'state', 'context_mode', 'note_ids'}
                or data['context_mode'] != 'source_note_snapshot'
                or type(data['note_ids']) is not list
                or any(type(identity) is not str or not re.fullmatch(r'[a-f0-9]{64}', identity)
                       for identity in data['note_ids'])):
            raise ValueError('topic_stage_corrupt')
    elif set(data) != {'revision_id', 'role', 'state'}:
        raise ValueError('topic_stage_corrupt')
    if (type(data['revision_id']) is not str or not re.fullmatch(r'[a-f0-9]{64}', data['revision_id'])
            or data['state'] not in {'pending', 'failed', 'done'}
            or data['role'] not in {'support', 'conflict'}
            or path.name != f"{data['revision_id']}-{data['role']}.json"):
        raise ValueError('topic_stage_corrupt')
    return data


def pending_public_source(source, journal):
    topic_enabled = os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') == '1'
    receipt = pipeline._public_topic_for_source(source, journal)
    if receipt and (receipt['schema_version'] == 3 or topic_enabled):
        return False
    if source.get('context_mode') == 'source_note_snapshot' and not pipeline._public_v3_enabled():
        return False
    path = (_topic_stage_path(source, journal)
            if topic_enabled or source.get('role', 'support') in {'support', 'conflict'} else None)
    stage = _read_topic_stage(path) if path is not None and path.exists() else None
    if stage and stage.get('schema_version') == 3 and not pipeline._public_v3_enabled():
        return False
    receipt = journal.load_source(source['revision_id'])
    if receipt and receipt['compile_state'] == 'skipped':
        return False
    if pipeline._pending_source(source, journal):
        return True
    if os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') != '1':
        return False
    return stage is None or stage['state'] in {'pending', 'failed'}


def resume_topic_sources(journal, now):
    if os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') != '1':
        return [], []
    from reports.wiki_topic_context import _verified_source
    sources, reasons = [], []
    for path in sorted((journal.root / 'topic-stages').glob('*.json')):
        stage = _read_topic_stage(path)
        if stage['state'] == 'done':
            continue
        if pipeline._public_topic_for_source(stage, journal):
            continue
        if (stage.get('schema_version') == 3
                and os.getenv('AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED', '0') != '1'):
            continue
        try:
            source, _, receipt = _verified_source(journal, stage['revision_id'], now)
            evidence = receipt['evidence_by_role'][stage['role']]
            local = ({'context_mode': stage['context_mode'], 'note_ids': stage['note_ids']}
                     if stage.get('schema_version') == 3 else {})
            sources.append({**source, 'role': stage['role'], 'observed_evidence_ids': evidence, **local})
        except (ValueError, KeyError, TypeError, OSError, UnicodeError):
            reasons.append('topic_resume_unavailable')
    return sources, reasons


def validate_topic_prompt(data, *, journal, reserved_revision_id, now,
                          context_mode="legacy_pages", pinned_context=None):
    """Bind the final provider envelope to current public archive/store evidence."""
    if context_mode == "source_note_snapshot":
        return _validate_snapshot_topic_prompt(data, journal=journal,
            reserved_revision_id=reserved_revision_id, now=now, pinned_context=pinned_context)
    if context_mode != "legacy_pages":
        raise ValueError('topic_context_mode_invalid')
    from agent_console import shared_memory, wiki
    from reports.wiki_topic_context import _note, _verified_source, revalidate_topic_context, topic_context_model_input
    from reports.wiki_topic_integration import _evidence, _prompt_target
    try:
        if (type(data) is not dict or set(data) != {'compiled', 'source_map', 'editable_topics',
                                                  'read_only_source_context', 'surface'}
                or data['compiled']['revision_id'] != reserved_revision_id
                or data['surface'] != 'market'):
            raise ValueError('topic_prompt_invalid')
        source, compiled, _ = _verified_source(journal, reserved_revision_id, now)
        source['role'] = 'support'  # Role does not change the evidence projection.
        rows = shared_memory.inspect_records()
        supplied = data['read_only_source_context']
        if type(supplied) is not dict or set(supplied) != {'notes'} or type(supplied['notes']) is not list:
            raise ValueError('topic_prompt_invalid')
        context = {'notes': supplied['notes'], 'dependencies': [], 'omitted': []}
        for note in supplied['notes']:
            _, dependency = _note(note['page_id'], note['revision_id'], journal=journal,
                                  records=rows, now=now, surface='market')
            context['dependencies'].append(dependency)
        revalidate_topic_context(context, journal=journal, records=rows, now=now)
        targets = []
        for target in data['editable_topics']:
            validate_public_targets([{'page_id': target['page_id'],
                                      'revision_hash': target['revision_hash']}],
                                    journal, public_revisions(journal))
            targets.append(wiki.semantic_snapshot(target['page_id'], records=rows))
        current, _, mapping, _, _, _, _ = _evidence(compiled, source, context, targets)
        expected = {'compiled': current, 'source_map': mapping, 'surface': 'market',
                    'editable_topics': [_prompt_target(target) for target in targets],
                    'read_only_source_context': topic_context_model_input(context)}
        if data != expected:
            raise ValueError('topic_prompt_changed')
        return reserved_revision_id
    except (KeyError, TypeError, OSError, UnicodeError) as exc:
        raise ValueError('topic_prompt_invalid') from exc


def _validate_snapshot_topic_prompt(data, *, journal, reserved_revision_id, now, pinned_context):
    from agent_console import wiki
    from reports.wiki_topic_snapshot_context import _same, revalidate_snapshot_context
    from reports.wiki_topic_v3 import (
        snapshot_topic_envelope, verified_current_v3, verified_targets_v3,
    )
    try:
        if (type(data) is not dict or set(data) != {'compiled', 'source_map', 'editable_topics',
                                                  'read_only_source_context', 'surface'}
                or data['compiled']['revision_id'] != reserved_revision_id
                or data['surface'] != 'market' or pinned_context is None):
            raise ValueError('topic_prompt_invalid')
        revalidate_snapshot_context(pinned_context, journal=journal, now=now)
        receipt = journal.load_source(reserved_revision_id)
        source = {**receipt['source'], 'role': 'support',
                  'observed_evidence_ids': receipt['observed_evidence_ids']}
        # The final envelope is role-independent, but new use still requires a
        # retained role and observed IDs. No model field supplies this authority.
        roles = receipt['evidence_by_role']
        source['role'] = next(role for role in ('support', 'conflict') if roles.get(role))
        source['observed_evidence_ids'] = roles[source['role']]
        compiled = journal.load_compilation(receipt)
        from reports.wiki_topic_context import _verified_source
        source_with_body, _, _ = _verified_source(journal, reserved_revision_id, now)
        source['body'] = source_with_body['body']
        compiled, source = verified_current_v3(compiled, source, journal=journal, now=now)
        rows = shared_memory.inspect_records()
        targets = [wiki.semantic_snapshot(target['page_id'], records=rows)
                   for target in data['editable_topics']]
        targets = verified_targets_v3(targets, journal=journal, records=rows, now=now)
        expected = snapshot_topic_envelope(compiled, source, pinned_context, targets)
        if not _same(data, expected):
            raise ValueError('topic_prompt_changed')
        return reserved_revision_id
    except (KeyError, TypeError, OSError, UnicodeError, StopIteration) as exc:
        raise ValueError('topic_prompt_invalid') from exc


def checked_topic_provider(llm_fn, *, journal, reserved_revision_id, now,
                           context_mode="legacy_pages", pinned_context=None):
    """Local-only pin closure; accepts matching v3 callback keywords.

    ``now`` may be a clock callable for fresh use. The underlying provider gets
    only the canonical prompt string, never the local mode or pins.
    """
    from copy import deepcopy
    from reports.wiki_topic_snapshot_context import _same
    from reports.wiki_topic_v3 import snapshot_topic_prompt, strict_topic_json
    bound_mode = context_mode
    pins = deepcopy(pinned_context)

    def checked(prompt, *, context_mode=bound_mode, pinned_context=pins):
        if context_mode != bound_mode or not _same(pinned_context, pins):
            raise ValueError('topic_prompt_changed')
        if type(prompt) is not str or not prompt.startswith('Propose append-only draft topic synthesis.'):
            raise ValueError('topic_prompt_invalid')
        data = strict_topic_json(prompt.rsplit('\n', 1)[-1])
        if context_mode == 'source_note_snapshot' and prompt != snapshot_topic_prompt(data):
            raise ValueError('topic_prompt_changed')
        validate_topic_prompt(data, journal=journal, reserved_revision_id=reserved_revision_id,
            now=now() if callable(now) else now, context_mode=context_mode, pinned_context=pins)
        return llm_fn(prompt)
    return checked


def process(source, events, journal, attempt, *, context_mode="legacy_pages", note_ids=None):
    identity = source['revision_id']
    receipt = pipeline._public_topic_for_source(source, journal)
    if not (receipt and receipt['schema_version'] == 3) and not is_public(source):
        raise ValueError('private_source_disallowed')
    if attempt.get('revision_id') != identity:
        raise ValueError('unreserved_source')
    if context_mode not in {'legacy_pages', 'source_note_snapshot'}:
        raise ValueError('topic_context_mode_invalid')
    if (context_mode == 'source_note_snapshot'
            and (os.getenv('AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED', '0') != '1'
                 or os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') != '1')):
        return {'topic': {'enabled': False}}
    topic_enabled = os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') == '1'
    if receipt and receipt['schema_version'] == 3 and not pipeline._public_v3_enabled():
        return {'topic': {'enabled': False}}
    if topic_enabled and receipt:
        topic = pipeline._recover_public_topic(receipt, journal=journal, now=datetime.now(timezone.utc))
        return {'topic': {'enabled': True, **topic},
                'effects_pending': int(any(value != 'done' for value in topic.get('effects', {}).values()))}
    stage_path = (_topic_stage_path(source, journal)
                  if source.get('role', 'support') in {'support', 'conflict'} else None)
    existing_stage = _read_topic_stage(stage_path) if stage_path is not None and stage_path.exists() else None
    if existing_stage and existing_stage.get('schema_version') == 3:
        if not pipeline._public_v3_enabled():
            return {'topic': {'enabled': False}}
        if context_mode != 'source_note_snapshot':
            raise ValueError('topic_context_mode_changed')
    if topic_enabled:
        if context_mode == 'source_note_snapshot' and (
                type(note_ids) is not list or any(type(note) is not str or not re.fullmatch(r'[a-f0-9]{64}', note)
                                                for note in note_ids)):
            raise ValueError('snapshot_note_ids_invalid')
        stage_path = _topic_stage_path(source, journal)
        stage = {'revision_id': identity, 'role': source.get('role', 'support'), 'state': 'pending'}
        if context_mode == 'source_note_snapshot':
            stage.update(schema_version=3, context_mode=context_mode, note_ids=list(note_ids))
        safe_io.atomic_write_json(str(stage_path), stage)
    calls = []
    original_targets, original_journal = pipeline.discover_targets, pipeline.Journal
    original_hydrate = pipeline.hydrate_target
    original_qmd = os.environ.get('AGENT_CONSOLE_QMD_ENABLED')
    def allowed_targets():
        return public_target_ids(journal, public_revisions(journal))
    def llm(prompt, *, context_mode="legacy_pages", pinned_context=None):
        if prompt.startswith('Compile'):
            data = json.loads(prompt.split('\nBEGIN UNTRUSTED SOURCE TEXT\n', 1)[0].splitlines()[-1])
            parent = data.get('chunk_parent_revision_id') or data['revision_id']
        elif prompt.startswith('Propose append-only draft topic synthesis.'):
            if context_mode == 'source_note_snapshot':
                from reports.wiki_topic_v3 import snapshot_topic_prompt, strict_topic_json
                data = strict_topic_json(prompt.rsplit('\n', 1)[1])
                if prompt != snapshot_topic_prompt(data):
                    raise ValueError('topic_prompt_changed')
            else:
                data = json.loads(prompt.rsplit('\n', 1)[1])
            parent = validate_topic_prompt(data, journal=journal,
                reserved_revision_id=identity, now=datetime.now(timezone.utc),
                context_mode=context_mode, pinned_context=pinned_context)
        else:
            data = json.loads(prompt.rsplit('\n', 1)[1])
            parent = data['compiled']['revision_id']
            validate_public_targets(data.get('candidates', data.get('targets', [])),
                                    journal, public_revisions(journal))
        if parent != identity:
            raise ValueError('unreserved_source_model_call')
        calls.append({'phase': prompt.split(' ', 1)[0], 'source_revision': identity})
        safe_io.atomic_write_json(str(journal.root / 'automatic-attempts' / (attempt['id'] + '.json')),
                                  {'reservation': attempt, 'model_calls': calls, 'stage': 'running'})
        print(json.dumps({'phase': calls[-1]['phase'], 'revision_id': identity}), flush=True)
        if context_mode == 'source_note_snapshot' and prompt.startswith('Propose append-only draft topic synthesis.'):
            return structured_llm_call(prompt)
        return structured_llm_call(
            'Use only supplied evidence. Preserve attribution and uncertainty. '
            'Do not use tools, read files, browse or load memories. Return only requested JSON.\n' + prompt)
    try:
        pipeline.discover_targets = lambda compiled, **kwargs: filter_targets(
            original_targets(compiled, **kwargs), allowed_targets())
        pipeline.Journal = lambda: journal
        pipeline.hydrate_target = lambda identity: hydrate_public_target(identity, journal, public_revisions(journal))
        os.environ['AGENT_CONSOLE_QMD_ENABLED'] = '0'
        result = pipeline.run(events=events, dry_run=False, llm_fn=llm, source_limit=1,
                              allowed_revision_ids={identity})
        receipt = journal.load_source(identity)
        is_skipped = bool(receipt) and receipt.get('compile_state') == 'skipped'
        if (topic_enabled and not is_skipped
                and not any(result.get(field, 0) for field in
                            ('compile_failed', 'routing_pending', 'effects_pending', 'stale_base'))):
            try:
                topic = pipeline.run_public_topic(source, journal=journal, llm_fn=llm,
                    reserved_revision_id=identity, now=datetime.now(timezone.utc),
                    context_mode=context_mode, note_ids=note_ids)
                result['topic'] = topic
                if (topic.get('stage') in {'stale-base', 'failed'}
                        or any(value != 'done' for value in topic.get('effects', {}).values())):
                    result['topic_failed'] = 1
            except Exception as exc:
                code = str(exc) if type(exc) is ValueError and re.fullmatch(r'[a-z_]{1,80}', str(exc)) else 'topic_failed'
                result['topic'] = {'enabled': True, 'stage': 'failed', 'reason': code}
                result['topic_failed'] = 1
            deferred = result['topic'].get('stage') == 'insufficient_context'
            effects_pending = result['topic'].get('stage') == 'effects-pending'
            stage['state'] = ('pending' if effects_pending else
                              'failed' if result.get('topic_failed') else 'pending' if deferred else 'done')
            safe_io.atomic_write_json(str(stage_path), stage)
    finally:
        pipeline.discover_targets, pipeline.Journal = original_targets, original_journal
        pipeline.hydrate_target = original_hydrate
        if original_qmd is None:
            os.environ.pop('AGENT_CONSOLE_QMD_ENABLED', None)
        else:
            os.environ['AGENT_CONSOLE_QMD_ENABLED'] = original_qmd
    safe_io.atomic_write_json(str(journal.root / 'automatic-attempts' / (attempt['id'] + '.json')),
                              {'reservation': attempt, 'model_calls': calls, 'stage': 'finished', 'result': result})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--source-limit', type=int, default=5)
    args = parser.parse_args(argv)
    if not 1 <= args.source_limit <= 20:
        raise ValueError('source_limit_invalid')
    if args.apply and not (shutil.which('codex') or shutil.which('hermes') or os.getenv('OPENROUTER_API_KEY')):
        raise ValueError('structured_provider_unavailable')
    journal = Journal(raw_archive.reports_root() / 'public-wiki-pilot-20261013')
    budget_path = journal.root / 'automatic-budget.json'
    now = datetime.now(timezone.utc)
    with discover_source_batch(journal, now, limit=args.source_limit) as batch:
        return _run_batch(args, journal, budget_path, now, batch)


def _run_batch(args, journal, budget_path, now, batch):
    candidates, relevant_events, already_pending, discovery_stats = batch
    topic_sources, topic_reasons = resume_topic_sources(journal, now)
    if not args.apply:
        available = {(s['revision_id'], s.get('role', 'support')): s for s in topic_sources}
        pending = discovery_stats['pending_sources'] + sum(
            pending_public_source(source, journal) and not already_pending(source)
            for source in available.values())
        print(json.dumps({**plan_status(budget_path, candidates=pending, limit=args.source_limit),
                          'discovery': discovery_stats}))
        return 0
    with safe_io.file_write_lock(str(journal.root / 'automatic-run')):
        topic_recovery = []
        topic_receipts = pipeline._public_topic_operations(journal)
        # Existing operations already own their attempt. Recovery, including a
        # stale retry, must not fall through to fresh selection in this batch.
        reserved_v3_revisions = {receipt['batch']['proposal']['revision_id'] for receipt in topic_receipts
                                if receipt['schema_version'] == 3 and receipt['stage'] in {'prepared', 'applied', 'committed'}}
        reserved_topic_keys = {(receipt['batch']['proposal']['revision_id'], receipt['batch']['proposal']['role'])
                               for receipt in topic_receipts if receipt['stage'] in {'prepared', 'applied', 'committed'}
                               and (receipt['schema_version'] == 3 or os.getenv(
                                   'AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') == '1')}
        if os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') == '1':
            for receipt in topic_receipts:
                if receipt['schema_version'] == 3 and not pipeline._public_v3_enabled():
                    continue
                if (receipt['stage'] in {'prepared', 'applied', 'committed'}
                        and (receipt['stage'] != 'committed' or any(
                            receipt[key] != 'done' for key in ('artifacts', 'search', 'log')))):
                    topic_recovery.append(pipeline._recover_public_topic(receipt, journal=journal, now=now))
        backup = journal.root / 'backups' / (now.strftime('%Y%m%dT%H%M%S%fZ') + '-events.jsonl')
        backup.parent.mkdir(parents=True, exist_ok=True)
        with shared_memory._events_lock():
            events_path = shared_memory._paths()['events']
            if Path(events_path).exists():
                shutil.copyfile(events_path, backup)
        known = {(source['revision_id'], source.get('role', 'support')) for source in candidates}
        available_revisions = (public_revisions(journal, excluded=reserved_v3_revisions)
                               if reserved_v3_revisions else public_revisions(journal))
        resumed, reasons = pipeline._resume_compilations(journal, now, known,
            available_revisions)
        by_id = {(source['revision_id'], source.get('role', 'support')): source
                 for source in [*resumed, *candidates, *topic_sources] if is_public(source)
                 and (source['revision_id'], source.get('role', 'support')) not in reserved_topic_keys}
        # Rotate failed/deferred attempts behind untouched sources; a poison source
        # cannot monopolize every unattended batch.
        attempted = {}
        for path in (journal.root / 'automatic-attempts').glob('*.json'):
            saved = json.loads(path.read_text())
            reservation = saved['reservation']
            revision = reservation['revision_id']
            attempted[revision] = max(attempted.get(revision, ''), reservation['at'])
        for source in by_id.values():
            source['last_attempt_at'] = attempted.get(source['revision_id'], '')
        chosen = select_pending(by_id.values(), lambda source: pending_public_source(source, journal),
                                limit=args.source_limit)
        results = []
        for source in chosen:
            attempt = quota.reserve(budget_path, source['revision_id'])
            if attempt is None:
                break
            local = ({'context_mode': source['context_mode'], 'note_ids': source['note_ids']}
                     if source.get('context_mode') == 'source_note_snapshot' else {})
            result = process(source, list(relevant_events(source)), journal, attempt, **local)
            results.append({'revision_id': source['revision_id'], 'result': result})
            print(json.dumps(results[-1]), flush=True)
        summary = {'attempted': len(results), 'results': results, 'quota': quota.status(budget_path),
                   'discovery': discovery_stats,
                   'resume_unavailable': len(reasons) + len(topic_reasons), 'unrestricted_pipeline_enabled': pipeline._mode_enabled()}
        if topic_recovery:
            summary['topic_recovery'] = topic_recovery
        safe_io.atomic_write_json(str(journal.root / 'automatic-latest.json'), summary)
        print(json.dumps(summary), flush=True)
    recovery_pending = any(item.get('stage') != 'committed' or any(
        value != 'done' for value in item.get('effects', {}).values()) for item in topic_recovery)
    failed = any(item['result'].get(field, 0) for item in results for field in
                 ('compile_failed', 'routing_pending', 'effects_pending', 'stale_base', 'topic_failed'))
    return 2 if recovery_pending or failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
