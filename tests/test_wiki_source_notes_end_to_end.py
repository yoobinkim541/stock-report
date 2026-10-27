"""Synthetic integration acceptance; no live sources or provider calls."""
import json
from pathlib import Path

import pytest

from agent_console import shared_memory, wiki, wiki_publication
from reports.wiki_source_notes import materialize_source_note, pin_source_note
from reports.wiki_source_note_viewer import frozen_source_note_citation
from reports.wiki_topic_snapshot_context import select_snapshot_context
from reports.wiki_topic_v3 import propose_topic_integration_v3
from reports import wiki_topic_transaction_v3 as transaction
from test_wiki_source_notes import NOW, encoded
from test_wiki_topic_v3 import payload, propose


@pytest.fixture
def evidence(source_note_evidence):
    return source_note_evidence


@pytest.mark.parametrize("stage", ["prepared", "committed"])
def test_actual_store_witness_rejection_preserves_bytes(evidence, store, tmp_path, monkeypatch, stage):
    _, journal, rows = evidence
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(rows)
    proposal = propose(evidence)
    for entry in proposal["source_map"].values():
        materialize_source_note(entry["revision_id"], journal=journal, now=NOW, apply=True)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=rows, now=NOW)
    transition = journal.transition_topic_v3

    def lose_process(operation_id, **kwargs):
        if kwargs.get("stage") == "committed":
            raise RuntimeError("synthetic loss before receipt transition")
        return transition(operation_id, **kwargs)

    if stage == "prepared":
        monkeypatch.setattr(journal, "transition_topic_v3", lose_process)
        with pytest.raises(RuntimeError, match="synthetic loss"):
            transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=NOW)
        monkeypatch.setattr(journal, "transition_topic_v3", transition)
    else:
        result = transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=NOW)
        assert result["newly_committed"]

    assert journal.load_topic_operation_v3(batch["operation_id"])["stage"] == stage
    events = shared_memory._paths()["events"]
    with shared_memory._events_lock():
        actual = shared_memory._read_jsonl_checked(events)
        witness = next(row for row in actual if row["id"] == transaction.topic_commit_record_v3(batch)["id"])
        witness["topic_protocol_version"] = 3.0
        shared_memory._write_jsonl_locked(actual)
    before_events = Path(events).read_bytes()
    receipt_path = journal._path("topic-operations-v3", batch["operation_id"])
    before_receipt = receipt_path.read_bytes()
    monkeypatch.setattr(journal, "load_source", lambda *_: pytest.fail("historical recovery read mutable source"))
    with pytest.raises(ValueError, match="topic_commit_witness_mismatch"):
        transaction.recover_topic_operation_v3(batch["operation_id"], journal=journal, now=NOW)
    assert Path(events).read_bytes() == before_events
    assert receipt_path.read_bytes() == before_receipt


