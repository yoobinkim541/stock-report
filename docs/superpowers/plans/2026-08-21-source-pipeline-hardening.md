# Source Pipeline Hardening Implementation Plan

> ## ✅ 완료 (2026-08-23 검증 후 표시)
>
> 다른 세션이 구현·워크트리에 미커밋 상태로 남겨둔 것을 검증 후 대신 커밋했다.
> 관련 커밋: `3c314a8`·`66ed9e5` — 소스 수집 그룹별 독립 실행 분리 + crontab drift 점검. 파일 전부 존재 확인·테스트 31건 통과.
> 라이브 crontab 은 템플릿과 별도 — 프로덕션 스케줄 반영은 미적용(설치는 후속 조치).


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve mutable source observations, isolate provider execution, reduce network cost, and make health reflect persistence rather than fetch attempts alone.

**Architecture:** Keep the JSONL cache as the compatibility layer, add type-aware identities and cross-process locking, then move provider orchestration into a registry that supports independent source groups and bounded concurrency. Separate cron schedules match source cadence.

**Tech Stack:** Python 3.11, `concurrent.futures`, existing `safe_io`, yfinance batch download, pytest, cron/flock.

**Spec:** `docs/superpowers/specs/2026-08-21-resilient-source-pipeline-design.md`

## Global Constraints

- Do not fabricate historical observations or rewrite existing JSONL ids.
- Immutable content stays URL/source-id deduplicated.
- Mutable observations are unique by stable entity and 30-minute observation bucket.
- Concurrent jobs must use a cross-process write lock.
- Selected-source runs update only sources they attempted.
- Existing source-cache readers remain compatible.

---

### Task 1: Type-Aware Event Identity

**Files:**
- Create: `reports/source_identity.py`
- Modify: `reports/source_collector.py`
- Create: `tests/test_source_identity.py`
- Modify: `tests/test_source_collector.py`

**Interfaces:**
- Produces: `normalize_event_identity(event: dict, observed_at: datetime, bucket_minutes: int = 30) -> dict`
- Produces fields: `id`, `content_id`, `entity_id`, `record_kind`, `observed_at`, `observation_bucket`

- [x] **Step 1: Write failing identity tests**

Assert same news URL keeps one id across runs, same market entity gets the same `entity_id` but different ids across 30-minute buckets, two rows in one bucket dedupe, and source-native ids take precedence over URLs.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_source_identity.py -q`

Expected: FAIL because the module does not exist.

- [x] **Step 3: Implement identity normalization**

Recognize mutable types `market_snapshot`, `macro_snapshot`, `prediction_market`, `economic_calendar_snapshot`, and explicit `record_kind=observation`. Preserve legacy `event_id()` as a compatibility wrapper over immutable identity.

- [x] **Step 4: Wire identity into append/load**

Set `collected_at` and `observed_at` before computing ids. Keep distinct observation ids in `load_recent_events()` and dedupe immutable content as before.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_source_identity.py tests/test_source_collector.py -q -k 'identity or append or recent'`

Expected: PASS.

### Task 2: Locked Append And Run Manifests

**Files:**
- Create: `reports/source_runs.py`
- Modify: `reports/source_collector.py`
- Create: `tests/test_source_runs.py`
- Modify: `tests/test_source_collector.py`

**Interfaces:**
- Produces: `record_source_run(cache_dir, run: dict) -> dict`
- Produces: `load_source_runs(cache_dir, *, hours: int = 24) -> list[dict]`
- Extends: `update_source_health(events: list[dict], cache_dir=DEFAULT_CACHE_DIR, now: datetime | None = None, attempted_sources: Iterable[str] | None = None, run_stats: dict[str, dict] | None = None) -> dict`

- [x] **Step 1: Write failing persistence-health tests**

Assert two concurrent append callers do not lose rows, selected-source health leaves unrelated records untouched, and a run with fetched rows but zero persisted rows is represented explicitly.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_source_runs.py tests/test_source_collector.py -q -k 'lock or attempted or persisted'`

Expected: FAIL on missing manifest and health fields.

- [x] **Step 3: Add cross-process locking and manifests**

Use `safe_io.file_write_lock` around read-dedupe-append. Store one JSONL run record per provider with start/end, duration, fetched, persisted, transport, availability, and error.

- [x] **Step 4: Extend health without breaking old files**

Old health rows lacking new fields must still load. New rows expose `last_fetched_count`, `last_persisted_count`, `last_duration_ms`, and `zero_persist_streak`.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_source_runs.py tests/test_source_collector.py -q`

Expected: PASS.

### Task 3: Provider Registry And Independent Groups

**Files:**
- Create: `reports/source_pipeline.py`
- Modify: `reports/source_collector.py`
- Create: `tests/test_source_pipeline.py`

