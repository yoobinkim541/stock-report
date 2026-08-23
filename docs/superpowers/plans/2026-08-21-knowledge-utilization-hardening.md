# Knowledge Utilization Hardening Implementation Plan

> ## ⏳ Task 1~5 완료 · Task 6 미착수 (2026-08-23 확인)
>
> **Task 1~5**: 다른 세션이 구현·워크트리에 미커밋 상태로 남겨둔 것을 검증 후 대신
> 커밋(`3c314a8`). 선언된 파일 전부 존재 확인 + 관련 테스트 167건 통과.
>
> **Task 6(End-To-End Evidence Trace)**: `scripts/trace_source_evidence.py`,
> `tests/test_source_evidence_trace.py` 둘 다 존재하지 않는다 — 구현되지 않은 신규
> 기능이라 "미커밋 작업 마무리"의 범위가 아니라고 판단해 임의로 만들지 않았다.
> 6단계 파이프라인 트레이스(collected→persisted→wiki_saved→qmd_exported→
> qmd_retrieved→context_used) + CLI 종료코드 계약 + 헬스체크 연동을 요구하는
> 별도 기능 규모라, 진행 여부는 확인 후 결정 바람.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure collected evidence is exported, indexed, retrieved, and visibly used by the AI console, while exposing heuristic fallbacks and unused knowledge.

**Architecture:** Add provenance to labels, make QMD export/index a write-path responsibility, strengthen health with executable probes and stage counters, and add an end-to-end evidence trace. Shared memory remains the source of truth; QMD remains a derived search index.

**Tech Stack:** Python 3.11, existing shared-memory JSONL, QMD CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-resilient-source-pipeline-design.md`

## Global Constraints

- Shared memory is authoritative; QMD is a rebuildable derived index.
- Read APIs must not require write permission merely to inspect existing records.
- A health check must execute a real QMD query, not infer health from installation.
- Heuristic labels must never be reported as LLM labels.
- AI context must exclude stale/blocked rows and retain source/observation time.
- No human-review gate is added.

---

### Task 1: Label Provenance And Fallback Health

**Files:**
- Modify: `providers/news_labels.py`
- Modify: `crons/news_llm_snapshot.py`
- Modify: `tests/test_news_labels.py`

**Interfaces:**
- Produces on every label: `label_method`, `label_provider`, `label_model`, `label_error`, `labeled_at`
- Produces: `label_health(labels: list[dict], *, hours: int = 24) -> dict`

- [x] **Step 1: Write failing provenance tests**

Assert valid LLM output is tagged `llm`, command failure fallback is tagged `heuristic` with a nonempty reason, parser-invalid output is tagged heuristic, and health computes fallback ratio.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_news_labels.py -q`

Expected: FAIL because labels have no generation provenance.

- [x] **Step 3: Implement provenance without changing fact guards**

Keep ticker/event/direction/strength validation. Attach provenance after parsing trusted event-derived fields so an LLM cannot invent it.

- [x] **Step 4: Expose cron summary**

Log `llm_count`, `heuristic_count`, fallback ratio, provider/model, and first failure reason per run.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_news_labels.py -q`

Expected: PASS.

### Task 2: Read-Only Shared Memory Inspection

**Files:**
- Modify: `agent_console/shared_memory.py`
- Modify: `agent_console/wiki.py`
- Modify: `tests/test_agent_console.py`

**Interfaces:**
- Preserves: `all_records() -> list[dict]`
- Produces: `inspect_records() -> list[dict]` that never calls `ensure_store()` or writes files

- [x] **Step 1: Write failing read-only tests**

Create an existing events file under a read-only directory and assert wiki listing/stats can read it without touching index or summary files. Assert missing stores return empty rows.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_agent_console.py -q -k read_only_shared_memory`

Expected: FAIL because current reads call `ensure_store()`.

- [x] **Step 3: Implement side-effect-free inspection**

Use the existing bounded JSONL parser directly. Keep mutating APIs responsible for `ensure_store()`.

- [x] **Step 4: Stop swallowing inspection failures as empty healthy state**

Wiki health returns an explicit `read_error` while normal list APIs may still return an empty list for UI resilience.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_agent_console.py -q -k 'read_only_shared_memory or wiki_search_health'`

Expected: PASS.

### Task 3: QMD Export And Executable Health Probe

**Files:**
- Modify: `agent_console/qmd_search.py`
- Modify: `agent_console/wiki.py`
- Modify: `reports/source_wiki_curator.py`
- Modify: `tests/test_qmd_search.py`
- Modify: `tests/test_source_wiki_curator.py`

**Interfaces:**
- Produces: `sync_pages(pages: list[dict], *, runner=subprocess.run) -> dict`
- Extends: `health(*, probe_query: str = "시장") -> dict` with `query_ok`, `index_fresh`, `latest_page_at`, `latest_export_at`, `error`

- [x] **Step 1: Write failing synchronization tests**

Assert batch curator save exports every saved page, invokes QMD update once per batch, health fails when export is older than shared-memory update, and a failed query marks `query_ok=false` even when the binary exists.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_qmd_search.py tests/test_source_wiki_curator.py -q -k 'sync or health or export'`

