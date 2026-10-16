import pytest

from agent_console import wiki


@pytest.mark.parametrize('knowledge', [
    {'claims': [{'text': 'hiddenchipshortageevidence', 'scope': 'Customer orders'}]},
    {'limitations': ['hiddenchipshortageevidence']},
    {'relations': [{'scope_explanation': 'hiddenchipshortageevidence'}]},
])
def test_structured_only_evidence_is_ranked_for_its_content(knowledge):
    target = {'id': 'target', 'title': 'Neutral note', 'source_knowledge': knowledge}
    unrelated = {'id': 'unrelated', 'title': 'Another neutral note'}
    pages = wiki._fallback_ranked_pages([unrelated, target], query='hiddenchipshortageevidence',
                                       surface='all', status='all', limit=1)
    assert pages[0]['id'] == 'target'


def test_full_prose_body_is_retrievable_not_only_title_and_summary():
    target = {'id': 'target', 'title': 'Neutral', 'body': 'hiddenchipshortageevidence'}
    pages = wiki._fallback_ranked_pages([{'id': 'other', 'title': 'Neutral'}, target],
                                       query='hiddenchipshortageevidence', surface='all', status='all', limit=1)
    assert pages[0]['id'] == 'target'


def test_private_metadata_does_not_increase_search_score():
    baseline = {'id': 'target', 'title': 'Neutral'}
    private = {**baseline, 'source_knowledge': {
        'private_notes': ['hiddenchipshortageevidence'],
        'claims': [{'text': {'private_notes': 'hiddenchipshortageevidence'}}]}}
    assert wiki._candidate_score(private, 'hiddenchipshortageevidence', 'all', 'all', include_document_content=True) == wiki._candidate_score(baseline, 'hiddenchipshortageevidence', 'all', 'all', include_document_content=True)


def test_search_content_does_not_change_automatic_curation_score():
    baseline = {'id': 'target', 'title': 'Neutral'}
    content = {**baseline, 'body': 'hiddenchipshortageevidence',
               'source_knowledge': {'claims': [{'text': 'hiddenchipshortageevidence'}]}}
    assert wiki._candidate_score(content, 'hiddenchipshortageevidence', 'all', 'all') == wiki._candidate_score(baseline, 'hiddenchipshortageevidence', 'all', 'all')


def test_no_status_match_does_not_return_other_statuses():
    pages = wiki._fallback_ranked_pages([{'id': 'draft', 'title': 'Needle'}],
                                       query='Needle', surface='all', status='archived', limit=5)
    assert pages == []


def test_surface_filter_cannot_be_overridden_by_knowledge_match():
    pages = wiki._fallback_ranked_pages([{'id': 'market', 'title': 'Needle',
                                         'source': {'surface': 'market'}}],
                                       query='Needle', surface='portfolio', status='all', limit=5)
    assert pages == []
