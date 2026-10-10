from pathlib import Path

import pytest

from agent_console import wiki_contract


def test_schema_defines_the_source_and_maintenance_contract():
    text = wiki_contract.maintenance_schema()
    for rule in ("raw", "source_refs", "contradiction", "append-only", "draft", "index.md"):
        assert rule in text
    assert len(text) < 6000


def test_raw_document_reader_preserves_original_and_returns_complete_text(tmp_path):
    path = tmp_path / "report.txt"
    content = "First fact.\n\nCounterevidence and conditions.\n"
    path.write_text(content)
    before = path.read_bytes()
    result = wiki_contract.read_raw_document(path, roots=[tmp_path])
    assert result["text"] == content
    assert result["sha256"]
    assert result["complete"] is True
    assert path.read_bytes() == before


def test_outside_root_and_symlink_sources_are_not_read(tmp_path):
    allowed = tmp_path / "raw"
    allowed.mkdir()
    other = tmp_path / "private.txt"
    other.write_text("not an approved source")
    (allowed / "escape.txt").symlink_to(other)
    for path in (other, allowed / "escape.txt"):
        with pytest.raises(ValueError, match="outside"):
            wiki_contract.read_raw_document(path, roots=[allowed])


def test_oversized_empty_and_non_text_sources_fail_closed(tmp_path):
    for content in (b"", b"x" * 101, b"\xff\xfe"):
        path = tmp_path / "report.txt"
        path.write_bytes(content)
        with pytest.raises(ValueError):
            wiki_contract.read_raw_document(path, roots=[tmp_path], max_bytes=100)


def test_remote_urls_are_not_treated_as_local_sources(tmp_path):
    with pytest.raises(ValueError):
        wiki_contract.read_raw_document("https://example.com/report", roots=[tmp_path])
