# Source-Compiled Wiki Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile each eligible original once and incrementally integrate its dated, attributed knowledge into related wiki pages without losing old knowledge or duplicating retries.

**Architecture:** Persist source-revision compilation receipts separately from per-target integration operations. Reuse the existing raw archive, evidence classification, wiki storage, lossless child pages and markdown/search artifacts. Guard multi-page updates against semantic revisions under the existing store lock; recover journal and artifact stages independently.

**Tech Stack:** Existing Python 3.11 environment, pytest, structured JSON, SHA-256, `pathlib`, existing `safe_io` atomic writes and file locks; no new database or dependency.

**Spec:** `/home/ubuntu/projects/stock-report/docs/superpowers/specs/2026-10-09-source-compiled-wiki-design.md`

Status: user-approved for direct TDD execution on 2026-10-09; implementation in progress. Production activation remains gated.

## Global Constraints

- Preserve query-time **1,600-character** wiki excerpts; complete ingest and stored prose are separate contracts.
- Preserve current budgets: **20,000 source characters**, **80,000 aggregate characters**, **8 aggregate articles**, wiki body **12,000** and split target **9,000** characters. An oversized individual source is `pending_chunking`, not compiled.
- Raw bytes are immutable; retention, resumable source chunking, credentials and paid compute require separate approval. Missing raw/provenance is unresolved, not reconstructed from a URL.
- Keep actual evidence IDs; hash full versioned provider/URL/raw/text/scope/provenance identity, not titles or shortened identifiers.
- Keep existing digest/manual/chat APIs. Do not introduce a second chatbot, unrestricted page replacement or mass legacy-state migration.
- Generated additions/new pages remain `draft`; structural validity, anchor matching and model confidence do not prove truth.
- Tests use temporary stores and injected fake models, never live private data or paid calls. No external private-payload approval or protected-source collection approval is inferred.
- Preserve unrelated dirty-worktree changes. Use explicit file staging after inspecting diffs; commits start with Korean `add)` or `fix)` and include work, result and trade-off in their bodies.

## Review Focus

1. A repeated delivery changes metadata/evidence ID but not content: reuse compilation while associating the newly observed ID (Task 1).
2. A child page or its ordering changes while a proposal is being generated: reject the stale whole-parent operation, not overwrite that child (Task 5).
3. Historical references exceed legacy proposal/storage caps (13/21 references): preserve all in discoverable provenance children (Task 5).
4. A process dies after the data write but before receipt/log completion: recover without duplicate additions or log entries (Task 6).
5. A model conflates differing horizons or follows instructions in article text: reject forged operations and expose semantic uncertainty (Tasks 3, 4 and 7).

---

## File Ownership And Shared Contracts

All paths below are relative to `/home/ubuntu/projects/stock-report`.

| File | Responsibility |
| --- | --- |
| `reports/wiki_source_revision.py` | Source discovery, body/provenance gate, versioned identity |
| `reports/wiki_source_journal.py` | Atomic receipts, compile artifacts, operation locks and transitions |
| `reports/wiki_source_compiler.py` | Full-source prompt and strictly validated compiled knowledge |
| `reports/wiki_source_integration.py` | Candidate routing, full target hydration, dated append/relation proposals |
| `reports/wiki_source_pipeline.py` | Worker orchestration, recovery, stage counts and CLI |
| `agent_console/shared_memory.py` | Narrow guarded atomic batch API; existing API unchanged |
| `agent_console/wiki.py` | Pure split preparation, semantic snapshots, validated batch commit, idempotent operation logs |
| `reports/wiki_distillation.py` | Opt-in entrypoint dispatch; legacy `run` unchanged |

Use documented `TypedDict` contracts in the owning modules; JSON serialization uses dictionaries. `llm_fn` is `Callable[[str], str | None]`, always injectable. Invalid model output raises `ValueError` and yields a failed receipt, never heuristic success. Assertion snippets use local fixture/helper names defined in their test modules, not additional production APIs.

