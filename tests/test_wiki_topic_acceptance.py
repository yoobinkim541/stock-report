"""Full reserved runner with injected JSON provider and temporary real stores."""
import copy
import json

from agent_console import shared_memory, wiki
from scripts import run_public_wiki_rollout as runner
from test_wiki_topic_transaction import transaction, evidence


def test_failed_topic_can_resume_without_recent_feed_events(transaction, monkeypatch):
    data, journal, _ = transaction
    monkeypatch.setenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '1')
    monkeypatch.setattr(runner.pipeline, 'run', lambda **kw: {'data_committed': 1})
    monkeypatch.setattr(runner.pipeline, '_pending_source', lambda *args: False)
    def fail(*args, **kwargs):
        raise ValueError('synthetic topic failure')
    monkeypatch.setattr(runner.pipeline, 'run_public_topic', fail)
    source = data['source']
    runner.process(source, [], journal, {'id': 'failed', 'revision_id': source['revision_id']})
    from test_wiki_topic_transaction import NOW
    resumed, reasons = runner.resume_topic_sources(journal, NOW)
    assert reasons == []
    assert [s['revision_id'] for s in resumed] == [source['revision_id']]
    selected = runner.select_pending(resumed, lambda s: runner.pending_public_source(s, journal))
    assert len(selected) == 1


def test_runner_seeds_then_updates_same_topic_and_repeat_is_noop(transaction, monkeypatch):
    data, journal, originals = transaction
    monkeypatch.setenv('AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED', '1')
    calls = []

    def provider(prompt, **kwargs):
        calls.append(prompt)
        envelope = json.loads(prompt.rsplit('\n', 1)[1])
        if 'Route source knowledge' in prompt:
            return json.dumps({'selected': [], 'rejected': {
                c['page_id']: 'Separate topic stage' for c in envelope['candidates']}, 'new_page_specs': []})
        assert 'Propose append-only draft topic synthesis.' in prompt
        current = envelope['compiled']
        targets = envelope['editable_topics']
        target = targets[0] if targets else None
        note = envelope['read_only_source_context']['notes'][0]
        units = [(current['revision_id'], current['claims'][0])]
        if target is None:
            units.append((note['revision_id'], note['claims'][0]))
        labels = {v['revision_id']: k for k, v in envelope['source_map'].items()}
        result = {'page_id': target['page_id'] if target else None,
            'before_hash': target['revision_hash'] if target else None,
            'contributing_revision_ids': [r for r, _ in units],
            'scope_explanation': 'Dated public accounts; uncertainty preserved',
            'additions': [{'claim_ids': [c['claim_id']], 'revision_ids': [r],
                'as_of': c['as_of'], 'scope': c['scope'],
                'body': f"Attributed account [{labels[r]}]."} for r, c in units], 'relations': []}
        if target is None:
            result.update(title='Hormuz shipping', kind='concept', surface='market', purpose='topic_synthesis')
        return json.dumps({'targets': [result], 'refusal': None})

    monkeypatch.setattr(runner.agent, '_try_codex_chat', provider)
    source = data['source']
    first = runner.process(source, [], journal, {'id': 'first', 'revision_id': source['revision_id']})
    assert first['topic']['stage'] == 'committed', first
    topic = next(r for r in shared_memory.inspect_records()
                 if r['id'].startswith('source-topic-'))
    first_snapshot = wiki.semantic_snapshot(topic['id'])
    first_revisions = {c['source_revision_id'] for c in first_snapshot['claims']}
    assert len(first_revisions) == 2
    # Pick the third original: it already has a source note, but has never been
    # included in this topic. Re-running the real legacy worker is not required
    # to manufacture a new original or reset its compilation receipt.
    remaining = next(journal.load_source(p.stem) for p in (journal.root / 'sources').glob('*.json')
                     if p.stem not in first_revisions)
    later = copy.deepcopy(remaining['source'])
    from reports.wiki_topic_context import _verified_source
    later = _verified_source(journal, later['revision_id'], runner.datetime.now(runner.timezone.utc))[0]
    later['role'] = 'support'
    second = runner.process(later, [], journal, {'id': 'second', 'revision_id': later['revision_id']})
    assert second['topic']['stage'] == 'committed', second
    final = wiki.semantic_snapshot(topic['id'])
    assert len({c['source_revision_id'] for c in final['claims']}) == 3
    assert final['page']['status'] == 'draft'
    assert {'Uncertainty 0', 'Uncertainty 1', 'Uncertainty 2'} <= set(final['source_knowledge']['limitations'])
    assert final['body'].startswith(first_snapshot['body'])
    assert set(c['claim_id'] for c in first_snapshot['claims']) <= set(c['claim_id'] for c in final['claims'])
    from agent_console import qmd_search
    markdown = qmd_search._page_markdown(wiki.get_page(topic['id']), topic['id'])
    for index in (1, 2, 3):
        assert f'https://t.me/public_channel/{index}' in markdown
    rows = shared_memory.inspect_records()
    assert [r for r in rows if r['id'] in {o['id'] for o in originals}] == originals
    count = len(calls)
    again = runner.process(later, [], journal, {'id': 'repeat', 'revision_id': later['revision_id']})
    assert again['topic']['stage'] == 'committed'
    assert len(calls) == count
    assert shared_memory.inspect_records() == rows
