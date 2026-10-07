"""Local protocol fixtures only: no weights, inference libraries, or network."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


WORKER = r'''
import json, os, signal, sys, time
mode, log = sys.argv[1:]
with open(log, 'a', encoding='utf-8') as f:
    f.write(json.dumps({'pid': os.getpid(), 'offline': os.getenv('HF_HUB_OFFLINE'),
                        'secret': os.getenv('LAYA_TEST_SECRET')}) + '\n')
if mode == 'startup_timeout':
    time.sleep(10)
if mode == 'bad_ready':
    print(json.dumps({'ready': True, 'backend': 'other'}), flush=True)
else:
    print(json.dumps({'ready': True, 'backend': 'laya'}), flush=True)
if mode == 'ignore_term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
for line in sys.stdin:
    request = json.loads(line)
    with open(log, 'a', encoding='utf-8') as f:
        f.write(json.dumps(request, ensure_ascii=False) + '\n')
    if mode in ('timeout', 'ignore_term'):
        time.sleep(10)
    if mode == 'exit':
        sys.exit(1)
    if mode == 'corrupt':
        print('private-secret-not-json', flush=True)
        continue
    if mode == 'oversized':
        print('x' * 100000, flush=True)
        continue
    if mode == 'error':
        print(json.dumps({'error': 'private-secret-worker-error'}), flush=True)
        continue
    keys = list(request['questions']['action']['criteria'])
    answer = {'type': 'choice', 'choice': keys[-1],
              'probabilities': {k: (0.9 if k == keys[-1] else 0.1 / max(1, len(keys)-1))
                                for k in keys}, 'confidence': 0.9}
    if mode == 'unknown':
        answer['choice'] = 'wiki:invented'
    if mode == 'unknown_probability':
        answer['probabilities']['invented'] = 0.5
    if mode == 'missing_probability':
        del answer['probabilities'][keys[-1]]
    if mode == 'bad_type':
        answer['type'] = 'text'
    if mode in ('nan', 'negative', 'over_one', 'boolean'):
        answer['probabilities'][keys[-1]] = {
            'nan': float('nan'), 'negative': -0.1, 'over_one': 1.1, 'boolean': True}[mode]
    if mode == 'bad_confidence':
        answer['confidence'] = float('inf')
    if mode == 'mismatched_confidence':
        answer['confidence'] = 0.99
    if mode == 'unnormalized':
        answer['probabilities'] = {k: 0.9 for k in keys}
    if mode == 'rounded':
        answer['probabilities'] = {k: 0.333333 for k in keys}
        answer['confidence'] = 0.333333
    if mode in ('native_rounded', 'rounding_overflow'):
        answer['probabilities'] = {k: (0.9001 if k == keys[-1] else 0.0143) for k in keys}
        if mode == 'rounding_overflow':
            answer['probabilities'][keys[0]] = 0.0146
        answer['confidence'] = 0.9001
    if mode == 'truncated':
        response = {'usage': {'truncated': True}, 'answers': {'action': answer}}
    else:
        response = {'answers': {'action': answer}}
    if mode.startswith('options_'):
        options = {'total': len(keys), 'distinct': len(keys), 'tokens_per_option': None}
        if mode == 'options_collapsed': options['distinct'] -= 1
        if mode == 'options_total_mismatch': options['total'] += 1
        if mode == 'options_distinct_mismatch': options['distinct'] += 1
        if mode == 'options_boolean_count': options['total'] = True
        if mode == 'options_bad_tokens': options['tokens_per_option'] = -1
        if mode == 'options_missing_count': del options['distinct']
        response['usage'] = {'truncated': False, 'options': {'action': options}}
        if mode == 'options_unknown_question': response['usage']['options'] = {'other': options}
        if mode == 'options_bad_container': response['usage']['options'] = []
    if mode == 'dropped_tokens':
        response['usage'] = {'truncated': False, 'state_tokens_dropped': 1}
    print(json.dumps(response), flush=True)
'''


@pytest.fixture
def bridge(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('LAYA_'):
            monkeypatch.delenv(key)
    assert importlib.util.find_spec('agent_console.laya_bridge') is not None, 'bridge is not implemented'
    from agent_console import laya_bridge
    laya_bridge.close()
    yield laya_bridge
    laya_bridge.close()


@pytest.fixture
def pages():
    return [
        {'id': 'p1', 'title': 'Rates', 'body': 'Rate conditions.',
         'source_refs': ['https://example.com/rates'], 'evidence_ids': ['e1']},
        {'id': 'p2', 'title': 'Margins', 'summary': 'Margin conditions.',
         'source_refs': ['https://example.com/margins'], 'evidence_ids': ['e2']},
        {'id': 'p3', 'title': 'Korean equities', 'body': 'Domestic conditions.',
         'source_refs': ['https://example.com/kr'], 'evidence_ids': ['e3']},
    ]


def configure(monkeypatch, tmp_path, mode='valid', validated=True):
    log = tmp_path / 'protocol.jsonl'
    monkeypatch.setenv('LAYA_WIKI_ENABLED', 'true')
    monkeypatch.setenv('LAYA_MODEL_WORKER_COMMAND', json.dumps(
        [sys.executable, '-u', '-c', WORKER, mode, str(log)]))
    monkeypatch.setenv('LAYA_WIKI_PROFILE_VALIDATED', str(validated).lower())
    monkeypatch.setenv('LAYA_WIKI_MIN_CONFIDENCE', '0.8')
    monkeypatch.setenv('LAYA_MODEL_WORKER_TIMEOUT_SECONDS', '0.3')
    monkeypatch.setenv('LAYA_MODEL_WORKER_LOAD_TIMEOUT_SECONDS', '1')
    return log


def records(log):
    return [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]


def test_no_model_default_preserves_context_and_never_spawns(bridge, pages, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('disabled/unconfigured bridge started a process')
    monkeypatch.setattr(bridge.subprocess, 'Popen', forbidden)
    result, review = bridge.review_wiki_candidates('market risks?', pages, limit=2)
    assert result == pages[:2]
    assert result[0] is pages[0]
    assert review['mode'] == 'disabled'
    assert review['selected_id'] is None
    monkeypatch.setenv('LAYA_WIKI_ENABLED', 'true')
    result, review = bridge.review_wiki_candidates('market risks?', pages)
    assert result == pages
    assert review['mode'] == 'unconfigured'
    assert bridge.readiness()['ready'] is False


@pytest.mark.parametrize('question', ['see wiki:p3', 'see [[p3]]', 'see (wiki:p3).'])
def test_exact_citation_is_deterministic_without_model(bridge, pages, question):
    result, review = bridge.review_wiki_candidates(question, pages, limit=2)
    assert result == [pages[2], pages[0]]
    assert review['method'] == 'exact_citation'
    assert review['selected_id'] == 'p3'
    assert review['selected_ids'] == ['p3']
    assert 'confidence' not in review
    assert review['backend'] is None


def test_exact_ambiguity_remains_a_set_without_arbitrary_single_choice(bridge, pages):
    result, review = bridge.review_wiki_candidates('[[p3]] and wiki:p1 and [[p3]]', pages, limit=2)
    assert result == [pages[0], pages[2]]
    assert set(review['selected_ids']) == {'p1', 'p3'}
    assert review['selected_id'] is None
    assert review['needs_review'] is True
    assert review['reason'] == 'ambiguous_exact_citations'
    assert 'confidence' not in review


@pytest.mark.parametrize('question', ['wiki:p30', 'notwiki:p3', '[[invented]]'])
def test_never_treats_prefix_or_unknown_reference_as_existing_id(bridge, pages, question):
    result, review = bridge.review_wiki_candidates(question, pages)
    assert result == pages
    assert review['selected_id'] is None


def test_exact_citation_precedes_even_configured_worker(bridge, pages, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    result, review = bridge.review_wiki_candidates('[[p2]]', pages)
    assert result[0] is pages[1]
    assert review['method'] == 'exact_citation'
    assert not log.exists()


def test_valid_choice_reorders_only_supplied_pages_and_preserves_sources(bridge, pages, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    result, review = bridge.review_wiki_candidates('domestic risks?', pages, limit=2)
    assert result == [pages[2], pages[0]]
    assert result[0] is pages[2]
    assert review['selected_id'] == 'p3'
    assert review['backend'] == 'laya'
    assert review['mode'] == 'active'
    assert review['confidence'] == 0.9
    assert review['reason'] == 'validated_profile_selection'
    assert {item['id'] for item in review['alternatives']} == {'p1', 'p2'}
    assert review['sources'][2]['source_refs'] == pages[2]['source_refs']


def test_unvalidated_financial_profile_is_shadow_even_at_high_confidence(bridge, pages, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, validated=False)
    result, review = bridge.review_wiki_candidates('\ud55c\uad6d \uc8fc\uc2dd \ub9ac\uc2a4\ud06c?', pages, limit=2)
    assert result == pages[:2]
    assert review['selected_id'] == 'p3'
    assert review['mode'] == 'shadow'
    assert review['needs_review'] is True
    assert review['reason'] == 'profile_unvalidated'


def test_validated_but_low_confidence_keeps_baseline(bridge, pages, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv('LAYA_WIKI_MIN_CONFIDENCE', '0.95')
    result, review = bridge.review_wiki_candidates('risks?', pages, limit=2)
    assert result == pages[:2]
    assert review['selected_id'] == 'p3'
    assert review['reason'] == 'low_confidence'
    assert review['needs_review'] is True


@pytest.mark.parametrize('mode', [
    'unknown', 'unknown_probability', 'missing_probability', 'bad_type', 'nan',
    'negative', 'over_one', 'boolean', 'bad_confidence', 'mismatched_confidence',
    'unnormalized', 'truncated', 'corrupt', 'oversized', 'error', 'exit', 'bad_ready',
])
def test_bad_worker_preserves_baseline_resets_and_redacts_errors(bridge, pages, monkeypatch, tmp_path, mode):
    log = configure(monkeypatch, tmp_path, mode)
    result, review = bridge.review_wiki_candidates('risk?', pages, limit=2)
    assert result == pages[:2]
    assert review['needs_review'] is True
    assert review['selected_id'] is None
    assert len(review['error_code']) <= 40
    assert 'private-secret' not in json.dumps(review)
    assert bridge.readiness()['ready'] is False
    bridge.review_wiki_candidates('risk?', pages, limit=2)
    assert len([item for item in records(log) if 'pid' in item]) == 2


@pytest.mark.parametrize('mode', ['timeout', 'startup_timeout', 'ignore_term'])
def test_timeout_and_cleanup_are_bounded(bridge, pages, monkeypatch, tmp_path, mode):
    configure(monkeypatch, tmp_path, mode)
    monkeypatch.setenv('LAYA_MODEL_WORKER_LOAD_TIMEOUT_SECONDS', '0.3')
    started = time.monotonic()
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert time.monotonic() - started < 2
    assert result == pages
    assert review['error_code'] == 'worker_timeout'
    assert bridge.readiness()['ready'] is False


def test_persistent_worker_reuse_and_serialized_requests(bridge, pages, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda n: bridge.review_wiki_candidates(str(n), pages), range(6)))
    assert all(result[0]['id'] == 'p3' for result, _ in replies)
    captured = records(log)
    assert len([item for item in captured if 'pid' in item]) == 1
    assert len([item for item in captured if 'questions' in item]) == 6
    assert bridge.readiness()['ready'] is True
    assert len(records(log)) == 7  # Health must not send inference requests.
    bridge.close()
    assert bridge.readiness()['ready'] is False


def test_multilingual_request_is_bounded_and_uses_original_contract(bridge, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    monkeypatch.setenv('LAYA_TEST_SECRET', 'private-secret-env')
    text = '\ud55c\uad6d \uae08\uc735 risk '
    pages = [{'id': 'p' + str(i), 'title': str(i) + ' ' + text * 100, 'body': text * 1000} for i in range(12)]
    history = [{'role': 'user', 'content': text * 1000, 'secret': 'do not send'} for _ in range(30)]
    _, review = bridge.review_wiki_candidates(text * 1000, pages, history=history)
    captured = records(log)
    assert captured[0]['offline'] == '1'
    assert captured[0]['secret'] is None
    request = captured[1]
    assert set(request) == {'state', 'questions'}
    assert request['questions']['action']['type'] == 'choice'
    criteria = request['questions']['action']['criteria']
    assert list(criteria) == ['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7']
    assert all(len(value) <= 180 for value in criteria.values())
    assert len(request['state']['read_documents']) == 8
    assert all(len(doc['text']) <= 600 for doc in request['state']['read_documents'])
    assert len(request['state']['goal']) <= 2000
    assert len(request['state']['history']) <= 4
    assert all(len(row['content']) <= 600 for row in request['state']['history'])
    assert text in request['state']['goal']
    assert 'relevance' in request['questions']['action']['instructions'].lower()
    assert review['selected_id'] == 'p7'
    assert 'do not send' not in json.dumps(request)


@pytest.mark.parametrize('command', ['echo unsafe', '[]', '["python", 3]', '{}', '[""]'])
def test_invalid_command_never_executes_shell(bridge, pages, monkeypatch, command):
    monkeypatch.setenv('LAYA_WIKI_ENABLED', 'true')
    monkeypatch.setenv('LAYA_MODEL_WORKER_COMMAND', command)
    monkeypatch.setattr(bridge.subprocess, 'Popen', lambda *a, **kw: pytest.fail('invalid command executed'))
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['needs_review'] is True
    assert review['error_code'] == 'invalid_configuration'


def test_original_code_without_private_assets_is_diagnosed_without_start(bridge, pages, monkeypatch, tmp_path):
    root = tmp_path / 'model-worker'
    root.mkdir()
    (root / 'worker.py').write_text('# Code-only fixture; must never execute.\n', encoding='utf-8')
    monkeypatch.setenv('LAYA_WIKI_ENABLED', 'true')
    monkeypatch.setenv('LAYA_MODEL_WORKER_COMMAND', json.dumps([
        sys.executable, str(root / 'worker.py'), '--model', str(root / 'base-model'),
        '--head', str(root / 'trained')]))
    monkeypatch.setattr(bridge.subprocess, 'Popen', lambda *a, **kw: pytest.fail('missing assets loaded'))
    status = bridge.readiness()
    assert status['code_configured'] is True
    assert status['assets_status'] == 'missing'
    assert status['model_root_status'] == 'code_configured_assets_missing'
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['error_code'] == 'model_assets_missing'


def test_readiness_can_handshake_without_inference(bridge, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    assert bridge.readiness()['ready'] is False
    assert not log.exists()
    assert bridge.readiness(start=True)['ready'] is True
    assert len(records(log)) == 1


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '1.1', 'nonsense'])
def test_invalid_confidence_config_cannot_enable_reordering(bridge, pages, monkeypatch, tmp_path, value):
    log = configure(monkeypatch, tmp_path)
    monkeypatch.setenv('LAYA_WIKI_MIN_CONFIDENCE', value)
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['error_code'] == 'invalid_configuration'
    assert not log.exists()


def test_empty_menu_and_zero_limit_do_not_call_worker(bridge, pages, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    assert bridge.review_wiki_candidates('risks?', [], limit=4)[0] == []
    assert bridge.review_wiki_candidates('risks?', pages, limit=0)[0] == []
    assert not log.exists()


def test_camelcase_provenance_is_preserved_in_review(bridge, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    pages = [
        {'id': 'p1', 'title': 'Rates', 'body': 'Rates.',
         'sourceRefs': ['https://example.com/rates'], 'evidenceIds': ['e1']},
        {'id': 'p2', 'title': 'Margins', 'body': 'Margins.',
         'sourceRefs': ['https://example.com/margins'], 'evidenceIds': ['e2']},
    ]
    result, review = bridge.review_wiki_candidates('margins?', pages)
    assert result[0] is pages[1]
    assert review['sources'] == [
        {'id': 'p1', 'source_refs': ['https://example.com/rates'], 'evidence_ids': ['e1']},
        {'id': 'p2', 'source_refs': ['https://example.com/margins'], 'evidence_ids': ['e2']},
    ]


def test_rounded_normalized_probabilities_are_accepted(bridge, pages, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, 'rounded')
    _, review = bridge.review_wiki_candidates('risks?', pages)
    assert review['selected_id'] == 'p3'
    assert review['confidence'] == 0.333333
    assert 'error_code' not in review


def test_close_interrupts_inflight_worker_with_bounded_cleanup(bridge, pages, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path, 'timeout')
    monkeypatch.setenv('LAYA_MODEL_WORKER_TIMEOUT_SECONDS', '5')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bridge.review_wiki_candidates, 'risks?', pages)
        deadline = time.monotonic() + 2
        while not log.exists() or len(records(log)) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        started = time.monotonic()
        bridge.close()
        assert time.monotonic() - started < 1.5
        assert bridge.readiness()['ready'] is False
        result, review = future.result(timeout=1)
    assert result == pages
    assert review['error_code'] == 'worker_closed'


@pytest.mark.parametrize('setting', ['disabled', 'unconfigured'])
def test_disabling_configuration_closes_idle_worker(bridge, pages, monkeypatch, tmp_path, setting):
    log = configure(monkeypatch, tmp_path)
    bridge.review_wiki_candidates('risks?', pages)
    pid = records(log)[0]['pid']
    if setting == 'disabled':
        monkeypatch.setenv('LAYA_WIKI_ENABLED', 'false')
    else:
        monkeypatch.delenv('LAYA_MODEL_WORKER_COMMAND')
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['mode'] == setting
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_unsupported_platform_never_starts_worker(bridge, pages, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    monkeypatch.setattr(bridge, '_POSIX_SUPPORTED', False)
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['error_code'] == 'unsupported_worker_platform'
    assert not log.exists()


def test_provenance_merges_both_naming_styles_without_losing_sources(bridge):
    page = {'id': 'p1', 'body': 'Original body.', 'source_refs': ['source:a'],
            'sourceRefs': ['source:a', 'source:b'], 'artifacts': ['source:c'],
            'evidence_ids': ['e1'], 'evidenceIds': ['e2']}
    result, review = bridge.review_wiki_candidates('[[p1]]', [page])
    assert result[0] is page
    assert review['sources'][0] == {
        'id': 'p1', 'source_refs': ['source:a', 'source:b', 'source:c'], 'evidence_ids': ['e1', 'e2']}


def test_native_four_decimal_rounding_accepts_per_candidate_drift(bridge, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, 'native_rounded')
    pages = [{'id': 'p' + str(i)} for i in range(8)]
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result[0]['id'] == 'p7'
    assert review['confidence'] == 0.9001
    assert 'error_code' not in review


def test_sum_outside_native_rounding_bound_is_rejected(bridge, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, 'rounding_overflow')
    pages = [{'id': 'p' + str(i)} for i in range(8)]
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages[:4]
    assert review['error_code'] == 'invalid_probabilities'


@pytest.mark.parametrize('mode', [
    'options_collapsed', 'options_total_mismatch', 'options_distinct_mismatch',
    'options_boolean_count', 'options_bad_tokens', 'options_missing_count',
    'options_unknown_question', 'options_bad_container', 'dropped_tokens',
])
def test_invalid_native_usage_preserves_baseline_and_resets(bridge, pages, monkeypatch, tmp_path, mode):
    configure(monkeypatch, tmp_path, mode)
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['needs_review'] is True
    assert review['selected_id'] is None
    assert review['error_code'] in {'invalid_worker_usage', 'worker_options_collapsed', 'worker_truncated'}
    assert bridge.readiness()['ready'] is False


def test_uncollapsed_native_option_counts_are_accepted(bridge, pages, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, 'options_valid')
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result[0] is pages[2]
    assert 'error_code' not in review


def test_exact_reference_after_model_question_bound_is_found_without_worker(bridge, pages, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    result, review = bridge.review_wiki_candidates('x' * 3000 + ' [[p3]]', pages)
    assert result[0] is pages[2]
    assert review['method'] == 'exact_citation'
    assert review['selected_id'] == 'p3'
    assert not log.exists()


def test_reference_outside_eight_candidate_menu_is_explicitly_reviewed(bridge):
    pages = [{'id': 'p' + str(i)} for i in range(9)]
    result, review = bridge.review_wiki_candidates('[[p8]]', pages)
    assert result == pages[:4]
    assert review['candidate_menu_truncated'] is True
    assert review['unmatched_reference_ids'] == ['p8']
    assert review['needs_review'] is True
    assert review['selected_id'] is None
    assert len(review['candidate_ids']) == 8


def test_exact_scan_cap_is_reported_instead_of_silent_success(bridge, pages):
    _, review = bridge.review_wiki_candidates('[[p1]] ' + 'x' * 65536 + ' [[p3]]', pages)
    assert review['exact_reference_scan_truncated'] is True
    assert review['needs_review'] is True


@pytest.mark.parametrize('head', [None, 'missing-files'])
def test_original_worker_requires_trained_head_before_start(bridge, pages, monkeypatch, tmp_path, head):
    root = tmp_path / 'model-worker'
    command = [sys.executable, str(root / 'worker.py'), '--model', str(root / 'base-model')]
    if head:
        command += ['--head', str(root / head)]
    monkeypatch.setenv('LAYA_WIKI_ENABLED', 'true')
    monkeypatch.setenv('LAYA_MODEL_WORKER_COMMAND', json.dumps(command))
    monkeypatch.setattr(bridge.Path, 'is_file', lambda path: path.name in {'worker.py', 'model.safetensors'})
    monkeypatch.setattr(bridge.Path, 'is_dir', lambda path: True)
    monkeypatch.setattr(bridge.subprocess, 'Popen', lambda *a, **kw: pytest.fail('headless original worker ran'))
    status = bridge.readiness(start=True)
    assert status['head_status'] == 'missing'
    assert status['model_root_status'] == 'code_configured_head_missing'
    assert status['error_code'] == 'model_head_missing'
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['head_status'] == 'missing'
    assert review['error_code'] == 'model_head_missing'


def test_public_reference_ids_are_ordered_deduplicated_and_bounded(bridge):
    assert bridge.referenced_page_ids('[[p3]] wiki:p1 [[p3]] (wiki:p2). notwiki:p0') == ['p3', 'p1', 'p2']
    assert bridge.referenced_page_ids('x' * 3000 + ' [[p9]]') == ['p9']
    assert bridge.referenced_page_ids(' '.join('[[p' + str(i) + ']]' for i in range(12))) == [
        'p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7']
    assert bridge.referenced_page_ids('x' * 65536 + ' [[p9]]') == []
    assert bridge.referenced_page_ids(None) == []


def test_unresolved_exact_reference_stops_learned_selection(bridge, pages, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    result, review = bridge.review_wiki_candidates('[[missing-page]] risks?', pages)
    assert result == pages
    assert review['selected_id'] is None
    assert review['needs_review'] is True
    assert review['reason'] == 'unmatched_exact_references'
    assert review['mode'] == 'baseline'
    assert not log.exists()


def test_partially_unresolved_reference_set_keeps_baseline(bridge, pages):
    result, review = bridge.review_wiki_candidates('[[p3]] and [[missing-page]]', pages)
    assert result == pages
    assert review['needs_review'] is True
    assert review['selected_id'] is None
    assert review['unmatched_reference_ids'] == ['missing-page']


@pytest.mark.parametrize('mode', ['unknown', 'timeout'])
def test_failed_worker_never_reports_active_mode(bridge, pages, monkeypatch, tmp_path, mode):
    configure(monkeypatch, tmp_path, mode)
    _, review = bridge.review_wiki_candidates('risks?', pages)
    assert review['mode'] == 'baseline'
    assert review['configured_policy'] == 'active'


def test_low_confidence_never_reports_active_mode(bridge, pages, monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv('LAYA_WIKI_MIN_CONFIDENCE', '0.95')
    _, review = bridge.review_wiki_candidates('risks?', pages)
    assert review['mode'] == 'baseline'
    assert review['configured_policy'] == 'active'


def test_same_titles_are_disambiguated_by_supplied_excerpts(bridge, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    pages = [{'id': 'p1', 'title': 'Risk', 'summary': 'Rates', 'body': 'Rates evidence.'},
             {'id': 'p2', 'title': 'Risk', 'summary': 'Margins', 'body': 'Margins evidence.'}]
    result, review = bridge.review_wiki_candidates('margins?', pages)
    assert result[0] is pages[1]
    criteria = records(log)[1]['questions']['action']['criteria']
    assert criteria['p1'] != criteria['p2']
    assert 'Rates' in criteria['p1'] and 'Margins' in criteria['p2']
    assert 'evidence' in criteria['p1']
    assert review['mode'] == 'active'


def test_indistinguishable_criteria_stop_before_worker(bridge, monkeypatch, tmp_path):
    log = configure(monkeypatch, tmp_path)
    pages = [{'id': 'p1', 'title': 'Risk', 'body': 'Same source excerpt.'},
             {'id': 'p2', 'title': 'Risk', 'body': 'Same source excerpt.'}]
    result, review = bridge.review_wiki_candidates('risks?', pages)
    assert result == pages
    assert review['needs_review'] is True
    assert review['reason'] == 'indistinguishable_candidates'
    assert review['mode'] == 'baseline'
    assert not log.exists()
