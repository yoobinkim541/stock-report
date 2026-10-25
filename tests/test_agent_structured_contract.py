import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_console import agent


@pytest.mark.parametrize('provider', ['codex', 'hermes', 'gemini', 'agy'])
def test_structured_provider_accepts_language_neutral_routing(monkeypatch, provider):
    response = json.dumps({'selected': ['public-evaluation'], 'rejected': {}, 'new_page_specs': []})

    def runner(command, **kwargs):
        if '--output-last-message' in command:
            Path(command[command.index('--output-last-message') + 1]).write_text(response)
        return SimpleNamespace(returncode=0, stdout=response)

    if provider == 'agy':
        from lib import llm_cli
        monkeypatch.setattr(llm_cli, 'backup_chat', lambda *a, **kw: (response, 'test'))
        result = agent._try_agy_backup('한국어 원문의 연결을 JSON으로 반환', structured_output=True)
    else:
        monkeypatch.setenv('AGENT_CONSOLE_' + provider.upper() + '_ENABLED', '1')
        result = getattr(agent, '_try_' + provider + '_chat')(
            '한국어 원문의 연결을 JSON으로 반환', runner=runner, structured_output=True)
    assert result == response


@pytest.mark.parametrize('response', [
    '{"selected":', 'Here is JSON: {"selected": []}',
    '{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}',
    '[]', 'null', '{"x":' + '[' * 65 + '0' + ']' * 65 + '}',
    json.dumps({'x': 'a' * 100_001}),
], ids=['partial', 'mixed', 'duplicate', 'nan', 'infinity', 'overflow',
        'array', 'null', 'deep', 'oversized'])
def test_structured_validation_rejects_untrusted_or_incomplete_json(response):
    assert not agent._is_usable_llm_output('한국어 입력', response, structured_output=True)


def test_structured_validation_does_not_scan_quoted_source_as_a_refusal():
    response = '{"quote":"as an AI language model", "selected":[]}'
    assert agent._is_usable_llm_output('한국어 입력', response, structured_output=True)
    assert not agent._is_usable_llm_output('한국어 입력', 'An English chat answer')


@pytest.mark.parametrize('winner', ['codex', 'hermes', 'gemini', 'agy'])
def test_structured_contract_survives_race_and_fallbacks(monkeypatch, winner):
    response = '{"selected":[]}'
    calls = []

    def provider(name):
        def invoke(prompt, **kwargs):
            calls.append((name, kwargs.get('structured_output')))
            return response if name == winner and kwargs.get('structured_output') else None
        return invoke

    monkeypatch.setenv('AGENT_CONSOLE_LLM_ENABLED', '1')
    for name in ['codex', 'hermes', 'gemini', 'agy']:
        monkeypatch.setattr(agent, '_try_' + name + ('_backup' if name == 'agy' else '_chat'), provider(name))
    assert agent.request_structured_output('한국어 입력') == response
    assert calls and all(enabled is True for _, enabled in calls)


@pytest.mark.parametrize('provider', ['codex', 'hermes', 'gemini', 'agy'])
@pytest.mark.parametrize('case', ['trailing_invalid', 'single_line_fence', 'chat_json',
                                 'chat_late_refusal', 'chat_late_korean'])