- **SourceRevision** (Task 1): `revision_id`, `protocol_version=1`, `provider`, `canonical_url`, `raw_sha256`, `text_sha256`, `scope`, `provenance_class`, `body`, `title`, separate `published_at/collected_at/ingested_at`, `raw_ref/text_ref/manifest_ref`, `observed_evidence_ids: list[str]`, `role: support|conflict`. `revision_id` is full SHA-256 of sorted structured identity JSON, excluding body, title, timestamps, role and evidence IDs but including both full hashes.
- **DiscoveryResult**: `eligible: list[SourceRevision]`, `unavailable: list[dict]`, `pending_chunking: list[dict]`; each unresolved item includes a stable discovery ID, refs and exact reason, not an invented source revision.
- **CompiledSource** (Task 3): `revision_id`, `body`, `claims: list[dict]`, `terms: list[str]`, `limitations: list[str]`, `skip_reason: str | None`. Claim fields: `claim_id`, `text`, `as_of`, `scope`, `anchor: {start,end,quote} | None`, `competing_claim_ids: list[str]`; citations resolve to the server-owned revision. Assign stable claim IDs as SHA-256 of revision ID plus validated claim ordinal.
- **TargetSnapshot** (Task 4): `page_id`, `revision_hash`, `page`, `children`, `body`, `claims`, `source_refs`, `evidence_ids`, `conflicting_evidence_ids`. `body` reconstructs ordered active split children; retain parent overview separately in `page`.
- **RoutingState**: persisted `candidate_ids`, `cursor`, `examined`, `selected`, `rejected`, `new_page_specs`, `unexamined`; candidate exhaustion is not proof of semantic routing coverage.
- **IntegrationProposal** (Task 4): `revision_id`, `role`, `targets: list[dict]`. Existing target entries contain `page_id`, `before_hash`, `additions` and `relations`; new entries also require `title/kind/surface/purpose`. Additions identify compiled claim IDs and dated prose; relation fields are `type: support|qualify|supersede|compete`, `from_claim_id`, `to_claim_id`, both source revisions, dates and scope explanation. No deletes or model-selected trust status.
- **PreparedBatch** (Task 5): `operation_id`, `revision_id`, `expected_before: dict[str,str | None]`, `expected_after: dict[str,str | None]`, `upserts: list[dict]`, `snapshots: list[dict]`, `target_ids: list[str]`. Include child/new IDs and parent semantic child sets in guards. Persist normalized output and fixed timestamps before write; operation ID hashes canonical revision/role/base/proposal content.
- **OperationReceipt** (Task 2): `schema_version=1`, IDs, prepared batch, `stage: prepared|applied|committed|stale-base|failed`, `artifacts: pending|done|failed`, `search: pending|done|failed`, `log: pending|done|failed`, stage timestamps and safe error codes. `committed` means verified data, not all side effects or factual review.

### Task 1: Discover Independent, Verified Source Revisions

**Files:** Create `reports/wiki_source_revision.py`; test `tests/test_wiki_source_revision.py`. Reuse `reports/raw_archive.py`, `reports/evidence_cards.py` and source gates in `reports/wiki_distillation.py` without changing provider policy.

**Interfaces:**
- Produce `discover_revisions(events: list[dict], *, now: datetime, evidence_roles: dict[str,str] | None = None) -> DiscoveryResult` and `build_revision(event: dict, *, now: datetime) -> SourceRevision`; gate failures raise `ValueError` with stable reason codes. Trusted existing evidence-role associations determine role; unassociated sources default to support, not model-invented conflict. Group by `(revision_id, role)` when both roles occur, sharing one compilation and preserving each group's actual IDs.
- Read configured archive roots and full bytes through existing archive APIs; derive exact observed IDs through `event_to_evidence_card`. Only explicit allowed URL normalization; preserve identity-bearing query parameters when provider policy permits them. No URL-only evidence association.

