# LLM Wiki — Autonomous Deletion & Lifecycle Management

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** LLM이 직접 오래된/중복/무효한 위키 페이지를 삭제하고, 페이지 TTL 기반 자동 정리와 병합/분할 기능을 추가한다.

**Architecture:** 기존 `auto_curate_from_chat`에 `action: delete | merge | split`을 추가하고, 페이지 TTL을 체크하는 크론/함수를 shared_memory 레벨에 추가한다. LLM의 삭제 결정은 `delete_page()`로 연결되며, 결정적 폴백으로 TTL 기반 `archive_stale_pages()`가 30분 cron에서 동작한다.

**Tech Stack:** Python 3.11, pytest, agent_console/wiki.py, agent_console/shared_memory.py

---

### Task 1: LLM 큐레이션에 `action: delete` 지원 추가

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Step 1: LLM 프롬프트에 delete action 추가**

`_build_auto_curation_prompt()`에서 허용 action 목록에 `delete`를 추가하고, LLM이 삭제를 판단할 기준을 프롬프트에 포함:
- 페이지가 30일 이상 업데이트되지 않음
- 페이지 내용이 현재 시장 상황과 모순됨
- 페이지가 다른 페이지와 완전히 중복됨
- 페이지가 정보가 없거나 (`summary`/`body` 부실) 검증 불가능한 상태

```json
{
  "action": "delete",
  "reason": "string — why this page should be deleted"
}
```

**Step 2: `_parse_curation_plan()`에 delete 핸들링 추가**

`action == "delete"` → `delete_page(page_id)` 호출. 결과를 `{"action": "delete", "page_id": ..., "ok": True}` 형태로 반환.

**Step 3: `_execute_curation_plan()`에 delete 분기 추가**

plan의 action이 `delete`면 → `delete_page()` 실행 → 결과 반환. 다른 action과 동일한 예외 처리 체계 사용.

**Step 4: 기존 skip/update/create와 동일한 로깅/아티팩트 재생성**

삭제 후 `rebuild_artifacts()` 호출, stats 업데이트.

**Step 5: 테스트**

```
pytest tests/test_agent_console.py -q -k delete
```

Command: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_agent_console.py -q -k delete`
Expected: 3+ tests pass (delete action parsing, LLM delete execution, stale page detection)

---

### Task 2: 페이지 TTL 및 자동 스테일 감지

**Files:**
- Modify: `agent_console/wiki.py`
- Modify: `agent_console/shared_memory.py`
- Create: `tests/test_wiki_lifecycle.py`

**Step 1: `_is_page_stale(page, max_age_days=30) -> bool`**

페이지가 마지막 업데이트(`updatedAt`)로부터 `max_age_days` 이상 지났는지 검사.
`kind=source_digest` 페이지는 소스 이벤트 시간도 확인 (원천 데이터가 더 오래됐으면 stale).

```python
def _is_page_stale(page: dict, max_age_days: int = 30) -> bool:
    now = datetime.now(timezone.utc)
    updated_str = page.get("updatedAt") or page.get("createdAt") or ""
    if not updated_str:
        return True
    try:
        updated = datetime.fromisoformat(updated_str)
        return (now - updated).days >= max_age_days
    except ValueError:
        return False
```

**Step 2: `list_stale_pages(max_age_days=30) -> list[dict]`**

모든 위키 페이지 중 `_is_page_stale()`이 True인 것만 반환.

**Step 3: `archive_stale_pages(max_age_days=30, dry_run=False) -> dict`**

stale 페이지를 찾아:
- `status: archived`로 마크 (삭제 대신 상태 변경)
- `reason: "stale"` 필드 추가
- 통계 반환: `{"archived": N, "stale_skipped": M, "total": K}`

**Step 4: `rebuild_artifacts()`에 archived 상태 반영**

`index.md`에 archived 섹션 추가 (하단, 접힌 상태).
`stats()`에 `kind_counts`에 `archived` 포함.

**Step 5: `_should_auto_curate()`에서 archived 페이지 재활성화 가능**

LLM이 archived 페이지를 다시 활성화(`action: update`로 status를 `active`로 변경)할 수 있어야 함. `upsert_page`가 `archived` 상태를 덮어쓸 수 있어야 함.

**Step 6: 만료된 archived 페이지 삭제 (선택사항)**

`archive_stale_pages()`에서 `max_archive_days=90` 추가 — archived 상태로 90일 이상이면 `delete_page()`.

Command: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_wiki_lifecycle.py -q`
Expected: 4+ tests pass (stale detection, archive, unarchive, full lifecycle)

---

### Task 3: LLM 큐레이션에 `action: merge | split` 지원 추가

**Files:**
- Modify: `agent_console/wiki.py`
- Test: `tests/test_agent_console.py`

**Step 1: LLM 프롬프트에 merge/split action 추가**

```json
{
  "action": "merge",
  "target_page_id": "id-to-merge-into",
  "source_page_ids": ["id-to-absorb"],
  "reason": "these pages overlap on NVDA Q2 earnings"
}
```

```json
{
  "action": "split",
  "source_page_id": "id-to-split",
  "new_titles": ["NVDA AI revenue", "NVDA data center"],
  "reason": "page covers two distinct topics"
}
```

**Step 2: `_merge_pages(source_ids: list[str], target_id: str, llm_synthesis: str) -> dict`**

- `source_ids`의 페이지들을 읽어 `target_id` 페이지로 내용 병합
- `source_ids` 페이지들을 `delete_page()`로 삭제
- `target_id` 페이지의 `body`에 LLM이 생성한 요약/합성 추가
- `merged_from: source_ids` 메타데이터 추가
- 결과: `{"action": "merge", "target": ..., "deleted": source_ids}`

**Step 3: `_split_page(source_id: str, new_titles: list[str], llm_bodies: list[str]) -> dict`**

- `source_id` 페이지를 읽어 내용을 분할
- 각 `new_title`에 대해 새 페이지 생성
- `source_id` 페이지는 `status: archived, split_into: new_ids`로 변경
- 교차 링크 자동 생성 (`links` 필드에 서로 링크)
- 결과: `{"action": "split", "source": source_id, "created": new_ids}`

**Step 4: `lint_pages()`에 merge/split 제안 추가**

`lint_pages()`가 중복 페이지 후보를 찾으면 `suggested: "merge"` 플래그 추가. LLM이 큐레이션할 때 이 정보를 활용할 수 있도록.

Command: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -m pytest tests/test_agent_console.py -q -k "merge or split"`
Expected: 4+ tests pass

---

### Task 4: 30분 cron에 `archive_stale_pages()` 연결

**Files:**
- Modify: `deploy/crontab.stock-report`

**Step 1: `source_wiki_curator` 실행 후 `archive_stale_pages()` 호출 추가**

`source_wiki_curator.py`의 `main()` 또는 crontab 라인에서:

```bash
# source-cache -> LLM wiki 자동 갱신 + stale 정리
8,38 * * * * cd /home/ubuntu/projects/stock-report && uv run python -c "from agent_console.wiki import archive_stale_pages; from agent_console.shared_memory import ensure_store; ensure_store(); archive_stale_pages(max_age_days=30)" >> /tmp/wiki_archive.log 2>&1
```

Command: `cd /home/ubuntu/projects/stock-report && .venv/bin/python -c "from agent_console.wiki import archive_stale_pages; print(archive_stale_pages(max_age_days=30, dry_run=True))"`
Expected: stats dict with stale page count