@pytest.mark.parametrize('force_split', [False, True])
def test_two_source_seed_then_third_source_update_exports_frozen_citations(
        store, tmp_path, monkeypatch, force_split):
    from reports import wiki_publication_effects, wiki_public_budget
    from scripts.run_public_wiki_pilot import public_target_ids
    from scripts.run_public_wiki_rollout import validate_topic_prompt

    monkeypatch.setenv('AGENT_CONSOLE_SHARED_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_ARTIFACTS_DIR', str(tmp_path / 'wiki'))
    monkeypatch.setenv('AGENT_CONSOLE_QMD_ENABLED', '0')
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', '1')
    monkeypatch.setattr(shared_memory, '_schedule_context_memory_summary', lambda: None)
    root = tmp_path / 'publication'
    monkeypatch.setattr(wiki_publication_effects, 'publication_root', lambda: root)
    sources = [store(url=f'https://t.me/public_channel/{i}', body=f'Capacity report {i}')
               for i in range(1, 4)]
    journal = sources[0][0]
    immutable = {name: Path(name).read_bytes()
                 for _, source, _ in sources
                 for name in (source['raw_ref'], source['text_ref'], source['manifest_ref'])}
    reservation_calls = []
    reserve = wiki_public_budget.reserve

    def reserve_current(path, revision_id, now=None):
        reservation_calls.append(revision_id)
        return reserve(path, revision_id, now)

    monkeypatch.setattr(wiki_public_budget, 'reserve', reserve_current)
    pins = []
    for _, source, _ in sources:
        note = materialize_source_note(source['revision_id'], journal=journal, now=NOW, apply=True)
        pins.append(pin_source_note(note['note_id'], journal=journal, now=NOW))
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked([])
    monkeypatch.setattr(shared_memory, 'inspect_records', lambda: transaction._rows())
    provider_calls = []

    def commit(index, target=None):
        _, source, compiled = sources[index]
        assert wiki_public_budget.reserve(journal.root / 'automatic-budget.json',
                                          source['revision_id'], NOW) is not None
        records = transaction._rows()
        data = {'source': source, 'compiled': compiled,
                'context': select_snapshot_context(compiled,
                    note_ids=[pin['note_id'] for pin in pins[:index]],
                    journal=journal, now=NOW, surface='market'),
                'targets': [wiki.semantic_snapshot(target, records=records)] if target else []}
        answer = payload(data)
        if force_split:
            for addition in answer['targets'][0]['additions']:
                addition['body'] += '\n' + 'Synthetic retained qualification. ' * 250
        if target:
            proposed = answer['targets'][0]
            for key in ('title', 'kind', 'surface', 'purpose'):
                proposed.pop(key)
            proposed.update(page_id=target, before_hash=data['targets'][0]['revision_hash'])

        def provider(prompt, **local):
            provider_calls.append(index)
            assert local['context_mode'] == 'source_note_snapshot'
            validate_topic_prompt(json.loads(prompt.rsplit('\n', 1)[1]),
                                  journal=journal, reserved_revision_id=source['revision_id'],
                                  now=NOW, **local)
            return json.dumps(answer)

        proposal = propose_topic_integration_v3(**data, journal=journal,
            records=records, now=NOW, llm_fn=provider)
        batch = transaction.prepare_topic_batch_v3(proposal, journal=journal,
                                                   records=records, now=NOW)
        result = transaction.recover_topic_operation_v3(batch['operation_id'], journal=journal, now=NOW)
        assert result['stage'] == 'committed' and result['newly_committed']
        assert result['effects']['artifacts'] == result['effects']['log'] == 'done'
        return batch

    seed = commit(1)
    target = seed['target_ids'][0]
    revisions = {source['revision_id'] for _, source, _ in sources}
    assert target in public_target_ids(journal, {
        source['revision_id'] for _, source, _ in sources[:2]})
    assert reservation_calls == [sources[1][1]['revision_id']]
    before = wiki.semantic_snapshot(target)
    if force_split:
        children = wiki.split_children_for_page(wiki.get_page(target))
        assert children
        assert all(child['parent_page_id'] == target for child in children)
    assert {claim['source_revision_id'] for claim in before['claims']} == {
        source['revision_id'] for _, source, _ in sources[:2]}
    seed_reader = wiki_publication.pin_generation(root=root)
    followup = commit(2, target)
    assert followup['target_ids'] == [target]
    assert target in public_target_ids(journal, revisions)
    after = wiki.semantic_snapshot(target)
    assert all(claim in after['claims'] for claim in before['claims'])
    assert before['body'] in after['body']
    assert {claim['source_revision_id'] for claim in after['claims']} == {
        source['revision_id'] for _, source, _ in sources}
    assert provider_calls == [1, 2]
    assert reservation_calls == [sources[1][1]['revision_id'], sources[2][1]['revision_id']]
    assert wiki_public_budget.status(journal.root / 'automatic-budget.json', NOW)['count'] == 2
    reader = wiki_publication.pin_generation(root=root)
    assert reader.generation_id != seed_reader.generation_id
    assert f'[[{target}|' in reader.read('index.md').decode()
    if force_split:
        children = after['children']
        assert children
        assert 'Synthetic retained qualification.' in ''.join(child['body'] for child in children)
        for child in children:
            exported = reader.read(f"notes/{child['id']}.md").decode().split('\n## Canonical Record\n', 1)[0]
            assert child['body'] in exported
            assert target in child['links']
            assert set(after['source_refs']) <= set(child['source_refs'])
            for _, source, _ in sources:
                assert source['canonical_url'] in exported
    for pin in pins:
        citation = frozen_source_note_citation(pin['note_id'], reader=reader)
        assert citation['availability'] == 'historical'
        assert citation['markdown_bytes'] == pin['note_md_bytes']
        assert f"../source-notes/{pin['note_id']}.md" in reader.read(f'notes/{target}.md').decode()
    assert {name: Path(name).read_bytes() for name in immutable} == immutable
    before_events = shared_memory._paths()['events'].read_bytes()
    monkeypatch.setattr(journal, 'load_source', lambda *_: pytest.fail('recovery reread source'))
    monkeypatch.setattr(wiki_public_budget, 'reserve', lambda *a, **k: pytest.fail('recovery quota'))
    replay = transaction.recover_topic_operation_v3(followup['operation_id'], journal=journal, now=NOW)
    assert replay['stage'] == 'committed' and not replay['newly_committed']
    assert shared_memory._paths()['events'].read_bytes() == before_events
    assert provider_calls == [1, 2]
    assert frozen_source_note_citation(pins[0]['note_id'], reader=seed_reader)['markdown_bytes'] == pins[0]['note_md_bytes']