- [x] **Write failing tests** `test_complete_source_progresses_independently`, `test_redelivery_reuses_revision_and_actual_ids`, `test_role_change_reuses_compilation_identity`, plus changed bytes/text/scope, provider mismatch, path escape, login/challenge, OCR/community/FRED scope, expired manifest/raw and oversize cases. Pin assertions:

```python
assert result["eligible"][0]["body"] == complete_a_body
assert result["unavailable"][0]["reason"] == "raw_unavailable"
assert first["revision_id"] == redelivery["revision_id"]
assert changed_text["revision_id"] != first["revision_id"]
assert changed_role["revision_id"] == first["revision_id"]
assert set(redelivery["observed_evidence_ids"]) == {actual_id_a, actual_id_b}
assert oversized_result["pending_chunking"][0]["reason"] == "source_budget_exceeded"
```

- [x] **RED:** `.venv/bin/python -m pytest tests/test_wiki_source_revision.py -q`; expect import/API failure, then genuine behavior failures as implemented.
- [x] **Implement discovery and gate:** verify manifest/provider/URL agreement and both complete hashes from actual stored bytes/text. Deduplicate deliveries by revision, union only observed IDs; retain source-specific attribution. One inaccessible source must not stop another.
- [x] **GREEN:** rerun the command plus `.venv/bin/python -m pytest tests/test_wiki_full_body_source_types.py tests/test_wiki_fred_note_pipeline.py tests/test_telegram_fulltext_pipeline.py -q`; all pass, raw bytes unchanged.
- [x] **Commit only Task 1 files:** `add) 원문별 검증과 안정적인 컴파일 식별자 추가`; body states independent progress, exact evidence association and stricter unresolved-source reporting.

### Task 2: Persist Compilation And Integration Receipts

**Files:** Create `reports/wiki_source_journal.py`; test `tests/test_wiki_source_journal.py`.

**Interfaces:**
- Produce `Journal(root: Path | None = None)` with root default `raw_archive.reports_root() / "wiki-source-v1"`.
- Methods: `source_lock(revision_id: str) -> ContextManager`, `target_locks(ids: list[str]) -> ContextManager`, `load_source(revision_id: str) -> dict | None`, `record_attempt(source: SourceRevision, *, state: str, error_code: str = "") -> dict`, `save_compilation(source: SourceRevision, compiled: CompiledSource) -> dict`, `load_compilation(receipt: dict) -> CompiledSource`, `save_routing(revision_id: str, role: str, state: RoutingState) -> None`, `prepare(batch: PreparedBatch) -> OperationReceipt`, `transition(operation_id: str, *, stage: str | None = None, effects: dict | None = None, error_code: str = "") -> OperationReceipt`, `pending_operations() -> list[OperationReceipt]`.
- Source receipt fields: `schema_version`, source metadata without body, observed IDs, `compile_state: pending|compiled|skipped|failed`, compile artifact ref/hash, skip/error reason and per-role routing state. Discovery-unavailable records have their own namespace, not fake revision keys.

- [x] **Write failing tests** `test_compiled_requires_durable_full_artifact`, `test_corrupt_receipt_fails_closed`, `test_source_lock_serializes_processes`, plus durable skipped/failed reasons, invalid transitions, traversal IDs, observed-ID union and role-specific routing. Pin:

```python
assert read_compiled_body(receipt) == entire_synthesis
assert skipped["compile_state"] == "skipped" and skipped["skip_reason"]
assert interrupted["compile_state"] != "compiled"
assert corrupt_receipt_error == "journal_corrupt"  # never reset to an empty store
assert lock_acquisition_order == sorted(set(target_ids))
```

