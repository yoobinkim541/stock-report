import hashlib
import json

import pytest

from reports.wiki_source_journal import Journal


def module():
    from reports import wiki_source_chunks
    return wiki_source_chunks


def source():
    body = 'A' * 19999 + '\n' + 'B' * 19999 + '\n' + 'TAIL'
    return {'revision_id': 'a' * 64, 'body': body, 'canonical_url': 'https://example.org/full',
            'provider': 'official', 'scope': 'article_fulltext', 'provenance_class': 'editorial',
            'title': 'Long source', 'published_at': 'unknown', 'text_sha256': hashlib.sha256(body.encode()).hexdigest(),
            'observed_evidence_ids': ['e1'], 'role': 'support'}


def source_text(prompt):
    literal = prompt[:-len('\nEND UNTRUSTED SOURCE TEXT')].split('\nBEGIN UNTRUSTED SOURCE TEXT\n', 1)[1]
    return json.loads(literal)


def response(prompt):
    body = source_text(prompt)
    quote = body[:40]
    return json.dumps({'action': 'create', 'kind': 'concept', 'title': 'Chunk knowledge',
        'body': 'Attributed chunk [S1]. ' + body[-4:], 'claims': [{
            'text': 'Attributed content ' + quote, 'as_of': 'unknown', 'scope': 'reported source fragment',
            'anchor': {'start': 0, 'end': len(quote), 'quote': quote}, 'competing_claim_ids': []}],
        'terms': ['source'], 'limitations': ['Source uncertainty ' + body[0]], 'skip_reason': None})


def test_chunk_aggregation_retains_compiled_title(tmp_path):
    compiled = module().compile_chunks(source(), journal=Journal(tmp_path), llm_fn=response)
    assert compiled['title'] == 'Chunk knowledge'


def test_aggregate_skips_leading_skipped_title_and_keeps_other_fragment_bodies():
    from reports.wiki_source_compiler import validate_compilation
    item = source()
    parts = module().source_chunks(item)
    outputs = [validate_compilation({
        'action': 'create', 'title': title, 'body': f'{title} [S1].',
        'claims': [{'text': title, 'as_of': 'unknown', 'scope': 'source fragment',
                    'anchor': None, 'competing_claim_ids': []}],
        'terms': [], 'limitations': [], 'skip_reason': None}, part)
        for part, title in zip(parts, ['Ignored heading', 'Capacity uncertainty', 'Different tail topic'])]
    outputs[0] = {'skip_reason': 'No reusable evidence', 'title': 'Skipped heading'}
    compiled = module()._aggregate(item, parts, outputs)
    assert compiled['title'] == 'Capacity uncertainty'
    assert 'Different tail topic' in compiled['body']
    assert len(compiled['claims']) == 2
    assert any('cross-span' in caveat for caveat in compiled['limitations'])
    for output in outputs:
        output.pop('title', None)
    assert 'title' not in module()._aggregate(item, parts, outputs)


def test_spans_are_contiguous_lossless_bounded_and_revision_specific():
    item = source()
    parts = module().source_chunks(item)
    assert ''.join(part['body'] for part in parts) == item['body']
    assert all(len(part['body']) <= 20000 for part in parts)
    assert [part['source_span'] for part in parts] == [[0, 20000], [20000, 40000], [40000, 40004]]
    assert len({part['revision_id'] for part in parts}) == 3
    assert parts == module().source_chunks(item)
    assert parts[0]['revision_id'] != module().source_chunks({**item, 'revision_id': 'b' * 64})[0]['revision_id']


def test_completed_chunks_resume_after_failed_call_without_recompilation(tmp_path):
    calls = []
    def llm(prompt):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError('synthetic transient failure')
        return response(prompt)
    journal = Journal(tmp_path)
    with pytest.raises(RuntimeError):
        module().compile_chunks(source(), journal=journal, llm_fn=llm)
    compiled = module().compile_chunks(source(), journal=journal, llm_fn=llm)
    assert len(calls) == 4
    assert calls.count(calls[0]) == 1
    assert len(compiled['claims']) == 3
    assert [claim['anchor']['start'] for claim in compiled['claims']] == [0, 20000, 40000]
    assert all(claim['source_revision_id'] == source()['revision_id'] for claim in compiled['claims'])
    assert len({claim['claim_id'] for claim in compiled['claims']}) == 3
    assert 'TAIL' in compiled['body']
    assert set(compiled['limitations']) >= {'Source uncertainty A', 'Source uncertainty B', 'Source uncertainty T'}
    assert module().compile_chunks(source(), journal=journal, llm_fn=lambda _: pytest.fail('cache not reused')) == compiled


