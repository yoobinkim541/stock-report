"""safe_io.atomic_write_text 회귀 테스트.

JSONL 전체 재작성을 원자적으로 하기 위한 헬퍼. 쓰기 도중 죽어도 원본이
온전해야 한다(temp→rename). 기존 atomic_write_json 은 JSON 전용이라 못 쓴다.
"""
import pytest

import safe_io


def test_atomic_write_text_creates_file(tmp_path):
    target = tmp_path / "out.jsonl"
    safe_io.atomic_write_text(str(target), '{"a":1}\n{"b":2}\n')
    assert target.read_text(encoding="utf-8") == '{"a":1}\n{"b":2}\n'


def test_atomic_write_text_replaces_existing(tmp_path):
    target = tmp_path / "out.jsonl"
    target.write_text("old\n", encoding="utf-8")
    safe_io.atomic_write_text(str(target), "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"


def test_atomic_write_text_leaves_original_on_failure(tmp_path, monkeypatch):
    """rename 직전에 터져도 원본이 남고 temp 는 청소된다."""
    target = tmp_path / "out.jsonl"
    target.write_text("original\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("디스크 오류 흉내")

    monkeypatch.setattr(safe_io.os, "replace", boom)
    with pytest.raises(RuntimeError):
        safe_io.atomic_write_text(str(target), "replacement\n")

    assert target.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob("*.tmp")) == []