- [x] **RED:** `.venv/bin/python -m pytest tests/test_wiki_source_journal.py -q`; expect missing Journal/API failures.
- [x] **Implement journal:** structured atomic writes via `safe_io`; immutable content-addressed compile artifact; hash verification on read; finite lock timeout reported as retryable. Acquire source lock, sorted target locks, then store lock; no reverse acquisition. Record safe codes/IDs, not article text in log/error metadata.
- [x] **GREEN:** same command; inject crashes before/after artifact rename and receipt rename, verify incomplete/corrupt state is explicit and complete synthesis survives.
- [x] **Commit only Task 2 files:** `add) 원문 컴파일 영수증과 통합 복구 기록 추가`; describe additional local metadata/storage trade-off.

### Task 3: Compile Source-Backed Draft Knowledge

**Files:** Create `reports/wiki_source_compiler.py`; test `tests/test_wiki_source_compiler.py`.

**Interfaces:** Consume SourceRevision; produce `compile_source(source: SourceRevision, *, llm_fn: Callable[[str], str | None]) -> CompiledSource` and `validate_compilation(payload: dict, source: SourceRevision) -> CompiledSource`. Task 2 persists the validated result; compiler never writes wiki pages.

- [x] **Write failing tests** `test_complete_source_and_synthesis_tail_preserved`, `test_exact_anchor_offsets`, `test_source_instructions_cannot_forge_provenance`, plus reusable prose/stable claims, skipped rationale, malformed/duplicate-key/nonfinite JSON and status promotion. Pin:

```python
assert source["body"] in captured_prompt
assert source["body"][start:end] == anchor["quote"]
assert compiled["body"].endswith(required_tail)
assert compiled["claims"][0]["claim_id"] == expected_stable_claim_id
assert invalid_output_raises_value_error
assert not wiki_write_calls
```

- [x] **RED:** `.venv/bin/python -m pytest tests/test_wiki_source_compiler.py -q`; expect missing APIs.
- [x] **Implement compiler:** prompt delimiting source as untrusted data, strict whole-response JSON (maximum 100,000 characters, depth 64; reuse existing strict parser rules), explicit schema allowlist, generated citation resolution, exact anchor checks. Require either nonempty synthesis/claims or nonempty skip reason. Retain limitations and community/OCR attribution; no source instruction execution.
- [x] **GREEN:** same command; model is a fixture callable. Show structural checks separately from factual judgments; no live model calls.
- [x] **Commit only Task 3 files:** `add) 원문 근거를 보존하는 지식 컴파일러 추가`; note draft outputs and unresolved semantic accuracy.

### Task 4: Route Knowledge And Hydrate Complete Target History

**Files:** Create `reports/wiki_source_integration.py`; narrowly add semantic snapshot helper in `agent_console/wiki.py`; test `tests/test_wiki_source_integration.py`.

**Interfaces:**
- `discover_targets(compiled: CompiledSource, *, surface: str) -> RoutingState` uses existing index/search and verified aliases, active eligible kinds only.
- `route_pass(compiled: CompiledSource, state: RoutingState, *, llm_fn: Callable[[str], str | None], limit: int = 20) -> RoutingState`; persist full candidate ID snapshot and examined/rejected reasons, advance cursor only after validated results.
- Produce `wiki.semantic_snapshot(page_id: str, *, records: list[dict] | None = None) -> TargetSnapshot` and `hydrate_target(page_id: str) -> TargetSnapshot` delegating to it; supplied records use a pure projection without another store read. Task 5 reuses the same projection under lock.
- `propose_integration(compiled: CompiledSource, source: SourceRevision, targets: list[TargetSnapshot], *, new_page_specs: list[dict], llm_fn: Callable[[str], str | None]) -> IntegrationProposal`.

- [x] **Write failing tests** `test_source_routes_to_two_pages_and_justified_new_page`, `test_hydration_includes_ordered_children_and_exceptions`, `test_routing_cursor_retains_unexamined_candidates`, plus archived/unknown IDs, title-only collisions, generic `속보` tags and dated relations. Pin:

