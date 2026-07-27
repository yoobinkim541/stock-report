# Wiki 결함 수정 — Task 2/3

## 프로젝트
/home/ubuntu/projects/stock-report (Python 3.10, uv, pytest)

## 작업 개요
Critical Review에서 발견된 이슈 중 성능(Task 2)·데이터 무결성(Task 3)을 순차적으로 수정. 순서: Task 2 → Task 3. (Task 1 보안 수정은 이미 별도로 완료됨)

## Task 2: 성능 — 캐싱 계층
**파일:** `agent_console/wiki.py`

### 2a. `@_cached` 데코레이터 추가 (파일 상단, import 아래)
```python
import functools
import time
from typing import Any

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 30.0

def _cached(key_prefix: str, ttl: float = _CACHE_TTL):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            k = f"{key_prefix}:{hash(args)}:{hash(frozenset(kwargs.items()))}"
            now = time.monotonic()
            if k in _CACHE and (now - _CACHE[k][0]) < ttl:
                return _CACHE[k][1]
            r = func(*args, **kwargs)
            _CACHE[k] = (now, r)
            return r
        return wrapper
    return decorator
```

### 2b. 적용 대상
- `stats()` → `@_cached("stats")`
- `lint_pages()` → `@_cached("lint_pages")`
- `_build_wiki_context_section()` → `@_cached("wiki_context")`

### 2c. `rebuild_artifacts()` 디바운스
```python
_REBUILD_TIMER: threading.Timer | None = None
_REBUILD_LOCK = threading.Lock()

def _debounced_rebuild():
    global _REBUILD_TIMER
    with _REBUILD_LOCK:
        if _REBUILD_TIMER and _REBUILD_TIMER.is_alive():
            _REBUILD_TIMER.cancel()
        _REBUILD_TIMER = threading.Timer(1.0, rebuild_artifacts)
        _REBUILD_TIMER.start()
```
`track_page_usage()`와 `_store_page_feedback()`에서 `rebuild_artifacts()` 대신 `_debounced_rebuild()` 호출.

### 2d. 캐시 무효화
`upsert_page()`, `delete_page()`, `_merge_pages()`, `_split_page()`에서 `_CACHE.clear()` (모두 wiki.py 내부 private 함수, exported 아님)

## Task 3: 데이터 무결성 — Atomic batch
**파일:** `agent_console/shared_memory.py` + `agent_console/wiki.py`

### 3a. `shared_memory.batch_upsert_delete()` 추가
```python
def batch_upsert_delete(*, upserts: list[dict], deletes: list[str]) -> dict:
    """원자적 배치: 모든 upsert + delete를 한 번의 파일 재작성으로 처리."""
    ensure_store()
    with _events_lock():
        rows = _read_jsonl(_paths()["events"])
        # delete
        delete_set = set(deletes or [])
        rows = [row for row in rows if row.get("id") not in delete_set]
        # upsert (치환)
        upsert_ids = {r["id"] for r in upserts if r.get("id")}
        rows = [row for row in rows if row.get("id") not in upsert_ids]
        rows.extend(normalize_record(r) for r in upserts)
        _write_jsonl_locked(rows)
        _write_index_locked()
    refresh_context_memory_summary()
    return {"ok": True, "upserted": len(upserts), "deleted": len(deletes)}
```

### 3b. `_merge_pages()` 리팩터
기존: `delete_page(A)` → `delete_page(B)` → `upsert_page(target)` (3개 개별 호출)
변경: `batch_upsert_delete(upserts=[target], deletes=[A_id, B_id])` (1회 호출)

### 3c. `_split_page()` 리팩터
기존: `new_titles` 개수(N, 가변)만큼 `upsert_page(new_i)` + 형제 링크 갱신용 `upsert_page(new_i)` N번 + `upsert_page(source, status="archived")` 1번 → 총 2N+1개 개별 호출
변경: `batch_upsert_delete(upserts=[*new_pages_with_links, archived_source], deletes=[])` (1회 호출). N이 고정 2가 아니므로 `new1, new2`로 하드코딩하지 말고 `new_titles` 루프로 upserts 리스트를 동적으로 구성할 것.

### 3d. `_merge_messages()` 제한 8→16 + 기존 우선
`_merge_messages()`(wiki.py) 마지막 줄 `return rows[:8]` → `return rows[:16]`. (이름 있는 `MAX_MESSAGES` 상수는 없음 — 하드코딩 리터럴이므로 grep으로 상수명을 찾지 말고 이 줄을 직접 수정할 것)
기존 메시지가 우선 보존되도록 정렬. `existing`를 먼저 추가하고, 남은 슬롯에 `new_messages` 추가. (현재 구현은 `existing + new_messages`를 합쳐 dedupe 후 앞에서부터 slice하므로 이미 existing 우선 — limit 숫자만 수정하면 됨)

## 중요 제약
- 파일 변경 범위: `agent_console/wiki.py`, `agent_console/shared_memory.py`만
- 다른 파일 건드리지 말 것
- 각 Task 완료 후 `uv run pytest tests/ -x -q` 실행 (시간 초과 시 `tests/test_wiki_lifecycle.py tests/test_qmd_search.py`)
- P0/P2에서 추가한 P0 기능(p1a/p1b)을 깨뜨리지 말 것
- 모든 변경 후 `git add -A && git commit -m "fix) wiki: Task N - 설명"` 각각 커밋