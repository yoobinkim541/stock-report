# Wiki Generation Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish complete wiki versions from one snapshot, with pinned reads and independently verified search activation/recovery.

**Architecture:** A focused generation module owns immutable files, manifests and activation. Existing renderers consume one detached canonical snapshot; a generation-aware QMD adapter and sidecar effect journal preserve external-index and legacy-receipt boundaries.

**Tech Stack:** Existing Python, pytest, pathlib, hashlib, JSON, safe_io/flock and installed QMD; no new database or dependencies.

**Spec:** `docs/superpowers/specs/2026-10-16-wiki-generation-publication-design.md` (user approved).

**Execution:** Tasks 1-6 software implementation and independent reviews are
complete. Final full run: 4,612 passed, two skipped, 41 warnings, exit 0.
Conditional commit/production rollout steps remain open; do not replay completed
software tasks from the original checklist. See the execution ledger and
`docs/audits/2026-10-16-wiki-generation-acceptance.md` for authoritative evidence.

## Global Constraints

- Preserve all existing knowledge, child pages, stable page IDs, original references, append-only history and the 1,600-character chat limit.
- Never fall back to copy-and-delete activation across filesystems.
- Automatic generation pruning is excluded from this change.
- Production activation is a separate step after tests and review, with old artifacts retained.
- No private article model processing is authorized here.
- Preserve dirty work; git metadata is currently read-only. Commit only owned changes when permitted; never reset unrelated files.
- Requested execution method: Luna/Sol subagent workers and independent review. Plan approval is required before implementation.

## Review Focus

- A pointer switches between two file reads: the reader must retain its pinned version (Task 3).
- Existing orphan/broken links: report a blocked candidate without deleting knowledge or silently dropping pages (Task 2).
- QMD reports success but queries the wrong index: smoke-query identity must reject activation (Tasks 1, 4).
- Artifact activation succeeds and history append fails: replay repairs history without another model call (Task 5).
- Old legacy receipts are loaded after migration: no schema reinterpretation or fabricated generation proof (Task 5).

## File Map

- Create `agent_console/wiki_publication.py`: snapshot, manifest, staging, pinning, activation and rollback.
- Create `agent_console/qmd_generation.py`: isolated QMD capability/index descriptor and query adapter.
- Modify `agent_console/wiki.py`: pure rendering seam and feature-gated publication/search wiring.
- Modify `agent_console/qmd_search.py`: retain legacy APIs; expose generation-aware health through adapter.
- Create `reports/wiki_publication_effects.py`: operation-to-generation sidecar and idempotent repair.
- Modify `reports/wiki_source_pipeline.py`, `reports/wiki_topic_transaction.py`: shared publication effects.
- Modify `config/wiki-maintainer.md`: version semantics, compatibility, recovery and rollback.
- Add focused tests named below; retain existing Markdown, citation and source-integrity tests.

## Task 1: Verify QMD Isolation Without Touching Production

**Files:** create `agent_console/qmd_generation.py`, `scripts/probe_qmd_generation.py`, `tests/test_qmd_generation_capabilities.py`; write evidence to `docs/audits/2026-10-16-qmd-generation-capabilities.md`.

**Interfaces:** `probe_isolation(*, executable: str, workspace: Path, runner) -> dict` returns exactly `supported: bool`, `reason: str`, `recipe: dict | None`. Recipe records verified argv/environment templates for isolated build/query; never guessed flags.

- [ ] Write `test_probe_never_uses_live_home_or_index`: injected runner asserts every invoked process uses temporary HOME/config/cache and only synthetic A/B documents.
- [ ] Write `test_global_only_or_wrong_index_is_unsupported`: unsupported CLI or B query returning A's sentinel yields `supported=False, recipe=None`.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_qmd_generation_capabilities.py`; observe missing-interface RED.
- [ ] Inspect installed executable/version/help read-only; implement the probe using documented local behavior. No downloads, embeddings/models or live collection updates. Bound subprocess timeouts to 30 seconds; deterministic failure codes only.
- [ ] Run tests GREEN, then explicitly run the probe in `/tmp` with synthetic notes. Save sanitized capability evidence, not environment secrets. If isolation is unavailable, filesystem work may proceed but real search activation remains unimplemented/pending and cannot pass final rollout.
- [ ] Review; when git writes are permitted commit owned files with title `add) 위키 검색 색인 격리 기능 검증` and Korean work/result/trade-off body.

## Task 2: Freeze And Validate One Complete Candidate

