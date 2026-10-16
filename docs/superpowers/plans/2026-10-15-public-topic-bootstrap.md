# Public Topic Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Accumulate verified public source claims in separate reusable topic pages without modifying source notes or originals.

**Architecture:** Select bounded read-only public source context, validate multi-source proposals against a server-owned claim/citation map, and persist topic changes through a dependency-checked journal transaction. Keep the current single-source flow intact and enable the new path only explicitly.

**Tech Stack:** Python, existing JSON journals and shared-memory store, pytest, existing Codex engine; no new provider or dependency.

**Spec:** `docs/superpowers/specs/2026-10-15-public-topic-bootstrap-design.md` (approved).

## Global Constraints

- Originals and source-specific notes are unchanged; new synthesis remains draft.
- Public allowlist, current fingerprints, original hashes and expiry are verified before exposure and commit.
- Maximum four context notes; complete claim units only; 12,000 serialized characters per note and 24,000 total.
- Five source attempts per run, twenty per KST day, failures included; no unreserved retries or historical replay.
- Chat excerpt remains 1,600 characters; no private model processing, trust promotion or broad activation.
- Multiple-file publication redesign and SaveTicker recovery are separate; do not imply they are fixed.
- Preserve unrelated dirty changes. Use task-specific commits only when git writes are permitted; Korean fix)/add) titles and bodies include changes, results and trade-offs.

## Review Focus

- Archive members may exist only inside compressed bundles: use archive-aware reads (Task 1).
- Same source can appear under several page IDs: deduplicate revision IDs (Task 1).
- Model may associate the same name with different events/time horizons: allow refusal, no forced merge (Task 2).
- A public page can change after prompt construction: revalidate dependency fingerprints (Task 3).
- A topic may have split children, stale effects or an already committed retry: preserve all claims and resume once (Tasks 3 and 5).

## File Map

- New `reports/wiki_topic_context.py`: typed context/dependency records, public validation and selection.
- New `reports/wiki_topic_integration.py`: routing proposal, source-map construction and multi-source validation.
- New `reports/wiki_topic_transaction.py`: topic batch preparation and dependency checks.
- Modify `reports/wiki_source_journal.py`: opt-in topic receipt protocol and recoverable effects.
- Modify `reports/wiki_source_pipeline.py`: guarded topic branch using the current reservation.
- Modify `scripts/run_public_wiki_rollout.py`: public wrapper checks for the new prompt shape.
- Modify `agent_console/wiki.py`: reuse existing append/split/commit primitives, without weakening single-source validators.
- Tests: `tests/test_wiki_topic_context.py`, `test_wiki_topic_integration.py`, `test_wiki_topic_transaction.py`, `test_wiki_topic_acceptance.py`, and existing rollout suites.

### Task 1: Verified Read-Only Public Context

**Interfaces:** Define `TopicContext` TypedDict with `notes`, `dependencies`, `omitted`. Each note contains page ID, revision ID, title, compiled claims, terms, limitations and canonical URL. Dependencies retain page fingerprint and raw/text/compiled hashes. Produce `select_topic_context(compiled: dict, *, journal: Journal, records: list[dict], now: datetime, surface: str) -> TopicContext` and `revalidate_topic_context(context: TopicContext, *, journal: Journal, records: list[dict], now: datetime) -> None`.

- [ ] Write failing context tests: four-note cap, 12,000/24,000 limits, deterministic tie order, revision deduplication, no sliced claim text, explicit omitted reasons, current revision excluded.
- [ ] Add rejection tests for private/changed/archived pages, missing/expired raw, corrupted compilation, nested private metadata and compressed originals. Capture model inputs and assert forbidden markers never appear.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_topic_context.py`; require intended failures before implementation.
- [ ] Implement using existing public fingerprint gate and archive-aware readers. Sort non-generic term-overlap descending then page ID; admit whole projections only. Context notes are not targets.
- [ ] Re-run new tests and `tests/test_public_wiki_rollout.py tests/test_public_wiki_pilot.py`; require zero failures.
- [ ] Review/commit only this task's module and tests.

### Task 2: Multi-Source Proposal And Validation

**Interfaces:** Consume Task 1. Produce `propose_topic_integration(compiled: dict, source: dict, context: TopicContext, targets: list[dict], *, llm_fn) -> dict` and `validate_topic_integration(payload: dict, *, compiled: dict, source: dict, context: TopicContext, targets: list[dict]) -> dict`. Validated output contains target identity/before hash, additions, original per-revision claims, limitations, source map, relations and dependencies.

**Contract:** Assign citation labels server-side: current source S1; context revisions sorted lexically use S2 onward. Additions contain claim IDs, revision IDs, as_of, scope and body. Claims grouped in one addition must share as_of and scope; preserve differing claims in separate additions. All cited revisions must be declared contributors and all declared contributors must be cited. Never infer factual entailment from syntactic validation.

- [ ] Write failing tests for two-source concept creation with purpose topic_synthesis, later update of an existing topic, refusal for unrelated/homonym scopes, and read-only context ID rejected as an editable target.
- [ ] Add tests rejecting invented claim IDs, wrong dates/revisions, duplicate IDs with differing content, substituted URLs/labels, omitted contributing citations, fabricated relation endpoints and trust promotion.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_topic_integration.py`; record RED.
- [ ] Implement a distinct strict JSON contract; reuse citation parsing where compatible but keep the single-source validator unchanged. Resolve relations only to supplied claims with matching revision/date; unresolved is not resolved.
- [ ] Re-run new tests plus `tests/test_wiki_source_integration.py tests/test_wiki_source_compiler.py`; require PASS.
- [ ] Review/commit this module and its tests.

