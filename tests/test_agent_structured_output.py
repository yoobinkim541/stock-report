import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_console import agent


@pytest.mark.parametrize('provider', ['codex', 'hermes', 'gemini', 'agy'])
@pytest.mark.parametrize('fenced', [False, True])
def test_long_structured_response_is_not_cut_before_wiki_parser(monkeypatch, provider, fenced):
    payload = {'action': 'create', 'kind': 'concept', 'title': '원문 기반 지식',
               'body': '원문 핵심 근거와 적용 예외. ' * 700 + '마지막 근거 [S1]'}
    response = json.dumps(payload, ensure_ascii=False)
    if fenced:
        response = '```json\n' + response + '\n```'
    assert len(response) > 6000

    def runner(command, **kwargs):
        if '--output-last-message' in command:
            Path(command[command.index('--output-last-message') + 1]).write_text(response)
        return SimpleNamespace(returncode=0, stdout=response)

    if provider == 'agy':
        from lib import llm_cli
        monkeypatch.setattr(llm_cli, 'backup_chat', lambda *a, **kw: (response, 'test'))
        result = agent._try_agy_backup('JSON만 반환하세요', structured_output=True)
    else:
        monkeypatch.setenv('AGENT_CONSOLE_' + provider.upper() + '_ENABLED', '1')
        result = getattr(agent, '_try_' + provider + '_chat')(
            'JSON만 반환하세요', runner=runner, structured_output=True)
    assert result == response


def test_ordinary_chat_retains_existing_display_limit():
    response = '일반 대화 응답입니다. ' * 1000
    assert agent._bounded_llm_output(response) == response[:6000]


def test_oversized_structured_response_fails_instead_of_becoming_partial_json():
    response = json.dumps({'body': 'x' * 100_001})
    assert agent._bounded_llm_output(response) is None