```python
assert set(state["selected"]) == {entity_id, topic_id}
assert state["unexamined"] == remaining_candidate_ids
assert snapshot["body"] == "".join(ordered_child_bodies)
assert old_exception in captured_prompt and old_counterevidence in captured_prompt
assert relation["type"] == "compete" and relation["scope_explanation"]
assert forged_target_rejected and unrelated_page_unchanged
```

- [x] **RED:** `.venv/bin/python -m pytest tests/test_wiki_source_integration.py -q`; expect missing routing and snapshot APIs.
- [x] **Implement routing/proposals and semantic snapshot:** exact ID allowlists, no archived resurrection/cross-surface writes; new page purpose required. Keep source dates and source revisions for both ends of relations. Hash title/summary/body/surface/kind/trust state/refs/evidence roles/claim ledger/links and ordered active child identities/content; exclude derived backlinks, update timestamps and `useCount/lastUsedAt/lastQuery`. Model proposes append/relation operations only; preserve prior knowledge rather than rewrite it.
- [x] **GREEN:** `.venv/bin/python -m pytest tests/test_wiki_source_integration.py -q`; all pass including complete hydration and semantic hash sensitivity to child/role changes, insensitivity to usage counters.
- [x] **Commit scoped Task 4 files/hunks:** `add) 지식의 다중 위키 연결과 통합 제안 추가`; describe bounded candidate coverage, not guaranteed semantic completeness. Preserve existing dirty edits in `wiki.py`.

### Task 5: Guard Atomic Multi-Page Integration And Lossless Splits

**Files:** Modify `agent_console/wiki.py` split/record/snapshot helpers, `agent_console/shared_memory.py` batch area; test `tests/test_wiki_source_atomic_integration.py`. Do not restructure unrelated sections.

**Interfaces:**
- Consume Task 4 `wiki.semantic_snapshot`; produce `wiki.prepare_source_batch(proposal: IntegrationProposal, compiled: CompiledSource, source: SourceRevision) -> PreparedBatch`.
- Extract pure `_prepare_split_records(page: dict, *, records: list[dict]) -> list[dict]`, reused by existing split save and new prepare. Normalize via existing `_build_wiki_record(existing=...)` and `shared_memory.normalize_record`; freeze resulting records before calculating after hashes.
- Produce `shared_memory.guarded_batch_upsert(*, upserts: list[dict], expected: dict[str,str | None], fingerprint: Callable[[str,list[dict]],str | None], preserve_fields: tuple[str,...] = ()) -> dict` returning `status: applied|stale-base`, `changed_ids`, `stale_ids`, `index_pending: bool`.
- Produce `wiki.apply_source_batch(batch: PreparedBatch) -> dict`; persist old snapshots first, call guarded API once, then invalidate read caches. Store-lock code never calls a public lock-taking API.

- [x] **Write failing tests** `test_stale_child_aborts_whole_batch`, `test_provenance_overflow_remains_discoverable` (13 and 21 refs, 101 evidence IDs), `test_two_workers_do_not_overwrite_knowledge`, plus old prose/exception/history, scoped dated additions, invalid relations, stale root/order, new-ID collision, long synthesis and usage increments. Pin:

```python
assert reconstructed_after.startswith(reconstructed_before)
assert all(ref in discoverable_provenance for ref in all_historical_refs)
assert child_changed_result["status"] == "stale-base"
assert stale_result_wrote_zero_records
assert unrelated_before == unrelated_after and raw_before == raw_after
assert reconstructed_long_synthesis == expected_complete_prose
assert winning_batches == 1 and actual_usage_count == concurrently_incremented_count
```