**Files:** create `agent_console/wiki_publication.py`, `tests/test_wiki_publication_snapshot.py`; modify renderer seam in `agent_console/wiki.py`.

**Interfaces:** `capture_snapshot() -> dict` returns detached `records`, `snapshot_sha256`, `schema_text`, `schema_sha256`. `render_candidate(snapshot: dict) -> dict[str, bytes]` returns `notes/<stable-id>.md`, `index.md`, `open-questions.md`, `lint.md`, `schema.md`, `snapshot.json`. `validate_candidate(files: dict[str, bytes]) -> dict` returns `ok: bool, errors: list[dict]`; no writes or log side effects.

- [ ] Write `test_writer_after_capture_cannot_change_rendered_index_or_note`: change canonical body after capture; both rendered note and index retain the captured body.
- [ ] Write tests for Unicode/colliding IDs, duplicate rows, missing link/child targets, archived targets, lossless child tails and preserved raw references. Candidate validation reports exact page IDs without deleting or filtering the source snapshot; broken targets prevent activation.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_publication_snapshot.py`; observe RED.
- [ ] Implement capture under existing `_events_lock` using checked reads; release before rendering. Render through existing page/Markdown helpers; extract a pure lint collector and keep existing `lint_pages` logging behavior for its callers. Hash every renderer input, including schema and any displayed usage field. No dynamic per-file timestamps: one candidate timestamp from capture.
- [ ] Run new tests plus `tests/test_wiki_markdown_integrity.py tests/test_wiki_compiled_split_roundtrip.py tests/test_wiki_maintenance.py` GREEN.
- [ ] Review and conditional scoped commit `add) 위키 게시용 단일 스냅샷 생성`.

## Task 3: Durable Activation, Pinned Readers And Rollback

**Files:** extend `agent_console/wiki_publication.py`; create `tests/test_wiki_publication_activation.py`.

**Interfaces:** `publish_snapshot(snapshot: dict, *, root: Path, operation_id: str) -> dict` returns `generation_id, snapshot_sha256, manifest_sha256`; `pin_generation(*, root: Path, generation_id: str | None = None) -> GenerationReader`; `GenerationReader.read(relative_path: str) -> bytes`; `activate_generation(generation_id: str, *, root: Path, operation_id: str) -> dict`. Reader binds one validated immutable directory.

- [ ] Write parameterized crash tests at file-write/fsync/directory-fsync/rename/pointer-replace boundaries: old pointer remains usable or new complete generation is active; never a partial version.
- [ ] Write `test_pinned_reader_survives_switch`, `test_rollback_keeps_history_and_originals`, and corruption/symlink/path traversal/cross-device failure tests. Snapshot/hash/schema identity must match manifest; unexpected files are rejected.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_publication_activation.py`; observe RED.
- [ ] Implement `.staging/`, immutable `generations/<sha256>/`, manifest and atomically replaced `current.json`, all within root. Flush files and directories. Validate existing generation bytes before reuse; no overwrite of a conflicting immutable directory. Lock activation with the existing publication lock order; do not acquire a store lock while holding it.
- [ ] Keep legacy directories untouched. Add stable `current` alias for operator navigation, documenting that separate external opens are not pinned reads; internal readers use `current.json` once. Rollback validates a retained manifest and uses the same activation mechanism, never deletions.
- [ ] Run Tasks 2–3 tests GREEN; review and conditional commit `add) 위키 버전 일괄 게시와 고정 읽기`.

## Task 4: Generation-Aware Search And Health

**Files:** extend `agent_console/qmd_generation.py`; modify `agent_console/qmd_search.py`, `agent_console/wiki.py`; create `tests/test_qmd_generation_search.py`.

**Interfaces:** `build_index(publication: dict, *, root: Path, capability: dict, runner) -> dict` returns `status: ready|search_pending`, `generation_id`, `snapshot_sha256`, `descriptor: dict | None`. `search_generation(query: str, *, descriptor: dict, limit: int, runner) -> list[dict]` emits page ID, generation ID and source semantic revision. `generation_health(*, root: Path) -> dict` separates published and indexed identities.

