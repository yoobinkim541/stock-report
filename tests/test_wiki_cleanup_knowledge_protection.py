import pytest
import json

from agent_console import wiki
from reports import wiki_health_check


@pytest.mark.parametrize('status', ['draft', 'archived'])
@pytest.mark.parametrize('field', ['claims', 'limitations', 'relations', 'integrations'])
@pytest.mark.parametrize('dry_run', [True, False])
def test_age_cleanup_protects_structured_only_knowledge(monkeypatch, status, field, dry_run):
    page = {'id': 'knowledge-note', 'kind': 'note', 'status': status,
            'body': '', 'summary': '', 'source_knowledge': {field: ['durable evidence']}}
    monkeypatch.setattr(wiki, '_all_wiki_pages', lambda: [page])
    monkeypatch.setattr(wiki, '_is_page_stale', lambda *args: True)
    def forbidden(*args, **kwargs):
        pytest.fail('protected knowledge must not be written or deleted')
    monkeypatch.setattr(wiki, 'delete_page', forbidden)
    monkeypatch.setattr(wiki, 'upsert_page', forbidden)
    monkeypatch.setattr(wiki, 'rebuild_artifacts', forbidden)
    result = wiki.archive_stale_pages(dry_run=dry_run)
    assert result['archived'] == result['deleted'] == 0


def test_llm_health_delete_cannot_remove_structured_knowledge(monkeypatch):
    monkeypatch.setattr('sys.argv', ['wiki-health'])
    monkeypatch.setattr(wiki_health_check, 'build_health_report', lambda **kw: {})
    monkeypatch.setattr(wiki_health_check, 'run_llm_health_review',
                        lambda _: [{'action': 'delete', 'page_id': 'protected'}])
    monkeypatch.setattr(wiki_health_check, 'format_report', lambda _: '')
    monkeypatch.setattr(wiki, 'get_page', lambda _: {'id': 'protected', 'source_knowledge': {'claims': ['evidence']}})
    deleted = []
    monkeypatch.setattr(wiki, 'delete_page', deleted.append)
    wiki_health_check.main()
    assert deleted == []


def test_chat_curation_cannot_delete_structured_only_page(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_CONSOLE_SHARED_MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setattr(wiki, '_debounced_rebuild', lambda: None)
    wiki._CACHE.clear()
    page = wiki.upsert_page({'id': 'structured-note', 'title': 'Capacity evidence',
                            'body': 'Capacity conditions', 'surface': 'market', 'kind': 'note',
                            'source_knowledge': {'limitations': ['Orders remain uncertain']}})
    result = wiki.auto_curate_from_chat(
        '위키 페이지 삭제 기준을 정리해줘. 오래된 메모는 지워도 될까?',
        '네, 아래 기준에 맞으면 삭제합니다.\n- 30일 이상 갱신 안 됨\n- 최신 리포트와 모순\n- 중복 내용\n검증 결과 이 페이지는 삭제 대상입니다.',
        surface='market', llm=lambda _: json.dumps({'action': 'delete', 'target_id': page['id'], 'reason': 'stale'}),
        pack={'focus': []}, history=[])
    assert result['action'] == 'skipped_injection_guard'
    assert wiki.get_page(page['id']) is not None