- [x] **RED:** `.venv/bin/python -m pytest tests/test_wiki_source_atomic_integration.py -q`; expect missing guarded/preparation API failures.
- [x] **Implement pure preparation and guarded batch:** reuse Task 4 semantic hashes, include expected absence for new IDs. Preserve `useCount/lastUsedAt/lastQuery` from locked current rows, reject every semantic change. Validate complete batch before atomic JSONL replacement; index failure after replacement returns applied-with-index-pending, not false non-application.
- [x] **Implement provenance/history storage:** retain old records via snapshots; store full claim ledger in `source_knowledge` metadata passed through record/page normalization. Metadata reference caps remain unchanged; extra references/claims go in deterministic provenance children with parent IDs so links beyond the cap remain discoverable. Archive obsolete split children, never delete history. Existing trusted page is not silently demoted, but new claims carry draft status and cannot promote page trust.
- [x] **GREEN:** `.venv/bin/python -m pytest tests/test_wiki_source_atomic_integration.py tests/test_wiki_source_integration.py tests/test_wiki_merge_overflow.py tests/test_wiki_markdown_integrity.py tests/test_wiki_stale_knowledge_protection.py -q`; all pass. Test lock-timeout/error paths and after-normalization hashes explicitly.
- [x] **Commit only intended hunks:** `add) 원문 지식을 원자적으로 통합하고 기존 근거 보존`; body explains narrow helpers, CAS retries and extra history/provenance storage. Existing dirty changes in these files need review before staging.

### Task 6: Orchestrate Recovery, Artifacts And Single-Writer Rollout

**Files:** Create `reports/wiki_source_pipeline.py`; modify `reports/wiki_distillation.py::main/run`, `agent_console/wiki.py::_append_wiki_log/rebuild_artifacts`; test `tests/test_wiki_source_pipeline.py` and `tests/test_wiki_source_recovery.py`.

**Interfaces:**
- `run(*, events: list[dict] | None = None, dry_run: bool = True, llm_fn: Callable[[str], str | None] | None = None, source_limit: int = 5, target_limit: int = 20, now: datetime | None = None) -> dict`.
- `recover_operation(receipt: OperationReceipt, *, journal: Journal) -> dict`; result includes `operation_id`, `stage`, `effects`, `reason`.
- Run result: independent `discovered/eligible/unavailable/pending_chunking/compiled/skipped/compile_failed/routing_pending/data_committed/effects_pending/stale_base` counts, safe reason counts and operation IDs. No single blanket `completed` count.
- Optional `operation_id: str | None = None` in `_append_wiki_log` and `rebuild_artifacts`; existing callers retain behavior. Check-and-append under the existing log flock, deduplicate `(action, full operation_id)`, metadata only.
- CLI: `--dry-run`, `--recover-only`, `--source-limit`, `--target-limit`, `--status`; no automatic activation. `WIKI_SOURCE_PIPELINE_ENABLED` defaults false. Existing distiller CLI dispatches exclusively to new worker when true, otherwise legacy `run`; direct legacy `run` must also filter out `source_digest` candidates when enabled, retaining its return shape and other legacy/manual behavior.

- [x] **Write failing tests** `test_crash_after_data_write_recovers_without_duplicates`, `test_role_change_does_not_recompile_source`, `test_enabled_worker_excludes_direct_legacy_source_writes`, plus A succeeding/B unavailable, source cursor progress and exclusive CLI dispatch. Parameterize crashes at compile artifact/receipt, prepared receipt, JSONL rename, applied receipt, index, markdown/search, log append and committed/effect receipts. Pin:

```python
assert compile_calls_after_retry == 1
assert count_source_additions(target) == 1 and count_ingest_logs(op_id) == 1
assert all_after_hashes_match and receipt["stage"] == "committed"
assert failed_artifact_receipt["artifacts"] == "failed"
assert dry_run_model_calls == 0 and dry_run_write_calls == 0
assert legacy_calls + new_worker_calls == 1
```

