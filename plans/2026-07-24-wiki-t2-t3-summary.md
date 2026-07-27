# Wiki 남은 작업: Task 2 (성능) + Task 3 (무결성)

## Task 2: 성능 — JSONL 캐싱 + rebuild debounce

**목표:** `auto_curate_from_chat()` 1회 호출당 events.jsonl 3회 전수 스캔 제거

### 2a. `@_cached(ttl=30s)` 데코레이터
**파일:** `agent_console/wiki.py`
**위치:** `WikiSystem` 클래스 내부, `_wiki_records()` 위

```python
import functools
import time

def _cached(ttl=30):
    """Decorator: cache result for TTL seconds, keyed by args."""
    def decorator(fn):
        cache = {}
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            if key in cache:
                result, expiry = cache[key]
                if now < expiry:
                    return result
            result = fn(self, *args, **kwargs)
            cache[key] = (result, now + ttl)
            return result
        # invalidate hook
        wrapper.cache_clear = lambda: cache.clear()
        return wrapper
    return decorator
```

**적용:** `@_cached(ttl=30)` → `_wiki_records()`, `all_records()`, `stats()`
**invalidation:** `write_page()`, `delete_page()`, `_merge_pages()`, `_split_page()`, `track_page_usage()` 내부에서 `self._wiki_records.cache_clear()` 호출

### 2b. `rebuild_artifacts()` 1초 debounce
**파일:** `agent_console/wiki.py`

```python
import asyncio

class WikiSystem:
    def __init__(self, ...):
        self._rebuild_task = None
        self._rebuild_needed = False
    
    def _schedule_rebuild(self):
        if self._rebuild_task is not None:
            self._rebuild_needed = True
            return
        async def _debounce():
            await asyncio.sleep(1)
            self._rebuild_needed = False
            self._rebuild_task = None
            self.rebuild_artifacts()
        self._rebuild_task = asyncio.create_task(_debounce())
```

**적용:** `track_page_usage()` 내 `rebuild_artifacts()` 호출 → `_schedule_rebuild()`로 대체
**주의:** `asyncio` 사용 중이면 OK, 아니면 `threading.Timer(1, ...)` 사용

### 2c. `_build_wiki_context_section()` 캐싱
**파일:** `agent_console/wiki.py`
**적용:** `@_cached(ttl=10)` → `_build_wiki_context_section()`
**invalidation:** `auto_curate_from_chat()` 시작 시 `cache_clear()`

### 검증
```bash
pytest tests/test_wiki_lifecycle.py tests/test_qmd_search.py -x -q
```
기존 15개 통과 + 캐싱 로직 변경으로 인한 회귀 없음

---

## Task 3: 데이터 무결성 — Atomic batch upsert

**목표:** `_merge_pages()` / `_split_page()` partial failure로 데이터 손실 방지

### 3a. `batch_upsert_delete()` 원자적 인터페이스
**파일:** `agent_console/wiki.py`
**위치:** `WikiSystem` 클래스

```python
def batch_upsert_delete(self, to_upsert: list[dict], to_delete: list[str]) -> None:
    """원자적 batch: 모든 upsert 후 모든 delete, OR rollback on failure."""
    with self.file_write_lock:
        records = self._wiki_records()
        # backup
        backup = list(records)
        try:
            # delete first (no dependency on upsert)
            records = [r for r in records if r.get('id') not in to_delete]
            # upsert
            existing_ids = {r.get('id') for r in records}
            for rec in to_upsert:
                if rec.get('id') in existing_ids:
                    records = [r if r.get('id') != rec.get('id') else rec for r in records]
                else:
                    records.append(rec)
            self._write_records(records)
            self.rebuild_artifacts()
        except Exception:
            self._write_records(backup)  # rollback
            raise
        finally:
            self._wiki_records.cache_clear()
```

### 3b. `_merge_pages()` 리팩터
**파일:** `agent_console/wiki.py`
**변경:** 3개 개별 호출 `delete_page()` + `upsert_page()` + `archive_source()` → `batch_upsert_delete()` 1회 호출로 통합

```python
def _merge_pages(self, source_id, target_id):
    # ... 기존 로직으로 payload 생성 ...
    to_upsert = [target_payload]
    to_delete = [source_id]
    self.batch_upsert_delete(to_upsert, to_delete)
```

### 3c. `_split_page()` 리팩터
**파일:** `agent_console/wiki.py`
**변경:** 3개 개별 호출 → `batch_upsert_delete()` 1회 호출로 통합

### 3d. `_merge_messages()` 제한 완화
**파일:** `agent_console/wiki.py`
**변경:** `messages[-8:]` → `messages[-16:]` (8개→16개)

### 3e. 읽기 락 추가
**파일:** `agent_console/wiki.py`
**변경:** `_wiki_records()` 내부에서 `with self.file_read_lock:` 추가 (RLock)

### 검증
```bash
pytest tests/test_wiki_lifecycle.py tests/test_qmd_search.py -x -q
```
15개 통과 + merge/split/rollback 시나리오 추가 테스트 권장

---

## 캐치업 실행법
```bash
cd /home/ubuntu/projects/stock-report
claude --dangerously-skip-permissions --max-budget-usd 3 \
  -p "$(cat plans/wiki-fix-prompt.md)"
```
단, `plans/wiki-fix-prompt.md`에서 Task 2/3 섹션만 남기고 Task 1 제거.