Expected: FAIL on missing synchronization contract.

- [x] **Step 3: Implement batch sync**

Write markdown files, remove derived files for deleted wiki ids, then call the configured QMD update command once. Return counts and errors without hiding failures.

- [x] **Step 4: Wire sync after successful wiki batches**

Source curator and merge/split batch paths call `sync_pages` after shared-memory commit. A QMD failure does not roll back source-of-truth data but is recorded as degraded health.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_qmd_search.py tests/test_source_wiki_curator.py -q`

Expected: PASS.

### Task 4: Curator Stage Metrics And Delta Updates

**Files:**
- Modify: `reports/source_wiki_curator.py`
- Modify: `reports/wiki_pipeline_health.py`
- Modify: `tests/test_source_wiki_curator.py`
- Modify: `tests/test_wiki_pipeline_health.py`

**Interfaces:**
- Extends curator result with: `candidate_count`, `saved_count`, `unchanged_count`, `failed_count`, `qmd_exported_count`, `duration_ms`
- Produces: `evidence_fingerprint(events: list[dict]) -> str`

- [x] **Step 1: Write failing delta tests**

Assert an unchanged evidence fingerprint skips the write/enrichment path, a new observation changes the fingerprint, all eligible candidates save when limit is zero, and per-page failures do not hide successful pages.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_source_wiki_curator.py -q -k 'fingerprint or metrics or limit_zero'`

Expected: FAIL on missing fingerprint/stage metrics.

- [x] **Step 3: Implement deterministic delta curation**

Fingerprint sorted evidence ids plus mutable observation values. Preserve existing page usage/feedback metadata when content updates.

- [x] **Step 4: Add stage metrics to pipeline health**

Flag candidate-to-saved gaps, failed QMD export, and repeated unchanged broad pages. Do not treat zero new evidence as a collection failure.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_source_wiki_curator.py tests/test_wiki_pipeline_health.py -q`

Expected: PASS.

### Task 5: Retrieval And Answer-Use Telemetry

**Files:**
- Create: `agent_console/evidence_usage.py`
- Modify: `agent_console/wiki.py`
- Modify: `agent_console/agent.py`
- Modify: `reports/wiki_pipeline_health.py`
- Create: `tests/test_evidence_usage.py`
- Modify: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `record_retrieval(query_id: str, page_ids: list[str], provider: str, fallback: bool) -> None`
- Produces: `record_context_use(query_id: str, evidence_ids: list[str]) -> None`
- Produces: `usage_summary(*, hours: int = 24) -> dict`

- [x] **Step 1: Write failing telemetry tests**

Assert retrieval hits/fallbacks are append-only, context use references only returned page/evidence ids, repeated query ids are idempotent, and health reports unused-page and retrieval-to-context conversion counts.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_evidence_usage.py -q`

Expected: FAIL because telemetry storage does not exist.

- [x] **Step 3: Implement lightweight telemetry**

Use a locked JSONL file under the existing shared-memory directory. Never include full user questions; store a hash/query id, ids, timestamps, provider, and fallback flag.

- [x] **Step 4: Wire retrieval and prompt-context use**

Record after QMD/fallback ranking and after final context assembly. Failures are nonblocking but visible in health.

- [x] **Step 5: Run tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_evidence_usage.py tests/test_agent_console.py -q -k 'evidence_usage or wiki'`

Expected: PASS.

### Task 6: End-To-End Evidence Trace And Operations Audit

**Files:**
- Create: `scripts/trace_source_evidence.py`
- Modify: `reports/wiki_health_check.py`
- Create: `tests/test_source_evidence_trace.py`
- Modify: `docs/data-collection-pipeline.md`

**Interfaces:**
- Produces: trace JSON stages `collected`, `persisted`, `wiki_saved`, `qmd_exported`, `qmd_retrieved`, `context_used`
- CLI exits 0 only when every required stage succeeds; unavailable optional providers are reported separately

- [ ] **Step 1: Write failing trace tests**

Use temporary stores and fake QMD runner to assert a complete trace exits healthy and deliberately missing persistence/index/context stages identify the exact failed stage.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest tests/test_source_evidence_trace.py -q`

Expected: FAIL because the trace tool does not exist.

- [ ] **Step 3: Implement the trace and health integration**

Use one synthetic, clearly tagged evidence row in temporary mode for tests. Production mode selects a recent real source row and never writes fabricated market evidence.

- [ ] **Step 4: Run focused and regression suites**

Run: `.venv/bin/pytest tests/test_source_evidence_trace.py tests/test_qmd_search.py tests/test_source_wiki_curator.py tests/test_wiki_pipeline_health.py tests/test_agent_console.py tests/test_news_labels.py -q`

- [ ] **Step 5: Run production read-only trace and document findings**

Run: `.venv/bin/python scripts/trace_source_evidence.py --read-only`

Update the data-pipeline document with measured stage counts, remaining limitations, and the exact operational commands.

Expected: the trace reports fresh collected/persisted evidence, current QMD export/query state, and whether the evidence entered AI context.

