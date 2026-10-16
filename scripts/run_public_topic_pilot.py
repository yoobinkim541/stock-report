"""One approved two-attempt pilot; default is archive-aware read-only preflight."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

import safe_io
from agent_console import agent, shared_memory, wiki
from reports import raw_archive, wiki_public_budget as quota
from reports.wiki_source_journal import Journal
from reports.wiki_topic_context import _note, _verified_source
from reports.wiki_topic_integration import propose_topic_integration
from reports.wiki_topic_transaction import prepare_topic_batch, recover_topic_operation, _commit_record
from scripts.run_public_wiki_rollout import validate_topic_prompt

APPROVED_REVISIONS = (
    '387f54cb405eb1f475a5a1f486450b29efbc38e1d4755614bd416771b40f63d6',
    '5fdda9590f68d65704ca12f8f0a5e3e343370bb61b600e725c3ec10606d9563e',
    'eabbe0e3a9384920dd147f1e87e4496c6d56b3b4744b39dafe4c5dd05916c3a9',
)


def _inspect(journal, revisions, now):
    if len(revisions) != 3 or len(set(revisions)) != 3:
        raise ValueError('pilot_revisions_invalid')
    rows = shared_memory.inspect_records()
    notes, dependencies, sources, compiled = [], [], [], []
    for revision in revisions:
        candidates = set()
        for path in (journal.root / 'operations').glob('*.json'):
            receipt = journal._read(path)
            if receipt['stage'] == 'committed' and receipt['revision_id'] == revision:
                candidates.update(receipt['batch']['target_ids'])
        selected = None
        for page_id in sorted(candidates):
            try:
                selected = _note(page_id, revision, journal=journal, records=rows, now=now, surface='market')
                break
            except (ValueError, KeyError, TypeError):
                continue
        if selected is None:
            raise ValueError('pilot_source_note_unavailable')
        note, dependency = selected
        source, compilation, receipt = _verified_source(journal, revision, now)
        source.update(role='support', observed_evidence_ids=receipt['evidence_by_role']['support'])
        notes.append(note)
        dependencies.append(dependency)
        sources.append(source)
        compiled.append(compilation)
    return rows, notes, dependencies, sources, compiled


def _load(path, revisions):
    if not path.exists():
        return {'schema_version': 1, 'revisions': list(revisions), 'attempts': [],
                'topic_ids': [], 'baseline': None}
    state = json.loads(path.read_text())
    if (not isinstance(state, dict) or set(state) != {
            'schema_version', 'revisions', 'attempts', 'topic_ids', 'baseline'}
            or state.get('schema_version') != 1 or state.get('revisions') != list(revisions)
            or type(state.get('attempts')) is not list or len(state['attempts']) > 2):
        raise ValueError('pilot_state_invalid')
    if (not isinstance(state['topic_ids'], list)
            or len(state['topic_ids']) > 1
            or any(not isinstance(p, str) or not p for p in state['topic_ids'])
            or (state['baseline'] is not None and not isinstance(state['baseline'], list))):
        raise ValueError('pilot_state_invalid')
    for index, attempt in enumerate(state['attempts']):
        if (not isinstance(attempt, dict)
                or not {'stage', 'status', 'revision_id', 'reservation'} <= set(attempt)
                or set(attempt) - {'stage', 'status', 'revision_id', 'reservation', 'operation_id'}
                or attempt['stage'] != ('seed' if index == 0 else 'followup')
                or attempt['revision_id'] != revisions[index + 1]
                or attempt['status'] not in {'interrupted', 'failed', 'refused', 'committed'}
                or (index and state['attempts'][index - 1]['status'] != 'committed')):
            raise ValueError('pilot_state_invalid')
        if attempt['status'] == 'committed' and (
                not attempt.get('operation_id') or not attempt['reservation']
                or not state['baseline'] or not state['topic_ids']):
            raise ValueError('pilot_state_invalid')
    return state


def _verify_commits(state, journal, rows, budget_path):
    reservations = [r for day in quota._read(budget_path)['days'].values() for r in day]
    for attempt in state['attempts']:
        reservation = attempt['reservation']
        if reservation is not None and (reservation not in reservations
                or reservation['revision_id'] != attempt['revision_id']):
            raise ValueError('pilot_reservation_unverified')
        if attempt['status'] != 'committed':
            continue
        receipt = journal.load_topic_operation(attempt['operation_id'])
        if (not receipt or receipt['stage'] != 'committed'
                or receipt['batch']['proposal']['revision_id'] != attempt['revision_id']
                or receipt['batch']['proposal']['role'] != 'support'
                or any(receipt[k] != 'done' for k in ('artifacts', 'search', 'log'))
                or receipt['batch']['target_ids'] != state['topic_ids']
                or _commit_record(receipt['batch']) not in rows):
            raise ValueError('pilot_commit_unverified')
    if state['attempts'] and state['attempts'][-1]['status'] == 'committed':
        batch = journal.load_topic_operation(state['attempts'][-1]['operation_id'])['batch']
        if any(wiki._source_fingerprint(pid, rows) != digest
               for pid, digest in batch['expected_after'].items()):
            raise ValueError('pilot_topic_changed')


def run(journal, *, revisions=APPROVED_REVISIONS, now=None, apply=False, llm_fn=None):
    clock = lambda: now or datetime.now(timezone.utc)
    state_path = journal.root / 'topic-pilot.json'
    budget_path = journal.root / 'automatic-budget.json'

    def execute():
        state = _load(state_path, revisions)
        rows, notes, dependencies, sources, compiled = _inspect(journal, revisions, clock())
        _verify_commits(state, journal, rows, budget_path)
        budget = quota.status(budget_path, now)
        result = {'status': 'ready', 'attempts_used': len(state['attempts']), 'quota': budget}
        if state['baseline'] is not None and state['baseline'] != dependencies:
            raise ValueError('pilot_originals_changed')
        if state['attempts'] and state['attempts'][-1]['status'] != 'committed':
            result['status'] = state['attempts'][-1]['status']
            return result
        if len(state['attempts']) == 2:
            result.update(status='awaiting_semantic_review', topic_ids=state['topic_ids'])
            return result
        if budget['count'] >= budget['limit']:
            result['status'] = 'quota_exhausted'
            return result
        if not apply:
            return result
        if llm_fn is None:
            raise ValueError('pilot_backend_required')
        while len(state['attempts']) < 2:
            index = len(state['attempts'])
            rows, notes, dependencies, sources, compiled = _inspect(journal, revisions, clock())
            if state['baseline'] is not None and state['baseline'] != dependencies:
                raise ValueError('pilot_originals_changed')
            source = sources[index + 1]
            # A durable intent counts against the local two-attempt cap even if
            # interrupted between the shared reservation and its receipt write.
            attempt = {'stage': 'seed' if index == 0 else 'followup', 'status': 'interrupted',
                       'revision_id': source['revision_id'], 'reservation': None}
            if quota.status(budget_path, now)['count'] >= 20:
                break
            state['baseline'] = dependencies
            state['attempts'].append(attempt)
            safe_io.atomic_write_json(str(state_path), state)
            reservation = quota.reserve(budget_path, source['revision_id'], now)
            if reservation is None:
                state['attempts'].pop()
                safe_io.atomic_write_json(str(state_path), state)
                break
            attempt['reservation'] = reservation
            safe_io.atomic_write_json(str(state_path), state)
            try:
                context = {'notes': notes[:index + 1], 'dependencies': dependencies[:index + 1], 'omitted': []}
                targets = [wiki.semantic_snapshot(identity, records=rows) for identity in state['topic_ids']]
                def checked_model(prompt):
                    envelope = json.loads(prompt.rsplit('\n', 1)[1])
                    validate_topic_prompt(envelope, journal=journal,
                        reserved_revision_id=source['revision_id'], now=clock())
                    return llm_fn(prompt)
                proposal = propose_topic_integration(compiled[index + 1], source, context, targets,
                    llm_fn=checked_model, journal=journal, records=rows, now=clock())
                if proposal['refusal']:
                    attempt['status'] = 'refused'
                else:
                    proposed = proposal['targets']
                    if (len(proposed) != 1 or (index == 1 and [p['page_id'] for p in proposed] != state['topic_ids'])
                            or set(proposed[0]['contributing_revision_ids']) - set(revisions[:index + 2])):
                        raise ValueError('pilot_scope_changed')
                    batch = prepare_topic_batch(proposal, journal=journal, records=rows, now=clock())
                    attempt['operation_id'] = batch['operation_id']
                    safe_io.atomic_write_json(str(state_path), state)
                    outcome = recover_topic_operation(batch['operation_id'], journal=journal, now=clock())
                    if outcome['stage'] != 'committed' or any(v != 'done' for v in outcome['effects'].values()):
                        raise ValueError('pilot_commit_incomplete')
                    state['topic_ids'] = batch['target_ids']
                    for identity in state['topic_ids']:
                        snapshot = wiki.semantic_snapshot(identity)
                        if ({c['source_revision_id'] for c in snapshot['claims']} != set(revisions[:index + 2])
                                or snapshot['page']['status'] != 'draft'):
                            raise ValueError('pilot_acceptance_failed')
                    if _inspect(journal, revisions, clock())[2] != state['baseline']:
                        raise ValueError('pilot_originals_changed')
                    attempt['status'] = 'committed'
            except Exception:
                attempt['status'] = 'failed'
            safe_io.atomic_write_json(str(state_path), state)
            if attempt['status'] != 'committed':
                return {'status': attempt['status'], 'attempts_used': len(state['attempts']),
                        'quota': quota.status(budget_path, now)}
        _verify_commits(state, journal, shared_memory.inspect_records(), budget_path)
        return {'status': 'awaiting_semantic_review' if len(state['attempts']) == 2 else 'quota_exhausted',
                'attempts_used': len(state['attempts']), 'topic_ids': state['topic_ids'],
                'quota': quota.status(budget_path, now)}

    if apply:
        with safe_io.file_write_lock(str(journal.root / 'automatic-run')):
            return execute()
    return execute()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    journal = Journal(raw_archive.reports_root() / 'public-wiki-pilot-20261013')
    def model(prompt):
        return agent._try_codex_chat('Use only supplied public evidence; no tools, browsing or memories. '
            'Return requested JSON only.\n' + prompt, search=False, max_timeout=90,
            reasoning_effort='medium', structured_output=True)
    result = run(journal, apply=args.apply, llm_fn=model if args.apply else None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in {'ready', 'awaiting_semantic_review', 'quota_exhausted'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
