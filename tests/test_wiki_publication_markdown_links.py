import pytest

from agent_console import wiki_publication as publication
from test_wiki_publication_bracket_links import row, snapshot


@pytest.mark.parametrize('body', [
    '[Document][ref]\n\n[ref]: absent.md',
    '[ref][]\n\n[ref]: absent.md',
    '[ref]\n\n[ref]: absent.md',
    '![Asset][ref]\n\n[ref]: absent.png',
])
def test_reference_style_internal_targets_must_exist(body):
    files = publication.render_candidate(snapshot(row('source', body=body)))
    result = publication.validate_candidate(files)
    assert not result['ok']
    assert any(error['code'] == 'missing_markdown_link' for error in result['errors'])


def test_valid_relative_reference_link_resolves_from_note_directory():
    files = publication.render_candidate(snapshot(
        row('source', body='[Target][ref]\n\n[ref]: target.md'), row('target')))
    assert publication.validate_candidate(files)['ok']


def test_reference_example_in_code_does_not_create_target_requirement():
    files = publication.render_candidate(snapshot(row('source', body=(
        '```md\n[Document][ref]\n\n[ref]: absent.md\n```'))))
    assert publication.validate_candidate(files)['ok']