def test_provider_validates_whole_response_before_display_bounding(monkeypatch, provider, case):
    structured = case in {'trailing_invalid', 'single_line_fence'}
    responses = {
        'trailing_invalid': '```json\n{"selected":[]}\n```' + ' ' * 6000 + '{"broken":',
        'single_line_fence': '```json ' + json.dumps({'body': 'x' * 7000}) + '```',
        'chat_json': json.dumps({'body': '한국어 ' * 2000}, ensure_ascii=False),
        'chat_late_refusal': '한국어 응답 ' + 'x' * 6500 + ' cannot assist',
        'chat_late_korean': 'x' * 6500 + ' 한국어 응답',
    }
    response = responses[case]

    def runner(command, **kwargs):
        if '--output-last-message' in command:
            Path(command[command.index('--output-last-message') + 1]).write_text(response)
        return SimpleNamespace(returncode=0, stdout=response)

    if provider == 'agy':
        from lib import llm_cli
        monkeypatch.setattr(llm_cli, 'backup_chat', lambda *a, **kw: (response, 'test'))
        result = agent._try_agy_backup('한국어 입력', structured_output=structured)
    else:
        monkeypatch.setenv('AGENT_CONSOLE_' + provider.upper() + '_ENABLED', '1')
        result = getattr(agent, '_try_' + provider + '_chat')(
            '한국어 입력', runner=runner, structured_output=structured)
    # 완결된 JSON 객체 채팅 응답(chat_json)은 원문 전체를 보존한다 — 표시 상한은 answer 필드에만
    # 적용된다(_normalize_answer_payload). 일반 문자열 채팅만 6000자로 자른다.
    expected = None if case in {'trailing_invalid', 'chat_late_refusal'} else (
        response if structured or case == 'chat_json' else response[:6000])
    assert result == expected


def _chat_provider_result(monkeypatch, provider, response, prompt='한국어 질문'):
    def runner(command, **kwargs):
        if '--output-last-message' in command:
            Path(command[command.index('--output-last-message') + 1]).write_text(
                response, encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=response)

    if provider == 'agy':
        from lib import llm_cli
        monkeypatch.setattr(llm_cli, 'backup_chat', lambda *a, **kw: (response, 'test'))
        return agent._try_agy_backup(prompt)
    monkeypatch.setenv('AGENT_CONSOLE_' + provider.upper() + '_ENABLED', '1')
    return getattr(agent, '_try_' + provider + '_chat')(prompt, runner=runner)


@pytest.mark.parametrize('provider', ['codex', 'hermes', 'gemini', 'agy'])
def test_long_chat_answer_contract_keeps_json_and_trailing_citation(monkeypatch, provider):
    """D-01: 6,000자를 넘는 정상 {answer, cited_evidence_ids} 가 잘려 원시 JSON 조각이 답변이 되면 안 된다."""
    response = json.dumps({'answer': '한국어 답변입니다. ' * 800,
                           'cited_evidence_ids': ['event-one']}, ensure_ascii=False)
    assert len(response) > 6000

    raw = _chat_provider_result(monkeypatch, provider, response)
    payload = agent._normalize_answer_payload(raw, {'event-one'})

    assert payload['structured'] is True
    assert payload['cited_evidence_ids'] == ['event-one']
    assert payload['answer'].startswith('한국어 답변입니다.')
    assert '{"answer"' not in payload['answer']
    assert len(payload['answer']) == 6000                 # 표시 상한은 answer 필드에만


@pytest.mark.parametrize('provider', ['codex', 'hermes', 'gemini', 'agy'])
@pytest.mark.parametrize('response', [
    '{"answer": "한국어 답변이 중간에서 잘렸', '```json\n{"answer": "한국어 답변", "cited_evidence_ids": [',
], ids=['cut_object', 'cut_fence'])
def test_partial_chat_json_is_not_promoted_to_legacy_answer(monkeypatch, provider, response):
    """D-01: 깨진 JSON 은 문자열 답변으로 채택하지 않고 다음 provider/정직한 폴백으로 넘긴다."""
    assert _chat_provider_result(monkeypatch, provider, response) is None


def test_structured_validation_does_not_initialize_distillation_runtime(tmp_path):
    script = '''
from agent_console import agent
import dotenv, logging, sys
sys.modules.pop("reports.wiki_distillation", None)
calls = []
dotenv.load_dotenv = lambda *a, **kw: calls.append("dotenv")
logging.basicConfig = lambda *a, **kw: calls.append("logging")
assert agent._is_usable_llm_output("한국어 입력", '{"selected":[]}', structured_output=True)
assert not calls, calls
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
