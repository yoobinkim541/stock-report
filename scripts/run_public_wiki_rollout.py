"""Approved bounded public-only wiki operation; without --apply this is read-only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import shutil

import safe_io
from agent_console import agent
from reports import raw_archive, source_collector, wiki_public_budget as quota, wiki_source_revision
from reports import wiki_source_pipeline as pipeline
from reports.wiki_source_journal import Journal
from scripts.run_public_wiki_pilot import is_public, filter_targets, public_target_ids, validate_public_targets, hydrate_public_target


def select_pending(sources, pending):
    return sorted([source for source in sources if is_public(source) and pending(source)],
                  key=lambda source: (source.get('published_at') or source.get('collected_at') or '',
                                      source['revision_id']))[:5]


def plan_status(path, *, candidates):
    budget = quota.status(path)
    return {'apply': False, 'pending_public_sources': candidates,
            'planned_attempts': min(5, candidates, budget['limit'] - budget['count']), 'quota': budget}


def public_revisions(journal):
    return {path.stem for path in (journal.root / 'sources').glob('*.json')
            if is_public(journal.load_source(path.stem)['source'])}


def _topic_stage_path(source, journal):
    identity, role = source['revision_id'], source.get('role', 'support')
    if not re.fullmatch(r'[a-f0-9]{64}', identity) or role not in {'support', 'conflict'}:
        raise ValueError('topic_stage_identity_invalid')
    return journal.root / 'topic-stages' / f'{identity}-{role}.json'


def _read_topic_stage(path):
    data = json.loads(path.read_text())
    if (type(data) is not dict or set(data) != {'revision_id', 'role', 'state'}
            or data['state'] not in {'pending', 'failed', 'done'}
            or data['role'] not in {'support', 'conflict'}
            or path.name != f"{data['revision_id']}-{data['role']}.json"):
        raise ValueError('topic_stage_corrupt')
    return data


def pending_public_source(source, journal):
    if pipeline._pending_source(source, journal):
        return True
    if os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') != '1':
        return False
    path = _topic_stage_path(source, journal)
    return path.exists() and _read_topic_stage(path)['state'] in {'pending', 'failed'}


def resume_topic_sources(journal, now):
    if os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') != '1':
        return [], []
    from reports.wiki_topic_context import _verified_source
    sources, reasons = [], []
    for path in sorted((journal.root / 'topic-stages').glob('*.json')):
        stage = _read_topic_stage(path)
        if stage['state'] == 'done':
            continue
        try:
            source, _, receipt = _verified_source(journal, stage['revision_id'], now)
            evidence = receipt['evidence_by_role'][stage['role']]
            sources.append({**source, 'role': stage['role'], 'observed_evidence_ids': evidence})
        except (ValueError, KeyError, TypeError, OSError, UnicodeError):
            reasons.append('topic_resume_unavailable')
    return sources, reasons


def validate_topic_prompt(data, *, journal, reserved_revision_id, now):
    """Bind the final provider envelope to current public archive/store evidence."""
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


def process(source, events, journal, attempt):
    if not is_public(source):
        raise ValueError('private_source_disallowed')
    identity = source['revision_id']
    if attempt.get('revision_id') != identity:
        raise ValueError('unreserved_source')
    topic_enabled = os.getenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '0') == '1'
    if topic_enabled:
        stage_path = _topic_stage_path(source, journal)
        stage = {'revision_id': identity, 'role': source.get('role', 'support'), 'state': 'pending'}
        safe_io.atomic_write_json(str(stage_path), stage)
    calls = []
    original_targets, original_journal = pipeline.discover_targets, pipeline.Journal
    original_hydrate = pipeline.hydrate_target
    original_qmd = os.environ.get('AGENT_CONSOLE_QMD_ENABLED')
    def allowed_targets():
        return public_target_ids(journal, public_revisions(journal))
    def llm(prompt):
        if prompt.startswith('Compile'):
            data = json.loads(prompt.split('\nBEGIN UNTRUSTED SOURCE TEXT\n', 1)[0].splitlines()[-1])
            parent = data.get('chunk_parent_revision_id') or data['revision_id']
        elif prompt.startswith('Propose append-only draft topic synthesis.'):
            data = json.loads(prompt.rsplit('\n', 1)[1])
            parent = validate_topic_prompt(data, journal=journal,
                                           reserved_revision_id=identity, now=datetime.now(timezone.utc))
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
        return agent._try_codex_chat(
            'Use only supplied public evidence. Do not use tools, read files, browse or load memories. '
            'Return only the requested JSON.\n' + prompt, search=False, max_timeout=90,
            reasoning_effort='medium', structured_output=True)
    try:
        pipeline.discover_targets = lambda compiled, **kwargs: filter_targets(
            original_targets(compiled, **kwargs), allowed_targets())
        pipeline.Journal = lambda: journal
        pipeline.hydrate_target = lambda identity: hydrate_public_target(identity, journal, public_revisions(journal))
        os.environ['AGENT_CONSOLE_QMD_ENABLED'] = '0'
        result = pipeline.run(events=events, dry_run=False, llm_fn=llm, source_limit=1,
                              allowed_revision_ids={identity})
        if (topic_enabled
                and not any(result.get(field, 0) for field in
                            ('compile_failed', 'routing_pending', 'effects_pending', 'stale_base'))):
            try:
                topic = pipeline.run_public_topic(source, journal=journal, llm_fn=llm,
                    reserved_revision_id=identity, now=datetime.now(timezone.utc))
                result['topic'] = topic
                if (topic.get('stage') in {'stale-base', 'failed'}
                        or any(value != 'done' for value in topic.get('effects', {}).values())):
                    result['topic_failed'] = 1
            except Exception:
                result['topic'] = {'enabled': True, 'stage': 'failed'}
                result['topic_failed'] = 1
            stage['state'] = 'failed' if result.get('topic_failed') else 'done'
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and pipeline._mode_enabled():
        raise ValueError('unrestricted_pipeline_enabled')
    if args.apply and shutil.which('codex') is None:
        raise ValueError('codex_executable_unavailable')
    journal = Journal(raw_archive.reports_root() / 'public-wiki-pilot-20261013')
    budget_path = journal.root / 'automatic-budget.json'
    events = [event for event in source_collector.load_recent_events(hours=72)
              if str(event.get('source', '')).startswith('telegram:')]
    now = datetime.now(timezone.utc)
    discovered = wiki_source_revision.discover_revisions(events, now=now, allow_chunking=True)
    candidates = [source for source in discovered['eligible'] if is_public(source)]
    topic_sources, topic_reasons = resume_topic_sources(journal, now)
    if not args.apply:
        available = {(s['revision_id'], s.get('role', 'support')): s for s in [*candidates, *topic_sources]}
        pending = sum(pending_public_source(source, journal) for source in available.values())
        print(json.dumps(plan_status(budget_path, candidates=pending)))
        return 0
    with safe_io.file_write_lock(str(journal.root / 'automatic-run')):
        known = {(source['revision_id'], source['role']) for source in candidates}
        resumed, reasons = pipeline._resume_compilations(journal, now, known, public_revisions(journal))
        by_id = {(source['revision_id'], source.get('role', 'support')): source
                 for source in [*resumed, *candidates, *topic_sources] if is_public(source)}
        chosen = select_pending(by_id.values(), lambda source: pending_public_source(source, journal))
        results = []
        for source in chosen:
            attempt = quota.reserve(budget_path, source['revision_id'])
            if attempt is None:
                break
            relevant = [event for event in events if event.get('url') == source['canonical_url']]
            result = process(source, relevant, journal, attempt)
            results.append({'revision_id': source['revision_id'], 'result': result})
            print(json.dumps(results[-1]), flush=True)
        summary = {'attempted': len(results), 'results': results, 'quota': quota.status(budget_path),
                   'resume_unavailable': len(reasons) + len(topic_reasons), 'unrestricted_pipeline_enabled': pipeline._mode_enabled()}
        safe_io.atomic_write_json(str(journal.root / 'automatic-latest.json'), summary)
        print(json.dumps(summary), flush=True)
    return 2 if any(item['result'].get(field, 0) for item in results for field in
                    ('compile_failed', 'routing_pending', 'effects_pending', 'stale_base', 'topic_failed')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