- [x] **RED:** `.venv/bin/python -m pytest tests/test_wiki_source_pipeline.py tests/test_wiki_source_recovery.py -q`; expect missing worker/recovery API failures.
- [x] **Implement orchestration/recovery:** under source and sorted target locks, persist prepare before atomic apply. Recovery verifies current semantic hashes: all-after commits without replay, all-before retries guarded apply, any other state becomes stale-base and replans; unreadable store/journal fails closed. Compilation receipt reuse unions newly observed IDs and schedules provenance-only updates when target attribution needs them. Commit durable full compiled prose as a draft `note` tagged `compiled_source`, with deterministic ID `sha256("compiled-source:" + revision_id)`, through the same batch path even when no entity target exists. Cite server-owned allowed original URL/raw/text refs and link the compiled page ID; journal paths are not passed as arbitrary source refs. Do not create empty pages or introduce an unsupported wiki kind.
- [x] **Implement side-effect stages:** reuse markdown rebuild, complete QMD sync and index APIs, separately tracked/retryable. Log operation once under flock; rebuild logging remains idempotent for that operation. No body/credentials/private query text in operational logs. Existing legacy knowledge and receipts stay untouched during exact permitted backfill.
- [x] **GREEN:** same tests plus `.venv/bin/python -m pytest tests/test_wiki_distillation.py tests/test_wiki_refresh_retry.py tests/test_wiki_pipeline_health.py -q`; all pass. Recovery tests spawn separate processes rather than simulate concurrency with only a thread mock.
- [x] **Commit scoped files:** `add) 위키 컴파일 복구 워커와 단계별 운영 상태 추가`; body distinguishes data commit/effects/factual validation and default-off rollout.

### Task 7: Acceptance, Semantic Evaluation And Operator Handoff

**Files:** Create `tests/test_wiki_source_acceptance.py`, `tests/fixtures/wiki_source_semantics.json`, `docs/wiki-source-compilation.md`; modify `config/wiki-maintainer.md`, `docs/superpowers/plans/2026-10-07-llm-wiki-acceptance.md`. Keep existing query/file-back/schema behavior.

**Interfaces:** Consume Task 6 run/status CLI and existing query/file-back APIs. Semantic fixtures contain full permitted synthetic input, prior knowledge, expected attribution/dates/scopes, required caveats, forbidden causal claims, competing-vs-different-horizon labels and reviewer verdict fields; not model-generated correctness labels.

- [x] **Write failing end-to-end tests** `test_source_knowledge_compounds_across_pages`, `test_query_fileback_and_excerpt_contract_remain_compatible`, `test_unavailable_sources_never_count_as_completed`, plus markdown/index/log, source links, old exceptions, unrelated/raw immutability and stable retry. Pin:

```python
assert set(updated_target_ids) == {entity_id, topic_id, justified_new_id}
assert full_synthesis_tail in reconstructed_compiled_page
assert query_excerpt_length <= 1600
assert query_capture_survives_next_ingest
assert source_access_status != "verified" when original_is_unavailable
assert all_required_caveats_present and not fabricated_contradiction
```

- [x] **RED:** `.venv/bin/python -m pytest tests/test_wiki_source_acceptance.py -q`; expect contract failures against missing integration/fixtures, not intentionally false assertions.
- [x] **Complete acceptance fixtures and operator documentation:** explain discovery/compile/integration/effects states, permitted backfill, recovery commands, retained legacy knowledge, source access limitations and rollback by disabling flag (retains committed wiki knowledge). Document which existing callers are sole worker owners; audit cron/manual entrypoints so aggregate `run` cannot simultaneously own enabled sources.
- [x] **GREEN focused/regression:** `.venv/bin/python -m pytest tests/test_wiki_source_*.py tests/test_llm_wiki_acceptance.py tests/test_wiki_chat_provenance.py tests/test_wiki_maintenance.py tests/test_hermes_source_webhook.py -q`; then `.venv/bin/python -m pytest -q` and `git diff --check`. Report fresh actual results, never recycle prior suite counts.
- [ ] **Run labelled semantic review:** use requested Luna/Sol only if available; otherwise state missing independent review. Evaluate entailment, mechanisms, competing horizons, attribution, overconfidence and routing omissions separately from structural test scores. No external private corpus is sent without separate approval.
- [ ] **Supervised permitted-source probe:** only after plan execution/provider-payload approvals. Capture exact hashes, compile artifact, before/after IDs, preserved claims, markdown/index/log and query citation. Use synthetic/local-authorized inputs while protected source access is unavailable; do not claim live SaveTicker recovery. Activation requires user review of evidence, semantic review and an actually permitted source; leave production flag off until then.
- [ ] **Commit scoped docs/tests:** `add) 원문 지식화 수용 검증과 운영 가이드 추가`; include evidence/results and the strict-gating cost. Deploy/restart only after activation approval, then verify worker status and both wiki views without invoking unapproved models.