def test_real_store_commit_and_publication_interruptions_recover_without_recalling_model(
        evidence, tmp_path, monkeypatch):
    from reports import wiki_publication_effects as effects, wiki_public_budget

    _, journal, records = evidence
    monkeypatch.setenv('AGENT_CONSOLE_SHARED_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_ARTIFACTS_DIR', str(tmp_path / 'wiki'))
    monkeypatch.setenv('AGENT_CONSOLE_QMD_ENABLED', '0')
    monkeypatch.setenv('AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED', '1')
    monkeypatch.setattr(shared_memory, '_schedule_context_memory_summary', lambda: None)
    root = tmp_path / 'publication'
    monkeypatch.setattr(effects, 'publication_root', lambda: root)
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(records)
    monkeypatch.setattr(shared_memory, 'inspect_records', lambda: transaction._rows())
    calls = []

    def provider(prompt, **local):
        calls.append('proposal')
        return json.dumps(payload(evidence[0]))

    proposal = propose(evidence, provider)
    for entry in proposal['source_map'].values():
        materialize_source_note(entry['revision_id'], journal=journal, now=NOW, apply=True)
    batch = transaction.prepare_topic_batch_v3(proposal, journal=journal, records=records, now=NOW)
    transition = journal.transition_topic_v3

    def lose_receipt_transition(operation_id, **kwargs):
        if kwargs.get('stage') == 'committed':
            raise RuntimeError('synthetic crash after atomic store commit')
        return transition(operation_id, **kwargs)

    monkeypatch.setattr(journal, 'transition_topic_v3', lose_receipt_transition)
    with pytest.raises(RuntimeError, match='synthetic crash after atomic store commit'):
        transaction.recover_topic_operation_v3(batch['operation_id'], journal=journal, now=NOW)
    assert journal.load_topic_operation_v3(batch['operation_id'])['stage'] == 'prepared'
    expected_witness = transaction.topic_commit_record_v3(batch)
    assert [row for row in transaction._rows() if row['id'] == expected_witness['id']] == [expected_witness]
    committed_bytes = shared_memory._paths()['events'].read_bytes()
    monkeypatch.setattr(journal, 'transition_topic_v3', transition)
    monkeypatch.setattr(journal, 'load_source', lambda *_: pytest.fail('historical source lookup'))
    monkeypatch.setattr(wiki_public_budget, 'reserve', lambda *a, **k: pytest.fail('historical quota'))
    publish = effects._publish_intent

    def unavailable_publication(*args, **kwargs):
        raise OSError('synthetic publication interruption')

    monkeypatch.setattr(effects, '_publish_intent', unavailable_publication)
    interrupted = transaction.recover_topic_operation_v3(batch['operation_id'], journal=journal, now=NOW)
    assert interrupted['stage'] == 'committed'
    assert interrupted['effects']['artifacts'] == 'failed'
    assert not (root / 'current.json').exists()
    sidecar = journal._path('publication-effects', batch['operation_id']).read_bytes()
    monkeypatch.setattr(effects, '_publish_intent', publish)
    result = transaction.recover_topic_operation_v3(batch['operation_id'], journal=journal, now=NOW)
    assert result['stage'] == 'committed' and not result['newly_committed']
    assert result['effects']['artifacts'] == result['effects']['log'] == 'done'
    assert shared_memory._paths()['events'].read_bytes() == committed_bytes
    assert calls == ['proposal']
    reader = wiki_publication.pin_generation(root=root)
    frozen_before = json.loads(sidecar)['snapshot']
    assert reader.snapshot_sha256 == frozen_before['snapshot_sha256']
    for note_id in frozen_before['source_note_assets']:
        assert frozen_source_note_citation(note_id, reader=reader)['availability'] == 'historical'


@pytest.mark.parametrize('moment', ['before_provider', 'after_provider'])
def test_expiry_after_selection_never_commits_a_topic(evidence, tmp_path, monkeypatch, moment):
    from scripts.run_public_wiki_rollout import validate_topic_prompt

    data, journal, records = evidence
    monkeypatch.setenv('AGENT_CONSOLE_SHARED_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setenv('AGENT_CONSOLE_QMD_ENABLED', '0')
    with shared_memory._events_lock():
        shared_memory._write_jsonl_locked(records)
    for revision in [data['source']['revision_id'],
                     *[note['revision_id'] for note in data['context']['notes']]]:
        materialize_source_note(revision, journal=journal, now=NOW, apply=True)
    original_events = shared_memory._paths()['events'].read_bytes()
    manifest_path = Path(data['source']['manifest_ref'])

    def expire():
        manifest = json.loads(manifest_path.read_bytes())
        manifest['expires_at'] = NOW.isoformat()
        manifest_path.write_bytes(encoded(manifest))

    calls = []

    def provider(prompt, **local):
        validate_topic_prompt(json.loads(prompt.rsplit('\n', 1)[1]), journal=journal,
                              reserved_revision_id=data['source']['revision_id'], now=NOW, **local)
        calls.append('model')
        expire()
        return json.dumps(payload(data))

    if moment == 'before_provider':
        expire()
    with pytest.raises(ValueError):
        proposal = propose(evidence, provider)
        transaction.prepare_topic_batch_v3(proposal, journal=journal, records=records, now=NOW)
    assert calls == ([] if moment == 'before_provider' else ['model'])
    assert shared_memory._paths()['events'].read_bytes() == original_events
    assert not list((journal.root / 'topic-operations-v3').glob('*.json'))