### Task 3: Atomic Topic Persistence And Recovery

**Interfaces:** Produce `prepare_topic_batch(proposal: dict, *, journal: Journal, records: list[dict], now: datetime) -> dict` and `recover_topic_operation(operation_id: str, *, journal: Journal, now: datetime) -> dict` in the transaction module. Batch carries protocol version 2, dependency fingerprints/hashes, target before/after fingerprints, retained per-revision claims and effect states. Existing version-1 receipts remain readable unchanged.

- [ ] Write failing tests for atomic two-source seed, third-source append, preserved prior claims/counterclaims, unchanged source-note and original hashes, and no duplicate claims on retry.
- [ ] Inject crashes before commit, after data commit and during index/search/log effects. Assert exactly one data append and recoverable effects. Changing/removing a dependency before commit must reject without partial target writes.
- [ ] Include split-child mutation, simultaneous overlapping target transactions and legacy receipt compatibility tests. Acquire dependency/target locks in one global sorted order and re-read records after locking.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_topic_transaction.py`; verify intended failures.
- [ ] Build a complete target change in memory, retaining original per-revision claim identities. Use existing shared-store atomic commit/split machinery under its lock; never fake a combined single-source revision or call sequential commits as an atomic substitute.
- [ ] Extend journal receipt validation explicitly for version 2. Keep data commit distinct from recoverable effects; ingest logs list all contributors without source bodies.
- [ ] Run new tests and `tests/test_wiki_source_atomic_integration.py tests/test_wiki_compiled_split_roundtrip.py tests/test_wiki_resumable_schema.py`; require PASS.
- [ ] Review/commit transaction, scoped journal/wiki changes and tests.

### Task 4: Guarded Pipeline Wiring

**Interfaces:** The existing `process(source, events, journal, attempt)` remains the reservation boundary. Add `AGENT_CONSOLE_PUBLIC_TOPIC_BOOTSTRAP_ENABLED`, default false, parsed using existing boolean conventions. When false, preserve old flow. When true, select/revalidate Task 1 context and use Tasks 2/3 only for topic operations; source-note creation remains unchanged.

- [ ] Write failing tests proving default-off compatibility, only public supplied dependencies in model inputs, rejection of unreserved/current-source mismatch, no extra source attempts, and restored monkeypatches/environment after errors.
- [ ] Verify new routing/proposal prompt envelopes in the rollout wrapper before model calls; validate every supplied context/target fingerprint, not only a claimed parent revision.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_public_wiki_rollout.py tests/test_wiki_topic_acceptance.py`; record RED for new contracts.
- [ ] Wire the opt-in branch without resetting completed receipts or activating timers. Historical backfill remains an explicit reserved operation.
- [ ] Re-run quota, public pilot, rollout and pipeline tests; require zero failures and unchanged five/twenty limits.
- [ ] Review/commit wiring and tests. Document flag and rollback in the maintainer contract.

### Task 5: End-To-End Accumulation Evidence

- [ ] Add synthetic acceptance tests: two source notes become one draft topic containing both revisions; a third updates the same topic with dated conflicting evidence; originals/notes remain identical; retry is a no-op.
- [ ] Assert Markdown retains per-source IDs/URLs/limitations, long-topic children are lossless, widget navigation reaches topic/children, and chat stays within 1,600 characters with exposed citations only.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_topic*.py tests/test_wiki_compiled_split_roundtrip.py tests/test_wiki_browser_stored_knowledge.py tests/test_wiki_chat_provenance.py`.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki*.py tests/test_agent_console.py tests/test_llm_wiki_acceptance.py tests/test_public_wiki*.py`, then the full `.venv/bin/python -m pytest -q --tb=short`. Report permission-dependent listener failures separately; do not skip or weaken them to manufacture green.
- [ ] Run `git diff --check` and independent whole-change review using requested gpt-6-luna or gpt-6.1-sol. Fix concrete findings through RED/GREEN tests.
- [ ] Update completion evidence without claiming deployment or semantic correctness from counters alone.

## Operational Pilot And Rollback

No operational activation is part of plan approval. After code review/tests,
request separate approval for one reserved public pair and one later follow-up.
Honor the existing remaining daily budget; wait for the actual KST boundary when
exhausted. Inspect resulting claims and qualifications against retained originals.
Record model failures, omissions and refusal as outcomes, not hidden retries.

Rollback: disable the new flag and leave the existing public source runner
unchanged. Keep committed topic data and receipts as draft audit history; do not
delete originals or unconditionally restore the dirty shared store. Revert only
the scoped feature commits if needed, preserving version-2 receipt readability.

## Handoff

Implementation has not started. The user already requested gpt-6-luna /
gpt-6.1-sol subagents; preserve that execution preference. Ask for plan review
before implementation, then run task-by-task implementation and independent
reviews. Tasks 1 and 2 share a typed interface; Task 3 depends on Task 2; Task 4
depends on all three; do not parallelize overlapping journal/pipeline edits.
