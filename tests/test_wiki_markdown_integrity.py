from pathlib import Path

import yaml
import pytest

from agent_console import qmd_search
from agent_console import wiki


def test_index_targets_exported_file_ids_when_titles_collide(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_CONSOLE_QMD_WIKI_DIR', str(tmp_path / 'markdown'))
    pages = [{'id': identity, 'title': 'Shared title', 'body': 'Independent knowledge'}
             for identity in ['first-note', 'second-note']]
    files = qmd_search.export_pages(pages)['files']
    index = wiki._render_index_md(pages)
    for filename in files:
        assert f'[[{Path(filename).stem}|Shared title]]' in index
    assert '[[Shared title]]' not in index


def test_index_target_stays_stable_after_title_change():
    before = wiki._render_index_entry({'id': 'stable-note', 'title': 'Old headline'})
    after = wiki._render_index_entry({'id': 'stable-note', 'title': 'Short title'})
    assert '[[stable-note|Old headline]]' in before
    assert '[[stable-note|Short title]]' in after


def test_index_matches_exported_long_id(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_CONSOLE_QMD_WIKI_DIR', str(tmp_path / 'markdown'))
    page = {'id': 'x' * 130, 'title': 'Long identity', 'body': 'Knowledge'}
    filename = qmd_search.export_pages([page])['files'][0]
    assert f'[[{Path(filename).stem}|Long identity]]' in wiki._render_index_entry(page)


def test_index_labels_cannot_close_links_or_create_extra_lines():
    entry = wiki._render_index_entry({'id': 'safe-note', 'title': 'Notes ]] appendix |\n details',
                                     'summary': 'First paragraph.\nSecond paragraph.'})
    assert entry.count('[[') == entry.count(']]') == 1
    assert entry.count('|') == 1
    assert '\n' not in entry
    assert 'Notes' in entry and 'appendix' in entry and 'details' in entry


def test_index_without_identity_does_not_invent_a_link():
    assert '[[' not in wiki._render_index_entry({'title': 'Unidentified note'})


def test_split_overview_uses_child_identity_not_ambiguous_title():
    children = [{'id': identity, 'title': 'Same ]] heading | details', 'summary': 'Evidence'}
                for identity in ('first-child', 'second-child')]
    overview = wiki._split_body_overview('Parent', children)
    assert '[[first-child|Same heading details]]' in overview
    assert '[[second-child|Same heading details]]' in overview
    assert overview.count('[[') == overview.count(']]') == 2


def test_split_overview_without_identity_is_plain_text():
    assert '[[' not in wiki._split_body_overview('Parent', [{'title': 'Unidentified child'}])


def test_markdown_filename_collision_cannot_overwrite_existing_exports(monkeypatch, tmp_path):
    directory = tmp_path / 'markdown'
    monkeypatch.setenv('AGENT_CONSOLE_QMD_WIKI_DIR', str(directory))
    qmd_search.export_pages([{'id': 'original', 'title': 'Original', 'body': 'Preserve this knowledge'}])
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(ValueError, match='wiki_export_filename_collision'):
        qmd_search.export_pages([
            {'id': 'ambiguous/id', 'title': 'First', 'body': 'First knowledge'},
            {'id': 'ambiguous:id', 'title': 'Second', 'body': 'Second knowledge'},
        ])
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_sequential_collision_preserves_first_document(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_CONSOLE_QMD_WIKI_DIR', str(tmp_path / 'markdown'))
    filename = Path(qmd_search.export_pages([{'id': 'ambiguous/id', 'body': 'First knowledge'}])['files'][0])
    before = filename.read_bytes()
    with pytest.raises(ValueError, match='wiki_export_filename_collision'):
        qmd_search.export_pages([{'id': 'ambiguous:id', 'body': 'Second knowledge'}])
    assert filename.read_bytes() == before


def test_same_identity_can_update_title_and_body(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENT_CONSOLE_QMD_WIKI_DIR', str(tmp_path / 'markdown'))
    before = qmd_search.export_pages([{'id': 'stable-note', 'title': 'Old', 'body': 'Before'}])
    after = qmd_search.export_pages([{'id': 'stable-note', 'title': 'New', 'body': 'After'}])
    assert before['files'] == after['files']
    assert 'After' in Path(after['files'][0]).read_text()


def test_markdown_mirror_preserves_long_body_and_paragraph_boundaries(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(tmp_path / "markdown"))
    body = "## Original heading\n\n" + "A complete evidence paragraph.\n\n" * 600 + "END OF ORIGINAL BODY"
    page = {"id": "long-knowledge", "title": "Long knowledge", "body": body,
            "source_refs": ["https://example.com/source"]}
    result = qmd_search.export_pages([page])
    markdown = Path(result["files"][0]).read_text()
    exported_body = markdown.split("## Body\n\n", 1)[1].split("\n\n## Source Refs", 1)[0]
    assert exported_body == body


def test_markdown_mirror_exports_resolvable_relationships_and_title_alias(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_QMD_WIKI_DIR", str(tmp_path / "markdown"))
    pages = [
        {"id": "parent-id", "title": "Parent knowledge", "body": "Overview", "links": ["child-id"]},
        {"id": "child-id", "title": "Child knowledge", "body": "Detailed knowledge",
         "links": ["parent-id"], "parent_page_id": "parent-id"},
    ]
    result = qmd_search.export_pages(pages)
    contents = {Path(path).stem: Path(path).read_text() for path in result["files"]}
    assert "[[child-id]]" in contents["parent-id"]
    assert "[[parent-id]]" in contents["child-id"]
    for page in pages:
        frontmatter = yaml.safe_load(contents[page["id"]].split("---", 2)[1])
        assert page["title"] in frontmatter["aliases"]
