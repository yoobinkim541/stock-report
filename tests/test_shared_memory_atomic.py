"""쓰기 원자성·재진입 회귀 테스트.

flock 은 재진입 불가라, 락 안에서 다시 락을 잡으면 LockTimeout 으로 죽는다.
append/delete/upsert 가 정상 동작하는 것 자체가 '중첩 락 없음'의 증거다.
"""
import json
from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "shared-memory"))


def test_append_and_delete_do_not_deadlock(monkeypatch, tmp_path):
    from agent_console import shared_memory

    _isolate(monkeypatch, tmp_path)
    rec = shared_memory.append_record({"title": "a", "summary": "b", "tags": ["chat"]})
    assert shared_memory.delete_record(rec["id"]) is True
    assert shared_memory.delete_record(rec["id"]) is False


def test_upsert_record_replaces_in_place(monkeypatch, tmp_path):
    from agent_console import shared_memory

    _isolate(monkeypatch, tmp_path)
    first = shared_memory.append_record({"title": "v1", "summary": "s1", "tags": ["wiki"]})
    updated = dict(first)
    updated["summary"] = "s2"
    shared_memory.upsert_record(updated)

    rows = shared_memory.all_records()
    matching = [r for r in rows if r.get("id") == first["id"]]
    assert len(matching) == 1
    assert matching[0]["summary"] == "s2"


def test_upsert_record_appends_when_new(monkeypatch, tmp_path):
    from agent_console import shared_memory

    _isolate(monkeypatch, tmp_path)
    shared_memory.upsert_record({
        "id": "brand-new-id", "title": "새 페이지", "summary": "s", "tags": ["wiki"],
        "createdAt": "2026-07-22T00:00:00+00:00", "updatedAt": "2026-07-22T00:00:00+00:00",
    })
    ids = [r.get("id") for r in shared_memory.all_records()]
    assert "brand-new-id" in ids


def test_index_written_atomically(monkeypatch, tmp_path):
    """쓰기 후 index.json 이 항상 파싱 가능한 완전한 JSON 이어야 한다."""
    from agent_console import shared_memory

    _isolate(monkeypatch, tmp_path)
    shared_memory.append_record({"title": "x", "summary": "y", "tags": ["chat"]})
    index_path = Path(shared_memory.shared_memory_dir()) / "index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["recordCount"] == 1
    assert list(tmp_path.rglob("*.tmp")) == []
