"""Resumable, exact-span compilation; raw sources are never rewritten."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import safe_io

from reports.wiki_source_compiler import compile_source
from reports.wiki_source_journal import Journal
from reports.wiki_source_journal import _encoded

CHUNK_CHARS = 20_000


class ChunkingPending(ValueError):
    def __init__(self, completed: int, total: int):
        super().__init__('chunk_budget_pending')
        self.completed = completed
        self.total = total


def source_chunks(source: dict) -> list[dict]:
    body = source['body']
    if not isinstance(body, str) or not body.strip():
        raise ValueError('chunk_source_invalid')
    parts = []
    for start in range(0, len(body), CHUNK_CHARS):
        end = min(start + CHUNK_CHARS, len(body))
        text = body[start:end]
        identity = {'protocol': 1, 'parent': source['revision_id'], 'span': [start, end],
                    'sha256': hashlib.sha256(text.encode()).hexdigest()}
        revision = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        parts.append({**deepcopy(source), 'revision_id': revision, 'body': text,
                      'chunk_parent_revision_id': source['revision_id'], 'source_span': [start, end],
                      'text_sha256': identity['sha256']})
    return parts


def compile_chunks(source: dict, *, journal: Journal, llm_fn, max_calls: int = 4) -> dict:
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError('chunk_budget_invalid')
    journal._path('sources', source['revision_id'])
    parts = source_chunks(source)
    child_journal = Journal(journal.root / 'chunks' / source['revision_id'])
    outputs = []
    calls = 0
    for part in parts:
        with child_journal.source_lock(part['revision_id']):
            receipt = child_journal.load_source(part['revision_id'])
            if receipt and receipt['compile_state'] in {'compiled', 'skipped'}:
                compiled = child_journal.load_compilation(receipt)
            else:
                path = child_journal._path('accepted', part['revision_id'])
                accepted = child_journal._read(path)
                if accepted:
                    compiled = accepted.get('compiled')
                    if (not isinstance(compiled, dict) or compiled.get('revision_id') != part['revision_id']
                            or accepted.get('sha256') != hashlib.sha256(_encoded(compiled).encode()).hexdigest()):
                        raise ValueError('journal_corrupt')
                else:
                    if calls >= max_calls:
                        raise ChunkingPending(len(outputs), len(parts))
                    child_journal.record_attempt(part, state='pending')
                    calls += 1
                    try:
                        compiled = compile_source(part, llm_fn=llm_fn)
                    except Exception:
                        child_journal.record_attempt(part, state='failed', error_code='chunk_compile_failed')
                        raise
                    safe_io.atomic_write_json(str(path), {'schema_version': 1, 'compiled': compiled,
                        'sha256': hashlib.sha256(_encoded(compiled).encode()).hexdigest()})
                child_journal.save_compilation(part, compiled)
            outputs.append(compiled)
    return _aggregate(source, parts, outputs)


def _aggregate(source: dict, parts: list[dict], outputs: list[dict]) -> dict:
    claims, prose, terms, limitations, skipped = [], [], [], [], []
    for part, compiled in zip(parts, outputs):
        start, end = part['source_span']
        if compiled['skip_reason']:
            skipped.append(f'Characters {start}:{end}: {compiled["skip_reason"]}')
            continue
        mapping = {claim['claim_id']: hashlib.sha256(
            f'{source["revision_id"]}:chunk:{start}:claim:{index}'.encode()).hexdigest()
            for index, claim in enumerate(compiled['claims'])}
        for original in compiled['claims']:
            claim = deepcopy(original)
            claim.update(claim_id=mapping[claim['claim_id']], source_revision_id=source['revision_id'],
                         competing_claim_ids=[mapping[value] for value in claim['competing_claim_ids']])
            if claim['anchor'] is not None:
                claim['anchor']['start'] += start
                claim['anchor']['end'] += start
                anchor = claim['anchor']
                if source['body'][anchor['start']:anchor['end']] != anchor['quote']:
                    raise ValueError('chunk_anchor_invalid')
            claims.append(claim)
        prose.append(f'### Source characters {start}:{end}\n{compiled["body"]}')
        terms.extend(compiled['terms'])
        limitations.extend(compiled['limitations'])
    limitations.extend(skipped)
    if claims:
        limitations.append('Compiled by source span; cross-span relationships have not been independently verified.')
    result = {'revision_id': source['revision_id'], 'body': '\n\n'.join(prose), 'claims': claims,
            'terms': list(dict.fromkeys(terms)), 'limitations': list(dict.fromkeys(limitations)),
            'skip_reason': None if claims else '; '.join(skipped)}
    title = next((output['title'] for output in outputs if not output['skip_reason'] and output.get('title')), None)
    if title:
        result['title'] = title
    return result