- [ ] Write wrong-index sentinel, timeout, index corruption, pointer switch during query and stale semantic revision tests. Failed build must retain previous search descriptor; no live global update or legacy-file pruning.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_qmd_generation_search.py`; observe RED.
- [ ] Implement only Task 1's verified isolated recipe. Smoke-query the generation sentinel and note IDs before atomic descriptor activation. With unsupported capabilities return `search_pending`; never `ready`. Pin descriptor once per search. Reject unknown or revised canonical hits and use existing canonical fallback; do not return indexed stale body as current content.
- [ ] Wire additive generation identity/health metadata without changing `list_pages` page dictionaries or dashboard child navigation contracts. Leave legacy API behavior unchanged when feature disabled.
- [ ] Run new tests plus `tests/test_qmd_search.py tests/test_wiki_knowledge_retrieval.py tests/test_wiki_chat_provenance.py` GREEN; review and conditional commit `add) 위키 버전별 검색 색인과 상태 검증`.

## Task 5: Source/Topic Recovery And Append-Only History

**Files:** create `reports/wiki_publication_effects.py`, `tests/test_wiki_publication_recovery.py`; modify source/topic effect dispatch only, not existing receipt schemas.

**Interfaces:** `repair_publication(operation_id: str, *, journal, now=None) -> dict` returns `generation_id, artifacts: done|failed, search: done|failed, log: done|failed`. Sidecar path `publication-effects/<operation-id>.json` binds snapshot, publication manifest and search descriptor hashes; existing v1/v2 receipts remain authoritative for source commits.

- [ ] Write artifact-activated/history-failed replay, search-pending replay, retained older generation, legacy receipt, forged sidecar and original commit-witness tests. Inject model callback that fails if invoked during repair; quota file must remain byte-identical.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_publication_recovery.py`; observe RED.
- [ ] Persist detached snapshot/sidecar intent before activation; use the same generation for artifacts and search. Validate operation/source commit before accepting sidecar. A stale operation generation may complete history without switching the active pointer backwards. Append idempotent publication/rollback events outside generation directories. Mark legacy effects done only after corresponding verified sidecar state; migration never invents a generation for prior done effects.
- [ ] Introduce `AGENT_CONSOLE_WIKI_GENERATION_PUBLICATION_ENABLED=0` default, document lock order and fallback. This is staged rollout only, not a claim the original goal is complete while disabled.
- [ ] Run new tests plus source/topic recovery, public budget and source integrity tests GREEN; review and conditional commit `add) 위키 게시 버전 기반 장애 복구`.

## Task 6: End-To-End Acceptance And Rollout Review

**Files:** create `tests/test_wiki_publication_acceptance.py`; update `config/wiki-maintainer.md`; create `docs/audits/2026-10-16-wiki-generation-acceptance.md`.

- [ ] RED acceptance: synthetic multi-source topic with long child tails -> publish -> pinned index/note reads -> isolated search -> canonical read -> rollback -> recover. Assert all expected claims, attribution, limitations, source hashes and 1,600-character excerpt behavior; switch a writer midway through rendering.
- [ ] Implement only integration gaps needed by this approved design; no source cleanup, private model exposure, global flag activation or raw retention changes.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_wiki_publication*.py tests/test_qmd_generation*.py tests/test_wiki_topic*.py tests/test_wiki_source*.py tests/test_agent_console.py tests/test_qmd_search.py`; all applicable tests must pass, skips justified individually.
- [ ] Run `.venv/bin/python -m pytest -q --tb=short` with local-listener permission if required, and `git diff --check`. Record actual exit/counts and known warnings; never reuse earlier full-suite counts.
- [ ] Fresh Sol review of whole change, including capability evidence and crash boundaries. Fix findings TDD, rerun impacted checks, then scope review again.
- [ ] Read-only real-store comparison with no model calls: validate candidate in temporary private storage and report existing broken links rather than silently repair/drop them. Seek separate production activation approval. After approved activation, verify live pinned reads, search descriptor, canonical fallback and rollback; unsupported QMD isolation blocks this gate.
- [ ] Record remaining model-pilot and long-term collection gates separately. Conditional scoped commit `add) 위키 버전 게시 통합 검증` with Korean work/result/trade-off body.

## Self-Review And Handoff

Snapshot/filesystem, pinned reads/compatibility, QMD isolation and history/recovery
map respectively to Tasks 2–3, 3–4, 1/4 and 5. Task 6 owns end-to-end and rollout.
The five Review Focus cases have explicit owning tests. Signatures above are the
cross-task contracts; no worker may replace them without updating dependents.
Implementation proceeded after user approval using Luna/Sol workers and fresh
reviews. Keep all uncommitted work intact; live activation remains separately
gated by the real-store link findings and user confirmation.
