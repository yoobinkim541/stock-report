# 위키 저장 계층 강화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 위키가 레코드 수와 무관하게 전체 페이지를 조회·갱신하게 만들고, `events.jsonl`·`index.json` 쓰기를 원자적으로 만들어 두 writer 를 직렬화한다.

**Architecture:** `shared_memory` 에 클램프 없는 `all_records()` 를 추가해 위키 조회 경로를 옮기고, 기존 `list_records(limit, offset)` 페이지네이션 계약은 그대로 둔다. 쓰기는 `safe_io.file_write_lock` 사이드카 락으로 `agent_console/shared_memory.py` 와 `lib/agent_memory.py` 를 직렬화하고, 전체 재작성은 `safe_io.atomic_write_text` 의 temp→fsync→rename 으로 바꾼다.

**Tech Stack:** Python 3.11, pytest, fcntl flock (`safe_io.py`), JSONL 저장소

## Global Constraints

- **flock 은 재진입 불가.** 같은 프로세스가 락을 잡은 채 다시 `file_write_lock()` 을 호출하면 `LOCK_NB` 재시도가 타임아웃까지 돌다 `LockTimeout` 이 난다. 락 안에서 부르는 내부 함수는 **절대 락을 다시 잡지 않는다**. 이름에 `_locked` 접미사를 붙여 계약을 드러낸다.
- `refresh_context_memory_summary()` 는 **락 밖에서** 호출한다. `lib.agent_memory.refresh_memory_summary` → `run_due_compression` 으로 이어지며 다른 파일(`STATE_PATH`, `SUMMARY_PATH`)을 쓰므로 락 보유 시간을 늘릴 이유가 없다.
- `list_records(limit, offset)` 의 시그니처와 클램프(최대 100)는 **변경하지 않는다**. 호출자: `shared_memory.py:290`, `:416`, `:533`.
- `agent_console/wiki.py` 의 `_is_wiki_record` 등 위키 판별 로직은 `wiki.py` 에 남긴다. `shared_memory` 로 옮기지 않는다.
- `lib/agent_memory._append_event` 의 **best-effort 계약을 유지**한다 — 기존처럼 예외를 `logger.warning` 으로 삼킨다(조용한 무음이 아니라 로그에 남는다). 반면 `shared_memory` 의 쓰기 경로는 `LockTimeout` 을 **호출자에게 전파**한다.
- `index.json` 의 키 소유권: `shared_memory` = `ok`, `schemaVersion`, `updatedAt`, `recordCount`, `latestRecordAt`, `records` / `lib.agent_memory` = `latestAt`, `latestTitle`, `count`. 양쪽 모두 **상대 키를 읽어서 보존**한다.
- 테스트는 반드시 저장소를 격리한다. `shared_memory` 는 `monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", ...)`, `lib.agent_memory` 는 import 시점에 경로가 굳으므로 `monkeypatch.setattr(agent_memory, "EVENTS_PATH", ...)` 방식을 쓴다(`tests/test_agent_memory.py:26` 관례).
- 회귀 판단은 실패 **개수**가 아니라 **목록 대조**로 한다.

---

### Task 1: `safe_io.atomic_write_text`

**Files:**
- Modify: `safe_io.py` (`atomic_write_json` 아래에 추가)
- Test: `tests/test_safe_io_text.py`

**Interfaces:**
- Consumes: 없음
- Produces: `safe_io.atomic_write_text(path: str, text: str) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_safe_io_text.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `./.venv/bin/pytest tests/test_safe_io_text.py -q`
Expected: FAIL — `AttributeError: module 'safe_io' has no attribute 'atomic_write_text'`

- [ ] **Step 3: 구현 추가**

`safe_io.py` 의 `atomic_write_json` 함수 바로 아래에 삽입:

```python
def atomic_write_text(path: str, text: str) -> None:
    """text 를 path 에 원자적으로 기록 (temp→fsync→rename). 실패 시 원본 보존.

    JSONL 처럼 JSON 이 아닌 텍스트 전체 재작성용. atomic_write_json 과 같은
    보장을 준다 — 독자는 항상 옛/새 중 '완전한' 파일만 본다.
    """
    path = os.path.abspath(path)
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `./.venv/bin/pytest tests/test_safe_io_text.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add safe_io.py tests/test_safe_io_text.py
git commit -m "add) JSONL 전체 재작성을 위한 원자적 텍스트 쓰기 헬퍼"
```

