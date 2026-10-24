"""One retained, two-attempt pilot. Missing authorization never initializes it."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import uuid

from agent_console import agent, shared_memory, wiki
from reports import raw_archive, wiki_public_budget as quota
from reports import wiki_topic_pilot_state as pilot
from reports.wiki_source_journal import Journal
from reports.wiki_source_notes import pin_source_note
from reports.wiki_topic_context import _note, _verified_source
from reports.wiki_topic_integration import propose_topic_integration
from reports.wiki_topic_transaction import prepare_topic_batch, recover_topic_operation
from reports.wiki_topic_snapshot_context import select_snapshot_context
from reports.wiki_topic_v3 import propose_topic_integration_v3, strict_topic_json, snapshot_topic_prompt
from reports.wiki_topic_transaction_v3 import prepare_topic_batch_v3, recover_topic_operation_v3
from scripts.run_public_wiki_rollout import validate_topic_prompt

APPROVED_REVISIONS = (
    '387f54cb405eb1f475a5a1f486450b29efbc38e1d4755614bd416771b40f63d6',
    '5fdda9590f68d65704ca12f8f0a5e3e343370bb61b600e725c3ec10606d9563e',
    'eabbe0e3a9384920dd147f1e87e4496c6d56b3b4744b39dafe4c5dd05916c3a9',
)


def _inspect(journal, revisions, now):
    rows = shared_memory.inspect_records()
    notes, dependencies, sources, compiled = [], [], [], []
    for revision in revisions:
        candidates = set()
        for name in pilot._list(journal, 'operations'):
            if not name.endswith('.json'):
                continue
            receipt = pilot._object(pilot._read(journal, 'operations/' + name))
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


def _snapshot_inputs(journal, state, now):
    sources, compiled, dependencies = [], [], []
    from reports.wiki_topic_snapshot_context import _candidate
    for revision in APPROVED_REVISIONS:
        pin = pin_source_note(state['note_ids'][revision], journal=journal, now=now)
        if pin['revision_id'] != revision:
            raise ValueError('pilot_note_revision_changed')
        dependencies.append(_candidate(pin, 'market')[1])
        source, compilation, receipt = _verified_source(journal, revision, now)
        source.update(role='support', observed_evidence_ids=receipt['evidence_by_role']['support'])
        sources.append(source)
        compiled.append(compilation)
    return shared_memory.inspect_records(), dependencies, sources, compiled


def _budget(path, now):
    status = quota.status(path, now)
    status['limit'] = min(20, status['limit'])
    return status


def run(journal, *, revisions=None, now=None, apply=False, llm_fn=None):
    """Injected provider/time are for synthetic callers; CLI has no overrides.

    Both modes share the exact same state, authorization, intents and budget.
    Model refusal, failure and process interruption are terminal for this pilot.
    """
    clock = lambda: now if now is not None else datetime.now(timezone.utc)
    budget_path = journal.root / 'automatic-budget.json'
    try:
        if revisions is not None and tuple(revisions) != APPROVED_REVISIONS:
            raise ValueError('pilot_revisions_invalid')
        with pilot.pilot_locks(journal):
            state, guard, _ = pilot._load_locked(journal)
            budget = _budget(budget_path, clock())
            report = pilot._report(state, guard)
            report.update(quota=budget, topic_ids=state['topic_ids'])
            if report['status'] != 'ready':
                return report
            snapshot = state['schema_version'] == 2

            def inputs():
                if snapshot:
                    rows, dependencies, sources, compiled = _snapshot_inputs(journal, state, clock())
                    notes = None
                else:
                    rows, notes, dependencies, sources, compiled = _inspect(journal, APPROVED_REVISIONS, clock())
                if state['baseline'] is not None and not pilot._same(state['baseline'], dependencies):
                    raise ValueError('pilot_originals_changed')
                return rows, notes, dependencies, sources, compiled

            inputs()
            if budget['count'] >= budget['limit']:
                report['status'] = 'quota_exhausted'
                return report
            if not apply:
                return report
            if llm_fn is None:
                raise ValueError('pilot_backend_required')
            while len(state['attempts']) < 2:
                index = len(state['attempts'])
                budget = _budget(budget_path, clock())
                if budget['count'] >= budget['limit']:
                    break
                rows, notes, dependencies, sources, compiled = inputs()
                source = sources[index + 1]
                if snapshot:
                    context = select_snapshot_context(compiled[index + 1], journal=journal, now=clock(),
                        note_ids=[state['note_ids'][r] for r in APPROVED_REVISIONS[:index + 1]], surface='market')
                    if (context['omitted'] or {n['revision_id'] for n in context['notes']}
                            != set(APPROVED_REVISIONS[:index + 1])):
                        raise ValueError('pilot_context_incomplete')
                else:
                    context = {'notes': notes[:index + 1], 'dependencies': dependencies[:index + 1], 'omitted': []}
                targets = [wiki.semantic_snapshot(identity, records=rows) for identity in state['topic_ids']]
                attempt = {'stage': 'seed' if index == 0 else 'followup', 'status': 'interrupted',
                    'revision_id': source['revision_id'], 'reservation': None,
                    'intent_id': pilot._sha(uuid.uuid4().bytes)}
                state['baseline'] = dependencies
                state['attempts'].append(attempt)
                pilot.persist_pilot_state(journal, state, guard)
                # An intent survives a crash inside reserve or before its ID
                # reaches the state. Reconciliation then stops for review.
                reservation = quota.reserve(budget_path, source['revision_id'], clock(), limit=budget['limit'])
                if reservation is None:
                    return {'status': 'interrupted', 'attempts_used': len(state['attempts']),
                            'quota': _budget(budget_path, clock())}
                attempt['reservation'] = reservation
                pilot.persist_pilot_state(journal, state, guard)
                try:
                    def checked_model(prompt, *, context_mode='legacy_pages', pinned_context=None):
                        envelope = strict_topic_json(prompt.rsplit('\n', 1)[1])
                        if snapshot and prompt != snapshot_topic_prompt(envelope):
                            raise ValueError('pilot_prompt_changed')
                        validate_topic_prompt(envelope, journal=journal, reserved_revision_id=source['revision_id'],
                            now=clock(), context_mode=context_mode, pinned_context=pinned_context)
                        inputs()
                        return llm_fn(prompt)
                    propose = propose_topic_integration_v3 if snapshot else propose_topic_integration
                    prepare = prepare_topic_batch_v3 if snapshot else prepare_topic_batch
                    recover = recover_topic_operation_v3 if snapshot else recover_topic_operation
                    proposal = propose(compiled[index + 1], source, context, targets, llm_fn=checked_model,
                        journal=journal, records=rows, now=clock())
                    if proposal['refusal']:
                        attempt['status'] = 'refused'
                    else:
                        proposed = proposal['targets']
                        contributors = set(APPROVED_REVISIONS[:index + 2])
                        if (len(proposed) != 1
                                or (index == 0 and proposed[0]['before_hash'] is not None)
                                or (index == 1 and [p['page_id'] for p in proposed] != state['topic_ids'])
                                or set(proposed[0]['contributing_revision_ids']) - contributors
                                or (index == 0 and set(proposed[0]['contributing_revision_ids']) != contributors)):
                            raise ValueError('pilot_scope_changed')
                        inputs()
                        batch = prepare(proposal, journal=journal, records=rows, now=clock())
                        attempt.update(operation_id=batch['operation_id'], protocol_version=3 if snapshot else 2)
                        pilot.persist_pilot_state(journal, state, guard)
                        outcome = recover(batch['operation_id'], journal=journal, now=clock())
                        if outcome['stage'] != 'committed' or any(v != 'done' for v in outcome['effects'].values()):
                            raise ValueError('pilot_commit_incomplete')
                        state['topic_ids'] = batch['target_ids']
                        for identity in state['topic_ids']:
                            topic = wiki.semantic_snapshot(identity)
                            if ({c['source_revision_id'] for c in topic['claims']} != contributors
                                    or topic['page']['status'] != 'draft'):
                                raise ValueError('pilot_acceptance_failed')
                        inputs()
                        attempt['status'] = 'committed'
                except Exception:
                    attempt['status'] = 'failed'
                pilot.persist_pilot_state(journal, state, guard)
                if attempt['status'] != 'committed':
                    return {'status': attempt['status'], 'attempts_used': len(state['attempts']),
                            'quota': _budget(budget_path, clock())}
            state, guard, _ = pilot._load_locked(journal)
            report = pilot._report(state, guard)
            if report['status'] == 'ready':
                report['status'] = 'quota_exhausted'
            report.update(topic_ids=state['topic_ids'], quota=_budget(budget_path, clock()))
            return report
    except pilot._HISTORY_ERRORS as exc:
        return pilot._review(exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    journal = Journal(raw_archive.reports_root() / 'public-wiki-pilot-20261013')
    def model(prompt):
        return agent._try_codex_chat(prompt, search=False, max_timeout=90,
            reasoning_effort='medium', structured_output=True)
    result = run(journal, apply=args.apply, llm_fn=model if args.apply else None)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in {'ready', 'awaiting_semantic_review', 'quota_exhausted'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
