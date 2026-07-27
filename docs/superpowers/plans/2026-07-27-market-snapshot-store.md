# Market Snapshot Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 콘솔의 한국 시장 마이크로스트럭처 데이터가 파일 캐시를 기본으로 읽히고, 나중에 Redis로 확장 가능한 저장소 인터페이스를 갖게 한다.

**Architecture:** `agent_console.market_snapshot_store`가 `FileSnapshotStore`, optional `RedisSnapshotStore`, `load_market_microstructure()`를 제공한다. `agent_console.realtime_market.build_market_snapshot()`은 기존 quote/fx 스냅샷에 저장소의 `kospi_index`, `kosdaq_index`, `investor_flow`, `k200_futures`, `advancers_decliners`를 병합하고, 값이 있는 필드는 `unavailable`에서 제거한다.

**Tech Stack:** Python 3, pytest, JSON file cache, optional `redis` Python package when `REDIS_URL` is set.

## Global Constraints

- Redis는 필수 의존성이 아니다. `REDIS_URL`이 없거나 `redis` 패키지가 없으면 파일 캐시만 사용한다.
- AI 콘솔 답변 경로는 외부 API를 직접 호출하지 않고 저장소/캐시만 읽는다.
- 캐시 payload는 `as_of`, `source`, `age_s` 또는 `max_age_s`로 freshness를 표시한다.
- 손상된 JSON, Redis 장애, stale payload는 예외를 던지지 않고 빈 스냅샷으로 degrade한다.

---

### Task 1: Snapshot Store

**Files:**
- Create: `agent_console/market_snapshot_store.py`
- Test: `tests/test_market_snapshot_store.py`

**Interfaces:**
- Produces: `FileSnapshotStore(path: str | Path | None = None).read() -> dict`
- Produces: `FileSnapshotStore(...).write(payload: dict) -> bool`
- Produces: `RedisSnapshotStore(url: str, key: str = DEFAULT_REDIS_KEY).read() -> dict`
- Produces: `load_market_microstructure(store=None) -> dict`

- [ ] **Step 1: Write failing tests for file read/write and stale rejection**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_market_snapshot_store.py -q`
Expected: FAIL because module does not exist.

- [ ] **Step 2: Implement minimal file store**

Use JSON and atomic write through `safe_io.atomic_write_json` when available.

- [ ] **Step 3: Run store tests**

Expected: PASS.

### Task 2: AI Console Snapshot Integration

**Files:**
- Modify: `agent_console/realtime_market.py`
- Test: `tests/test_agent_realtime_market_context.py`

**Interfaces:**
- Consumes: `market_snapshot_store.load_market_microstructure()`
- Produces: `build_market_snapshot()["indices"]`
- Produces: `build_market_snapshot()["investor_flow"]`
- Produces: `build_market_snapshot()["k200_futures"]`
- Produces: `build_market_snapshot()["breadth"]`

- [ ] **Step 1: Write failing integration test**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py::test_realtime_snapshot_merges_market_microstructure_store -q`
Expected: FAIL because `market_microstructure` store is not read.

- [ ] **Step 2: Implement merge**

Read store once, merge known fields, remove populated fields from unavailable list.

- [ ] **Step 3: Run focused tests**

Expected: PASS.

### Task 3: Prompt Formatting

**Files:**
- Modify: `agent_console/realtime_market.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `compact_snapshot_lines(snapshot)`
- Produces: compact lines for indices, investor flow, futures, breadth.

- [ ] **Step 1: Write failing prompt/formatter assertion**

Assert compact lines include KOSPI, 외국인/기관, K200 선물, 상승/하락.

- [ ] **Step 2: Implement formatter additions**

Keep lines concise and source/as_of visible.

- [ ] **Step 3: Run focused tests**

Expected: PASS.

### Task 4: Verification

**Files:**
- All changed files.

- [ ] **Step 1: Run related tests**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m pytest tests/test_market_snapshot_store.py tests/test_agent_realtime_market_context.py tests/test_agent_console.py tests/test_dashboard_pages.py -q`

- [ ] **Step 2: Run syntax and diff checks**

Run: `/home/ubuntu/projects/stock-report/.venv/bin/python -m py_compile agent_console/market_snapshot_store.py agent_console/realtime_market.py agent_console/agent.py`
Run: `git diff --check`

- [ ] **Step 3: Commit, merge, push, restart**

Commit only relevant files, fast-forward master, push, restart dashboard, verify `/agent` 200.