---

### Task 2: 위키 읽기 창 제거 (`all_records`)

**Files:**
- Modify: `agent_console/shared_memory.py` (`list_records` 아래에 `all_records` 추가)
- Modify: `agent_console/wiki.py:193`, `:216`, `:223`
- Test: `tests/test_wiki_storage_window.py`

**Interfaces:**
- Consumes: 없음
- Produces: `shared_memory.all_records() -> list[dict]` (createdAt 내림차순, 클램프 없음)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_wiki_storage_window.py`:

```python
"""위키가 최근 100 레코드 창에 갇히던 회귀를 고정한다.

shared_memory.list_records 는 limit 을 100 으로 클램프한다. 위키는 지식층이라
전수를 봐야 하므로 all_records() 를 쓴다. 이 테스트는 '100번째보다 오래된
위키 페이지'가 조회·통계·갱신에서 살아있는지 확인한다.
"""
from pathlib import Path

import pytest


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "shared-memory"))


def _seed(count: int, surface: str = "market") -> None:
    """채팅 레코드를 count 건 쌓아 위키 페이지를 100 창 밖으로 밀어낸다."""
    from agent_console import shared_memory

    for i in range(count):
        shared_memory.append_record({
            "title": f"chat-{i:04d}",
            "summary": f"본문 {i}",
            "tags": ["chat"],
            "source": {"surface": surface, "screen": surface},
        })