**Interfaces:**
- Produces: `ProviderSpec(name, sources, group, fetch, timeout_seconds, retries, mutable)`
- Produces: `run_providers(*, group: str | None = None, sources: list[str] | None = None, max_workers: int = 4, cache_dir: Path | str = DEFAULT_CACHE_DIR) -> dict`
- CLI: `python -m reports.source_pipeline --group <news|market|macro|prediction|calendar>`

- [x] **Step 1: Write failing registry tests**

Assert source/group selection, bounded parallel execution, exception isolation, non-retryable 451 behavior, transient retry count, and per-provider manifest/health updates.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_source_pipeline.py -q`

Expected: FAIL because the registry does not exist.

- [x] **Step 3: Implement registry and runner**

Keep provider fetch functions in their current modules. Use `ThreadPoolExecutor` only for independent providers; preserve deterministic result ordering for tests and logs.

- [x] **Step 4: Make the old collector CLI a compatibility entrypoint**

`reports/source_collector.py` without selection invokes all registered providers. Existing imports and tests keep working.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_source_pipeline.py tests/test_source_collector.py -q`

Expected: PASS.

### Task 4: Batch Yahoo Snapshot Collection

**Files:**
- Modify: `reports/source_collector.py`
- Modify: `tests/test_source_collector.py`

**Interfaces:**
- Preserves: `fetch_market_snapshot_events(yf_module=None) -> list[dict]`
- Consumes: `yf_module.download(tickers, period="1y", auto_adjust=True, progress=False, group_by="column")`

- [x] **Step 1: Write failing batch tests**

Assert one batch call supplies multiple tickers, MultiIndex columns normalize correctly, missing batch symbols use per-ticker fallback, and every row carries observation identity inputs.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_source_collector.py -q -k market_snapshot`

Expected: FAIL because current code invokes `Ticker.history` for every ticker.

- [x] **Step 3: Implement batch-first fetching**

Download once, normalize single/MultiIndex shapes, calculate 1D/5D/1M/1Y metrics, and call `Ticker.history` only for symbols absent from the batch.

- [x] **Step 4: Run tests and benchmark**

Run: `.venv/bin/pytest tests/test_source_collector.py -q -k market_snapshot`

Run one live market-group collection and record total duration and per-provider duration in the manifest.

- [x] **Step 5: Verify GREEN**

Expected: tests PASS and live Yahoo provider uses one batch request before any fallback.

### Task 5: Economic Calendar Pipeline And Existing-Data Adapters

**Files:**
- Create: `reports/operational_events.py`
- Modify: `reports/source_pipeline.py`
- Modify: `agent_console/context.py`
- Create: `tests/test_operational_events.py`
- Modify: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `fetch_economic_calendar_events(days: int = 14) -> list[dict]`
- Produces: `load_existing_operational_events(now=None) -> list[dict]` for DART disclosure cache and KR microstructure snapshots without new upstream calls

- [x] **Step 1: Write failing adapter tests**

Assert calendar times/importances normalize, duplicate scheduled events remain immutable content, cached DART/KR records expose original source time, and stale cached records are excluded.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_operational_events.py tests/test_agent_console.py -q -k 'calendar or operational'`

Expected: FAIL because the adapter module does not exist.

- [x] **Step 3: Implement calendar and cache adapters**

Reuse `providers.econ_calendar.upcoming_events`; do not add duplicate DART or KR network calls. Normalize source refs and freshness.

- [x] **Step 4: Register calendar collection and context exposure**

Add the calendar group and compact upcoming-event context. Existing DART/KR context remains authoritative when fresher.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_operational_events.py tests/test_agent_console.py -q -k 'calendar or operational'`

Expected: PASS.

### Task 6: Split Cron Schedules And Drift Check

**Files:**
- Modify: `deploy/crontab.stock-report`
- Create: `scripts/check_crontab_drift.py`
- Create: `tests/test_crontab_drift.py`
- Modify: `docs/data-collection-pipeline.md`

**Interfaces:**
- Produces: `relevant_cron_lines(text: str) -> set[str]`; CLI exits 0 when installed relevant lines match, 1 on drift

- [x] **Step 1: Write failing drift tests**

Assert whitespace/comments are normalized, source group lines and wiki `--limit` differences are detected, and unrelated user cron lines are ignored.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_crontab_drift.py -q`

Expected: FAIL because the checker does not exist.

- [x] **Step 3: Replace the monolithic source cron**

Add separate `flock` lines for news, market, prediction, macro, and calendar groups. Keep source-wiki after news/prediction collection and use `--limit 0`.

- [x] **Step 4: Implement and run drift check**

Run: `.venv/bin/python scripts/check_crontab_drift.py --installed-file <(crontab -l)` in an interactive shell, or pipe `crontab -l` to a temporary read-only comparison file.

- [x] **Step 5: Apply the checked-in crontab and verify**

Run: `crontab deploy/crontab.stock-report`

Run: `.venv/bin/python scripts/check_crontab_drift.py`

Expected: exit 0 and installed source-wiki line contains `--limit 0`.
