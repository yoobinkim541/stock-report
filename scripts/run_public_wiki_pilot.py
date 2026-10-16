"""One approved five-source pilot; defaults to read-only source validation."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import re
import shutil

import safe_io
from agent_console import agent, shared_memory, wiki
from reports import raw_archive, source_collector, wiki_source_pipeline as pipeline, wiki_source_revision
from reports.wiki_source_journal import Journal

APPROVED_REVISIONS = (
    '38a0647521f39d932e255c91dc702c648bdb912a1f4060b6d1a3e746271b30a6',
    'd36f709dd03025e1066d9409857d70a00ea60d7af9653f4daa3b36bbed79db3a',
    '0194f091c31c9d84c02774f74f670f3bce97fb81bad911b555b135bf057ad847',
    '30f69905bbc1c2488e16a7d5cae472f8e80e013625d004c7a2e15f75270707fc',
    '3ce20c82442525f12fd7157a9528dfc82cebee8a72b2f6eafd9245b7b9650cc8',
)


def is_public(source):
    return (str(source.get('provider', '')).startswith('telegram:')
            and source.get('scope') == 'public_message'
            and re.fullmatch(r'https://t\.me/[A-Za-z][A-Za-z0-9_]{3,31}/[0-9]+',
                             str(source.get('canonical_url', ''))) is not None)


def select_sources(revisions):
    by_id = {source['revision_id']: source for source in revisions if is_public(source)}
    if any(identity not in by_id for identity in APPROVED_REVISIONS):
        raise ValueError('pilot_sources_unavailable')
    return [by_id[identity] for identity in APPROVED_REVISIONS]


def filter_targets(state, allowed):
    result = deepcopy(state)
    result['candidate_ids'] = [identity for identity in result['candidate_ids'] if identity in allowed]
    result['unexamined'] = result['candidate_ids'][:]
    return result


def public_target_ids(journal, allowed_revisions=None, *, records=None):
    allowed_revisions = set(APPROVED_REVISIONS) if allowed_revisions is None else set(allowed_revisions)
    allowed = set()
    rows = shared_memory.inspect_records() if records is None else records
    for path in (journal.root / 'operations').glob('*.json'):
        receipt = journal._read(path)
        if receipt['stage'] != 'committed' or receipt['revision_id'] not in allowed_revisions:
            continue
        batch = receipt['batch']
        for identity in batch['target_ids']:
            if wiki._source_fingerprint(identity, rows) == batch['expected_after'][identity]:
                allowed.add(identity)
    # Topic receipts are separate from legacy single-source operations. Their
    # source set and atomic store witness must both remain verifiable.
    for path in (journal.root / 'topic-operations').glob('*.json'):
        try:
            from reports.wiki_topic_context import _verified_source
            from reports.wiki_topic_transaction import _commit_record
            receipt = journal.load_topic_operation(path.stem)
            if not receipt or receipt['stage'] != 'committed':
                continue
            batch = receipt['batch']
            entries = list(batch['source_map'].values())
            revisions = {entry['revision_id'] for entry in entries}
            if not revisions or not revisions <= allowed_revisions:
                continue
            now = datetime.now(timezone.utc)
            for entry in entries:
                source, _, source_receipt = _verified_source(journal, entry['revision_id'], now)
                expected = batch['source_hashes'][entry['revision_id']]
                if (source['canonical_url'] != entry['canonical_url']
                        or source['raw_sha256'] != expected['raw_sha256']
                        or source['text_sha256'] != expected['text_sha256']
                        or source_receipt['compile_sha256'] != expected['compile_sha256']):
                    raise ValueError('public_topic_source_changed')
            witness = _commit_record(batch)
            if wiki._source_fingerprint(witness['id'], rows) != wiki._source_fingerprint(witness['id'], [witness]):
                continue
            for identity in batch['target_ids']:
                if wiki._source_fingerprint(identity, rows) == batch['expected_after'][identity]:
                    allowed.add(identity)
        except (ValueError, KeyError, TypeError, OSError, UnicodeError):
            continue
    return allowed


def validate_public_targets(targets, journal, allowed_revisions=None):
    rows = shared_memory.inspect_records()
    allowed = public_target_ids(journal, allowed_revisions, records=rows)
    def matches(supplied, current):
        if isinstance(supplied, dict):
            return isinstance(current, dict) and all(
                key in current and matches(value, current[key]) for key, value in supplied.items())
        if isinstance(supplied, list):
            return isinstance(current, list) and len(supplied) == len(current) and all(
                matches(left, right) for left, right in zip(supplied, current))
        return supplied == current
    for target in targets:
        if target['page_id'] not in allowed:
            raise ValueError('public_target_changed')
        current = wiki.semantic_snapshot(target['page_id'], records=rows)
        current.update(title=current['page']['title'], summary=current['page'].get('summary', ''))
        if target.get('revision_hash') != current['revision_hash'] or not matches(target, current):
            raise ValueError('public_target_changed')


def hydrate_public_target(identity, journal, allowed_revisions=None):
    snapshot = wiki.semantic_snapshot(identity)
    validate_public_targets([{'page_id': identity, 'revision_hash': snapshot['revision_hash']}],
                            journal, allowed_revisions)
    return snapshot


def verify_repeat(repeat, pages, source_states):
    return (bool(pages) and len(source_states) == 5
            and all(page and page.get('status') == 'draft' for page in pages)
            and all(receipt and receipt.get('compile_state') in {'compiled', 'skipped'} for receipt in source_states)
            and all(repeat.get(field) == 0 for field in
                    ('data_committed', 'compile_failed', 'routing_pending', 'effects_pending', 'stale_base')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    rows = [row for row in source_collector.load_recent_events(hours=72)
            if str(row.get('source', '')).startswith('telegram:')]
    discovery = wiki_source_revision.discover_revisions(rows, now=datetime.now(timezone.utc), allow_chunking=True)
    sources = select_sources(discovery['eligible'])
    selected_urls = {source['canonical_url'] for source in sources}
    events = [row for row in rows if row.get('url') in selected_urls]
    if not args.apply:
        print(json.dumps({'apply': False, 'verified_sources': len(sources),
                          'revisions': [source['revision_id'] for source in sources]}))
        return 0

    journal = Journal(raw_archive.reports_root() / 'public-wiki-pilot-20261013')
    journal.root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = journal.root / 'before-events.jsonl'
    with shared_memory._events_lock():
        if not backup.exists():
            shutil.copyfile(shared_memory._paths()['events'], backup)
    calls = []

    def llm(prompt):
        if prompt.startswith('Compile'):
            metadata = json.loads(prompt.split('\nBEGIN UNTRUSTED SOURCE TEXT\n', 1)[0].splitlines()[-1])
            identity = metadata['revision_id']
        else:
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            identity = payload['compiled']['revision_id']
            validate_public_targets(payload.get('candidates', payload.get('targets', [])), journal)
        if identity not in APPROVED_REVISIONS:
            raise ValueError('pilot_source_not_approved')
        calls.append({'phase': prompt.split(' ', 1)[0], 'revision_id': identity})
        safe_io.atomic_write_json(str(journal.root / 'model-calls' / f'{run_id}.json'), calls)
        print(json.dumps({'phase': calls[-1]['phase'], 'source': APPROVED_REVISIONS.index(identity) + 1,
                          'call': len(calls)}), flush=True)
        response = agent._try_codex_chat(
            'Use only the supplied public evidence. Do not use tools, read files, load memories, or browse. '
            'Return only the requested JSON object.\n' + prompt,
            max_timeout=180, search=False, reasoning_effort='medium', structured_output=True)
        safe_io.atomic_write_json(str(journal.root / 'public-model-responses' / run_id / f'{len(calls):03d}.json'),
                                  {'phase': calls[-1]['phase'], 'revision_id': identity, 'response': response})
        return response

    original_targets = pipeline.discover_targets
    pipeline.discover_targets = lambda compiled, **kwargs: filter_targets(
        original_targets(compiled, **kwargs), public_target_ids(journal))
    pipeline.hydrate_target = lambda identity: hydrate_public_target(identity, journal)
    pipeline.Journal = lambda: journal
    os.environ['AGENT_CONSOLE_QMD_ENABLED'] = '0'
    os.environ['AGENT_CONSOLE_CODEX_SEARCH'] = '0'
    first = pipeline.run(events=events, dry_run=False, llm_fn=llm, source_limit=5)
    print(json.dumps({'first': first, 'model_calls': len(calls)}), flush=True)
    if first['compile_failed'] or first['routing_pending'] or first['effects_pending'] or first['stale_base']:
        safe_io.atomic_write_json(str(journal.root / 'verification.json'), {'first': first, 'calls': calls, 'ok': False})
        return 2
    before_repeat = len(calls)
    repeat = pipeline.run(events=events, dry_run=False,
                          llm_fn=lambda _: (_ for _ in ()).throw(ValueError('pilot_repeat_model_call')), source_limit=5)
    source_states = [journal.load_source(source['revision_id']) for source in sources]
    allowed = public_target_ids(journal)
    pages = [wiki.get_page(identity) for identity in allowed]
    revalidated = wiki_source_revision.discover_revisions(events, now=datetime.now(timezone.utc), allow_chunking=True)
    select_sources(revalidated['eligible'])
    ok = verify_repeat(repeat, pages, source_states) and len(calls) == before_repeat
    result = {'ok': ok, 'first': first, 'repeat': repeat, 'model_calls': len(calls),
              'source_states': [receipt['compile_state'] for receipt in source_states],
              'public_page_ids': sorted(allowed), 'qmd_external_indexing': False,
              'persistent_pipeline_enabled': pipeline._mode_enabled()}
    safe_io.atomic_write_json(str(journal.root / 'verification.json'), result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