## Plan Self-Review And Handoff

Implementation verification on 2026-10-10: latest focused suite 235 passed;
staged-only source/schema/health/acceptance suite 139 passed. Whole-project run
3,953 passed / 2 skipped / 41 warnings, begun before the last native Telegram
and newly routed concept retry corrections; no newer whole-suite result is
claimed. Read-only real-source discovery found 100 eligible archived Telegram
bodies with zero model calls/writes. Requested 6.1 Sol code review is underway;
model-output semantic review, protected-source collection and activation remain
open. Task 7 is not blanket-complete merely because structural tests passed.

- Identity/discovery/body/raw access: Task 1; durable compilation/role receipts: Tasks 2-3.
- Full hydration/routing/dated conflict/history/provenance: Tasks 4-5; store-lock ownership and CAS: Task 5.
- Crash replay/log/index/search/compatibility/backfill: Task 6; query/file-back/schema, semantic checks and supervised rollout: Task 7.
- Five Review Focus cases have owning regressions; contract names and state fields match across tasks. Semantic snapshot/hydration is owned by Task 4 and reused under the Task 5 commit lock, so each task has a complete passing-test gate.
- New retention, source chunking, protected login and private external payload approvals remain outside this plan. These unresolved sources are not acceptance successes.
- Parent self-review completed; independent Luna/Sol plan review is not claimed because the latest requested workers hit usage limits.
- Review gate: the user approved this plan and direct implementation on 2026-10-09. Preserve the requested subagent models; do not silently substitute. Independent semantic review and production activation remain separate gates.

2026-10-11 verification continuation: source/recovery/integration tests 45 passed,
including an old-effect recovery plus failed-final-pass regression. Staged-only
source/schema/health tests 145 passed. Recorded real Sol synthetic compilation
samples passed runtime schema validation (acceptance suite 8 passed); independent
Luna review passed entailment, causal restraint, dates/scope, attribution and
overconfidence for all four cases. Routing omissions are not semantically tested.
Live dry-run found 82 eligible archived originals among 10,200 events; zero model
calls or wiki writes. These are current-window counts, not corpus-wide coverage.
Fresh full regression and final bounded code re-review are running. No runtime
activation or authenticated SaveTicker recovery is claimed.

Later 2026-10-11 evidence: fresh planned focused suite235 passed; staged-only
suite150 passed. Final bounded Sol code review found no further defect in its
reviewed scope. The user approved a small public-only Codex probe: initial output
added unsupported claim IDs; exact prompt schema added via RED/GREEN. A guarded
retry compiled successfully but failed routing and committed no wiki batch.
Chat language validation rejects machine-only routing JSON with Korean input;
a general structured-provider path is still required before a successful real
end-to-end probe. Production flag remains off, and Task7's supervised rollout
gate is not complete. Final post-prompt whole-project test result is pending.

Final post-prompt verification: `.venv/bin/python -m pytest -q` completed with
3,967 passed / 2 skipped / 41 warnings in 652.84s. The exact planned focused
command passed235; staged-only source/schema/health passed150. Both worktree and
staged diff checks are clean. The scoped implementation/docs/tests commit does
not mark semantic routing coverage, supervised integration or deployment done.