def test_call_budget_leaves_whole_source_pending_then_resumes(tmp_path):
    journal = Journal(tmp_path)
    calls = []
    def llm(prompt):
        calls.append(prompt)
        return response(prompt)
    with pytest.raises(module().ChunkingPending):
        module().compile_chunks(source(), journal=journal, llm_fn=llm, max_calls=1)
    assert len(calls) == 1
    with pytest.raises(module().ChunkingPending):
        module().compile_chunks(source(), journal=journal, llm_fn=llm, max_calls=1)
    assert len(calls) == 2
    result = module().compile_chunks(source(), journal=journal, llm_fn=llm, max_calls=1)
    assert len(calls) == 3 and result['skip_reason'] is None


def test_corrupt_saved_chunk_is_not_silently_reused_or_sent_to_model(tmp_path):
    journal = Journal(tmp_path)
    module().compile_chunks(source(), journal=journal, llm_fn=response)
    path = next((tmp_path / 'chunks' / source()['revision_id'] / 'compiled').glob('*.json'))
    path.write_text('{}')
    with pytest.raises(ValueError, match='journal_corrupt'):
        module().compile_chunks(source(), journal=journal, llm_fn=lambda _: pytest.fail('corruption triggered model'))


def test_all_skipped_spans_retain_every_reason_and_are_not_knowledge(tmp_path):
    def skip(prompt):
        body = source_text(prompt)
        return json.dumps({'action': 'skip', 'skip_reason': 'No reusable knowledge: ' + body[0]})
    result = module().compile_chunks(source(), journal=Journal(tmp_path), llm_fn=skip)
    assert result['claims'] == [] and result['body'] == ''
    for marker in ('0:20000', '20000:40000', '40000:40004'):
        assert marker in result['skip_reason']


def test_aggregate_remaps_competing_claim_ids_without_losing_relationship(monkeypatch, tmp_path):
    def compile_part(part, **kwargs):
        first, second = part['revision_id'] + '-1', part['revision_id'] + '-2'
        claims = [{'claim_id': identity, 'source_revision_id': part['revision_id'], 'text': 'Reported claim',
                   'as_of': 'unknown', 'scope': 'source fragment', 'anchor': None,
                   'competing_claim_ids': [other], 'knowledge_state': 'draft'}
                  for identity, other in ((first, second), (second, first))]
        return {'revision_id': part['revision_id'], 'body': 'Attributed claims', 'claims': claims,
                'terms': [], 'limitations': [], 'skip_reason': None}
    monkeypatch.setattr(module(), 'compile_source', compile_part)
    result = module().compile_chunks(source(), journal=Journal(tmp_path), llm_fn=None)
    claims = result['claims']
    for index in range(0, len(claims), 2):
        assert claims[index]['competing_claim_ids'] == [claims[index + 1]['claim_id']]
        assert claims[index + 1]['competing_claim_ids'] == [claims[index]['claim_id']]


def test_chunk_parent_identity_cannot_escape_journal_directory(tmp_path):
    item = {**source(), 'revision_id': '../outside'}
    with pytest.raises(ValueError, match='identity_invalid'):
        module().compile_chunks(item, journal=Journal(tmp_path), llm_fn=lambda _: pytest.fail('invalid identity invoked model'))
    assert not list(tmp_path.rglob('*'))


def test_partial_skip_remains_explicit_without_marking_whole_source_skipped(tmp_path):
    def llm(prompt):
        body = source_text(prompt)
        if body.startswith('B'):
            return json.dumps({'action': 'skip', 'skip_reason': 'No reusable facts in middle span'})
        return response(prompt)
    result = module().compile_chunks(source(), journal=Journal(tmp_path), llm_fn=llm)
    assert result['skip_reason'] is None and len(result['claims']) == 2
    assert any('20000:40000' in note and 'No reusable facts' in note for note in result['limitations'])


def test_crash_after_result_persistence_reuses_same_result(monkeypatch, tmp_path):
    original = Journal.save_compilation
    calls = []
    def interrupted(self, part, compiled):
        if len(calls) == 1:
            raise RuntimeError('synthetic receipt interruption')
        return original(self, part, compiled)
    def llm(prompt):
        calls.append(prompt)
        return response(prompt)
    monkeypatch.setattr(Journal, 'save_compilation', interrupted)
    with pytest.raises(RuntimeError):
        module().compile_chunks(source(), journal=Journal(tmp_path), llm_fn=llm)
    monkeypatch.setattr(Journal, 'save_compilation', original)
    result = module().compile_chunks(source(), journal=Journal(tmp_path), llm_fn=llm)
    assert len(calls) == 3 and len(result['claims']) == 3