def test_old_wiki_page_survives_beyond_100_records(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    page = wiki.upsert_page({
        "title": "오래된 손실한도 규칙",
        "surface": "portfolio",
        "kind": "playbook",
        "status": "reviewed",
        "summary": "손실한도 1% 규칙",
        "body": "손실한도는 1% 로 고정한다.",
    })
    page_id = page["id"]

    _seed(150)   # 위키 페이지를 최근 100 창 밖으로 밀어냄

    assert wiki.get_page(page_id) is not None, "get_page 가 오래된 페이지를 못 찾음"
    titles = [p["title"] for p in wiki.list_pages(limit=50)]
    assert "오래된 손실한도 규칙" in titles, "list_pages 에서 사라짐"
    assert wiki.stats()["total"] >= 1, "stats 가 오래된 페이지를 못 셈"


def test_upsert_old_page_does_not_duplicate_id(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import json

    from agent_console import shared_memory, wiki

    page = wiki.upsert_page({
        "title": "오래된 손실한도 규칙",
        "surface": "portfolio",
        "kind": "playbook",
        "summary": "v1",
        "body": "v1 본문",
    })
    page_id = page["id"]

    _seed(150)

    wiki.upsert_page({
        "id": page_id,
        "title": "오래된 손실한도 규칙",
        "surface": "portfolio",
        "kind": "playbook",
        "summary": "v2",
        "body": "v2 본문",
    })

    events = Path(shared_memory.shared_memory_dir()) / "events.jsonl"
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    matching = [r for r in rows if r.get("id") == page_id]
    assert len(matching) == 1, f"같은 id 레코드가 {len(matching)}개 — 중복 누적"
    assert matching[0]["summary"] == "v2"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `./.venv/bin/pytest tests/test_wiki_storage_window.py -q`
Expected: FAIL — 첫 테스트는 `get_page 가 오래된 페이지를 못 찾음`, 둘째는 중복 id 로 실패.

- [ ] **Step 3: `all_records` 추가**

`agent_console/shared_memory.py` 의 `list_records` 정의 **바로 아래**에 삽입:

```python
def all_records() -> list[dict]:
    """전체 레코드를 createdAt 내림차순으로 반환한다. 창(window) 없음.

    list_records() 는 limit/offset 페이지네이션 계약이라 최대 100 으로 클램프한다.
    위키 같은 지식층은 '전수'가 필요하므로 이 함수를 쓴다. _read_jsonl 이 어차피
    파일 전체를 읽으므로 추가 I/O 비용은 없다.
    """
    ensure_store()
    rows = _read_jsonl(_paths()["events"])
    rows.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    return rows
```

- [ ] **Step 4: 위키 조회 경로 3곳 교체**

`agent_console/wiki.py:193` (`list_pages` 안):

```python
        rows = shared_memory.all_records()
```

`agent_console/wiki.py:216` (`get_page` 안):

```python
    for row in shared_memory.all_records():
```

`agent_console/wiki.py:223` (`stats` 안):

```python
    rows = [row for row in shared_memory.all_records() if _is_wiki_record(row)]
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `./.venv/bin/pytest tests/test_wiki_storage_window.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: 커밋**

```bash
git add agent_console/shared_memory.py agent_console/wiki.py tests/test_wiki_storage_window.py
git commit -m "fix) 위키가 최근 100 레코드 창에 갇히던 문제 해결"
```

---

### Task 3: `shared_memory` 쓰기 직렬화 + 원자적 재작성

**Files:**
- Modify: `agent_console/shared_memory.py:139-147` (`append_record`), `:254-270` (`delete_record`), `:272-285` (`_write_index`)
- Modify: `agent_console/wiki.py:287-290` (`upsert_page`)
- Test: `tests/test_shared_memory_atomic.py`

**Interfaces:**
- Consumes: `safe_io.atomic_write_text(path: str, text: str) -> None` (Task 1)
- Produces:
  - `shared_memory.upsert_record(record: dict) -> dict`
  - `shared_memory._events_lock()` — context manager
  - `shared_memory._write_index_locked() -> None` — **락 보유 중에만 호출**

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_shared_memory_atomic.py`:

```python
"""쓰기 원자성·재진입 회귀 테스트.

flock 은 재진입 불가라, 락 안에서 다시 락을 잡으면 LockTimeout 으로 죽는다.
append/delete/upsert 가 정상 동작하는 것 자체가 '중첩 락 없음'의 증거다.
"""
from pathlib import Path

import pytest


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
    import json

    from agent_console import shared_memory

    _isolate(monkeypatch, tmp_path)
    shared_memory.append_record({"title": "x", "summary": "y", "tags": ["chat"]})
    index_path = Path(shared_memory.shared_memory_dir()) / "index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["recordCount"] == 1
    assert list(tmp_path.rglob("*.tmp")) == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `./.venv/bin/pytest tests/test_shared_memory_atomic.py -q`
Expected: FAIL — `AttributeError: module 'agent_console.shared_memory' has no attribute 'upsert_record'` (2건). 나머지 2건은 현재도 통과할 수 있다.

- [ ] **Step 3: import 와 락 헬퍼 추가**

`agent_console/shared_memory.py` 상단 import 블록에 추가:

```python
import safe_io
```

`_read_jsonl` 정의 **위**에 삽입:

```python
def _events_lock():
    """events.jsonl 사이드카 락.

    lib/agent_memory 도 같은 경로로 이 락을 잡으므로 두 모듈의 쓰기가 직렬화된다.
    주의: flock 은 재진입 불가 — 이 블록 안에서 다시 _events_lock() 을 호출하면
    LockTimeout 이 난다. 내부 호출은 _write_index_locked() 처럼 락을 잡지 않는
    버전을 쓴다.
    """
    return safe_io.file_write_lock(str(_paths()["events"]), timeout=30.0)


def _write_jsonl_locked(rows: list[dict]) -> None:
    """전체 재작성 — 락 보유 중에만 호출."""
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    safe_io.atomic_write_text(str(_paths()["events"]), text)
```

- [ ] **Step 4: `_write_index` 를 락 비획득 버전으로 전환**

`agent_console/shared_memory.py:272` 의 `def _write_index() -> None:` 을 다음으로 교체한다. 이름이 바뀌므로 호출부(144행·267행)도 Step 5·6 에서 함께 바꾼다.

```python
def _write_index_locked() -> None:
    """index.json 갱신 — 락 보유 중에만 호출.

    lib/agent_memory 가 같은 파일에 latestAt/latestTitle/count 를 쓰므로,
    기존 내용을 읽어 자기 키만 덮어쓰고 상대 키는 보존한다.
    """
    paths = _paths()
    rows = _read_jsonl(paths["events"])
    rows.sort(key=lambda row: row.get("createdAt") or "", reverse=True)
    latest = rows[:200]
    try:
        existing = json.loads(paths["index"].read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}
    existing.update({
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": _now(),
        "recordCount": len(rows),
        "latestRecordAt": latest[0].get("createdAt") if latest else "",
        "records": latest,
    })
    safe_io.atomic_write_json(str(paths["index"]), existing)
```

- [ ] **Step 5: `append_record` 를 락 안으로**

`agent_console/shared_memory.py:139-147` 의 `append_record` 본문을 교체:

```python
def append_record(payload: dict) -> dict:
    ensure_store()
    record = normalize_record(payload)
    with _events_lock():
        with _paths()["events"].open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        _write_index_locked()
    refresh_context_memory_summary()   # 락 밖 — 다른 파일을 쓰고 오래 걸린다
    return record
```

- [ ] **Step 6: `delete_record` 를 원자적 재작성으로**

`agent_console/shared_memory.py:254-270` 의 `delete_record` 본문을 교체:

```python
def delete_record(record_id: str) -> bool:
    ensure_store()
    record_id = str(record_id or "").strip()
    if not record_id:
        return False
    with _events_lock():
        rows = _read_jsonl(_paths()["events"])
        kept = [row for row in rows if row.get("id") != record_id]
        if len(kept) == len(rows):
            return False
        _write_jsonl_locked(kept)
        _write_index_locked()
    refresh_context_memory_summary()
    return True
```

- [ ] **Step 7: `upsert_record` 추가**

`delete_record` 정의 **아래**에 삽입:

```python
def upsert_record(record: dict) -> dict:
    """id 기준 치환-또는-추가를 한 번의 원자적 재작성으로 수행한다.

    delete_record + append_record 조합은 그 사이에 죽으면 레코드가 사라지는
    창이 있었다. 이 함수는 그 창을 없앤다.
    """
    ensure_store()
    normalized = normalize_record(record)
    record_id = str(normalized.get("id") or "").strip()
    if not record_id:
        return append_record(record)
    with _events_lock():
        rows = _read_jsonl(_paths()["events"])
        kept = [row for row in rows if row.get("id") != record_id]
        kept.append(normalized)
        _write_jsonl_locked(kept)
        _write_index_locked()
    refresh_context_memory_summary()
    return normalized
```

- [ ] **Step 8: `wiki.upsert_page` 를 `upsert_record` 로**

`agent_console/wiki.py:287-290` 의 아래 블록을

```python
    if existing and existing.get("id"):
        shared_memory.delete_record(page_id)
    saved = shared_memory.append_record(record)
    return _record_to_page(saved)
```

다음으로 교체:

```python
    saved = shared_memory.upsert_record(record)
    return _record_to_page(saved)
```

- [ ] **Step 9: 테스트 통과 확인**

Run: `./.venv/bin/pytest tests/test_shared_memory_atomic.py tests/test_wiki_storage_window.py -q`
Expected: PASS (6 passed)

- [ ] **Step 10: 커밋**

```bash
git add agent_console/shared_memory.py agent_console/wiki.py tests/test_shared_memory_atomic.py
git commit -m "fix) 공유 메모리 쓰기를 락·원자적 재작성으로 보호"
```

---

### Task 4: `lib/agent_memory` 를 같은 락에 편입

**Files:**
- Modify: `lib/agent_memory.py:87-91` (`_write_text_atomic`), `:414-427` (`_append_event`)
- Test: `tests/test_shared_memory_concurrency.py`

**Interfaces:**
- Consumes: `safe_io.file_write_lock`, `safe_io.atomic_write_text` (Task 1), `shared_memory.append_record` / `delete_record` (Task 3)
- Produces: 없음

- [ ] **Step 1: 실패하는 동시성 테스트 작성**

`tests/test_shared_memory_concurrency.py`:

```python
"""두 writer 가 같은 events.jsonl 에 붙는 상황에서 레코드 유실이 없어야 한다.

shared_memory 의 전체 재작성(delete) 도중 lib/agent_memory 나 다른 프로세스가
append 하면, 락이 없을 때 그 레코드가 사라진다. 서브프로세스로 재현한다.
(monkeypatch 는 자식 프로세스에 전달되지 않으므로 환경변수로 저장소를 지정한다.)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WORKER = """
import sys
sys.path.insert(0, {root!r})
from agent_console import shared_memory

mode = sys.argv[1]
if mode == "append":
    for i in range(40):
        shared_memory.append_record({{"title": f"w-{{i}}", "summary": "s", "tags": ["chat"]}})
else:
    for rid in sys.argv[2:]:
        shared_memory.delete_record(rid)
"""


def _run(script: Path, env: dict, *args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(script), *args], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_concurrent_append_and_rewrite_lose_no_records(tmp_path):
    store = tmp_path / "shared-memory"
    env = dict(os.environ)
    env["AGENT_CONSOLE_SHARED_MEMORY_DIR"] = str(store)
    env["AGENT_MEMORY_DIR"] = str(store)

    script = tmp_path / "worker.py"
    script.write_text(WORKER.format(root=str(PROJECT_ROOT)), encoding="utf-8")

    # 시드 30건을 만들고 그중 10건의 id 를 삭제 대상으로 고른다
    seed_env = dict(env)
    seed = tmp_path / "seed.py"
    seed.write_text(
        "import sys, json\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "from agent_console import shared_memory\n"
        "ids = [shared_memory.append_record({'title': f'seed-{i}', 'summary': 's',"
        " 'tags': ['chat']})['id'] for i in range(30)]\n"
        "print(json.dumps(ids))\n",
        encoding="utf-8",
    )
    out = subprocess.run([sys.executable, str(seed)], env=seed_env,
                         capture_output=True, text=True, check=True)
    seed_ids = json.loads(out.stdout.strip().splitlines()[-1])
    victims = seed_ids[:10]

    p1 = _run(script, env, "append")
    p2 = _run(script, env, "delete", *victims)
    p1.wait(timeout=180)
    p2.wait(timeout=180)
    assert p1.returncode == 0, p1.stderr.read().decode()
    assert p2.returncode == 0, p2.stderr.read().decode()

    rows = [json.loads(line) for line in
            (store / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    # 시드 30 - 삭제 10 + append 40 = 60
    assert len(rows) == 60, f"레코드 유실/중복: {len(rows)}건"


def test_index_keeps_both_writer_schemas(tmp_path, monkeypatch):
    """shared_memory 와 lib.agent_memory 가 번갈아 써도 서로의 키를 지운다."""
    store = tmp_path / "shared-memory"
    store.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(store))

    from agent_console import shared_memory
    from lib import agent_memory

    monkeypatch.setattr(agent_memory, "MEMORY_DIR", store)
    monkeypatch.setattr(agent_memory, "EVENTS_PATH", store / "events.jsonl")
    monkeypatch.setattr(agent_memory, "INDEX_PATH", store / "index.json")

    shared_memory.append_record({"title": "a", "summary": "s", "tags": ["chat"]})
    agent_memory._append_event({"title": "b", "summary": "s2"})
    shared_memory.append_record({"title": "c", "summary": "s3", "tags": ["chat"]})

    payload = json.loads((store / "index.json").read_text(encoding="utf-8"))
    assert payload.get("recordCount") == 3, "shared_memory 키가 사라짐"
    assert payload.get("count") == 1, "agent_memory 키가 사라짐"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `./.venv/bin/pytest tests/test_shared_memory_concurrency.py -q`
Expected: 첫 테스트가 `레코드 유실/중복` 으로 FAIL (락 없이 append 와 전체 재작성이 겹침). 둘째는 `agent_memory 키가 사라짐` 으로 FAIL.

- [ ] **Step 3: `_write_text_atomic` 을 safe_io 로 위임**

`lib/agent_memory.py:87-91` 을 교체:

```python
def _write_text_atomic(path: Path, text: str) -> None:
    """원자적 텍스트 쓰기 — 구현은 safe_io 단일 소스에 위임."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    import safe_io

    safe_io.atomic_write_text(str(path), text)
```

- [ ] **Step 4: `_append_event` 를 공유 락 안으로**

`lib/agent_memory.py:414-427` 의 `_append_event` 본문을 교체:

```python
def _append_event(payload: dict, now: datetime | None = None) -> None:
    try:
        import safe_io

        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"schemaVersion": SCHEMA_VERSION, "id": uuid.uuid4().hex[:16],
               "createdAt": (now or _now()).isoformat(timespec="seconds"),
               "visibility": "local-only", **payload}
        # agent_console/shared_memory 와 같은 사이드카 락 — 전체 재작성과 직렬화된다
        with safe_io.file_write_lock(str(EVENTS_PATH), timeout=30.0):
            with open(EVENTS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            idx = _read_json(INDEX_PATH, {}) or {}
            idx.update({"latestAt": rec["createdAt"], "latestTitle": rec.get("title", ""),
                        "count": int(idx.get("count", 0)) + 1})
            safe_io.atomic_write_json(str(INDEX_PATH), idx)
    except Exception as e:
        logger.warning("이벤트 기록 실패(무시): %s", e)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `./.venv/bin/pytest tests/test_shared_memory_concurrency.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: 기존 agent_memory 테스트 회귀 확인**

Run: `./.venv/bin/pytest tests/test_agent_memory.py -q`
Expected: 전부 PASS (특히 `index.json["count"] == 3` 단언)

- [ ] **Step 7: 커밋**

```bash
git add lib/agent_memory.py tests/test_shared_memory_concurrency.py
git commit -m "fix) lib/agent_memory 를 공유 쓰기 락에 편입하고 원자쓰기를 통합"
```

---

### Task 5: 전체 회귀 + 문서 갱신

**Files:**
- Modify: `docs/shared-agent-memory.md`

**Interfaces:**
- Consumes: Task 1~4 전체
- Produces: 없음

- [ ] **Step 1: 변경 전 실패 목록 확보 (베이스라인)**

Run:
```bash
git stash list >/dev/null; BASE=$(git rev-parse HEAD)
git worktree add -q --detach /tmp/wiki-base d37388f
cd /tmp/wiki-base && /home/ubuntu/projects/stock-report/.venv/bin/pytest tests/ -q 2>&1 | grep "^FAILED" | sed 's/ - .*//' | sort > /tmp/before.txt
cd /home/ubuntu/projects/stock-report && git worktree remove /tmp/wiki-base --force
wc -l /tmp/before.txt
```
Expected: 작업 시작 커밋(`d37388f`) 기준 실패 목록이 `/tmp/before.txt` 에 저장됨.

- [ ] **Step 2: 현재 실패 목록과 대조**

Run:
```bash
./.venv/bin/pytest tests/ -q 2>&1 | grep "^FAILED" | sed 's/ - .*//' | sort > /tmp/after.txt
echo "--- 신규 실패 ---"; comm -13 /tmp/before.txt /tmp/after.txt
echo "--- 해결된 실패 ---"; comm -23 /tmp/before.txt /tmp/after.txt
```
Expected: **신규 실패 0건**. 신규 실패가 있으면 멈추고 원인을 고친다.

- [ ] **Step 3: 위키 실제 렌더 확인**

Run:
```bash
cat > /tmp/probe.py <<'PY'
from dashboard.wiki_browser import render_wiki_tab
render_wiki_tab("market")
PY
./.venv/bin/python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('/tmp/probe.py', default_timeout=90); at.run()
print('exceptions:', len(at.exception))
for e in at.exception: print('EXC:', e.value)
"
```
Expected: `exceptions: 0`

- [ ] **Step 4: 문서 갱신**

`docs/shared-agent-memory.md` 의 `index.json` 설명(18행)을 교체:

```markdown
- `index.json`: latest-record snapshot generated from JSONL.
  두 writer 가 키를 나눠 쓴다 — `agent_console/shared_memory` 는
  `ok/schemaVersion/updatedAt/recordCount/latestRecordAt/records`,
  `lib/agent_memory` 는 `latestAt/latestTitle/count`. 양쪽 모두 기존 내용을 읽어
  자기 키만 갱신하며, `events.jsonl.lock` 사이드카 flock 으로 직렬화된다.
```

같은 문서 끝에 다음 절을 추가:

```markdown
## 쓰기 규약

- `events.jsonl` 을 쓰는 모든 코드는 `safe_io.file_write_lock(EVENTS_PATH)` 를 잡는다.
  현재 writer: `shared_memory.append_record` / `delete_record` / `upsert_record`,
  `lib/agent_memory._append_event`.
- flock 은 재진입 불가다. 락 안에서 호출하는 내부 함수는 락을 다시 잡지 않는다
  (`_write_index_locked`, `_write_jsonl_locked`).
- 전체 재작성은 반드시 `safe_io.atomic_write_text` 를 쓴다 (temp→fsync→rename).
- 위키처럼 전수 조회가 필요한 소비자는 `list_records()` 가 아니라
  `all_records()` 를 쓴다. `list_records` 는 최대 100 으로 클램프된다.

### 알려진 부채

`_write_index_locked()` 는 매 쓰기마다 최신 200건 스냅샷(약 119KB)을 재작성한다.
현재 규모(수십~수백 건)에서는 무해하나, 레코드가 수천 건이 되면 디바운스가 필요하다.
```

- [ ] **Step 5: 커밋**

```bash
git add docs/shared-agent-memory.md
git commit -m "docs) 공유 메모리 쓰기 규약과 index.json 키 소유권을 문서화"
```

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 요구 | 태스크 |
| --- | --- |
| 목표 1 — 읽기 창 제거 | Task 2 (`all_records` + 위키 3곳) |
| 목표 2 — 원자적·직렬화 쓰기 | Task 1(헬퍼), Task 3(shared_memory), Task 4(agent_memory) |
| 목표 3 — index.json 클로버 제거 | Task 3 Step 4(merge), Task 4 Step 4(merge), 테스트는 Task 4 Step 1 |
| `list_records` 불변 | Global Constraints + Task 2 Step 3(별도 함수 추가) |
| 위키 판별을 `wiki.py` 에 유지 | Task 2 Step 4(필터는 `wiki.py` 에 그대로) |
| `LockTimeout` 전파 vs best-effort | Global Constraints + Task 4 Step 4(agent_memory 는 기존 `logger.warning` 유지) |
| 테스트 1~5 | Task 2 Step 1(창·중복), Task 3 Step 1(원자성·재진입), Task 4 Step 1(동시성·index), Task 1 Step 1(원자 쓰기 실패 복원) |
| 회귀는 목록 대조 | Task 5 Step 1~2 |
| 부채 문서화 | Task 5 Step 4 |

**2. 플레이스홀더** 없음 — 모든 코드 스텝에 실제 코드가 있다.

**3. 타입 일관성** `all_records() -> list[dict]`, `upsert_record(record: dict) -> dict`,
`_write_index_locked() -> None`, `_write_jsonl_locked(rows: list[dict]) -> None`,
`atomic_write_text(path: str, text: str) -> None` 이 정의(Task 1·2·3)와 사용처(Task 3·4)에서 일치한다.
`_write_index` → `_write_index_locked` 이름 변경에 따른 호출부는 Task 3 Step 5·6 에서 함께 교체된다.
