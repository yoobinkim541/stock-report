# Immutable Public Source Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore source-grounded topic accumulation through immutable public source notes without rewriting editorial history or renewing pilot authorization.

**Architecture:** Add a content-addressed derivative layer under the existing journal, with explicit snapshot context and separate v3 proposals, receipts and witnesses. New use revalidates current evidence policy; historical recovery uses frozen witnessed inputs and never reconstructs authority from mutable receipts. Keep legacy v1/v2 behavior and default-off publication intact.

**Tech Stack:** Python, pytest, existing finite sorted JSON/hash contracts, `safe_io`, journal/source locks, canonical shared-memory CAS, Streamlit citation UI and generation-bound publication/QMD.

**Spec:** `docs/superpowers/specs/2026-10-20-immutable-public-source-notes-design.md`

**Execution Approval:** User approved this reviewed plan and Luna/Sol execution
in call_b194b279b36a4a628f35d65bdd4ce042. Gate A is satisfied for synthetic
implementation only; Gate C operational actions remain unapproved.

## Global Constraints

- "Raw HTML/PDF/text/manifests, existing source receipts, existing compilations, existing wiki pages and all v1/v2 topic witnesses remain byte-for-byte intact during snapshot creation."
- "Only public, retained, unexpired sources with verified revision identity, archive hashes and successful compilation qualify."
- "Login articles and personal conversations are excluded."
- "The existing chatbot 1,600-character excerpt limit, global public quotas, source retention rules and standing activation defaults do not change."
- "No retention/pruning worker may delete these new artifacts in this rollout."
- "Retain the outer operation lock before sorted source locks, then target/child locks, then the store lock."
- "Snapshot dependencies are artifact guards, not entries in wiki `expected_before`; mutable target and split-child fingerprints remain CAS guards."
- "V1/v2 serialization, witnesses, retries and negative guards remain unchanged; unknown versions fail closed."
- "Successful commits report `awaiting_semantic_review`, never factual completion."
- "Written-spec approval permits a TDD implementation plan, not implementation or production activation."
- This turn owns only this plan. Do not edit `agent_console/wiki.py` or the concurrent bounded tag-inheritance test. Future architecture work must integrate the reviewed fix, not replace it.
- Preserve the user's Luna/Sol subagent method: review this plan with both before implementation; implement approved tasks with fresh task/review contexts and whole-branch review. Do not substitute native execution without approval.
- Planning reads code/spec only. Future tests use synthetic temporary roots, stub providers/indexers and no production source reads, network refetch, live writes, activation or model calls.
- Each commit step is conditional on user-authorized Git integration and writable Git metadata; retain reviewed changes without staging when those conditions are absent. Do not request or assume deployment permission from this plan.

## Review Focus

- A symlink swapped between validation and open must not expose outside-root bytes: Task 1 tests descriptor-safe reads, not merely `resolve()` checks.
- A valid current source cannot authorize private additions to a target or forged outgoing prompt: Tasks 2, 3 and 5 test both projections independently.
- Two writers discovering different split children must retain lock order and detect stale CAS: Task 4 tests barrier-controlled races.
- A witnessed commit whose mutable receipt vanishes must remain historical without fabricated publication proof: Task 6 tests frozen recovery and missing-proof pending effects.
- Missing/relocated pilot state with zero daily usage must never mint two new tries: Task 8 tests external authorization/history guards.

---

## File Map And Review Gates

New modules separate artifact storage, context, v3 proposals, transactions, viewer and pilot authorization. Existing journal, worker, eligibility, publication and dashboard consumers gain explicit v3 branches; exact files/interfaces follow. No new dependencies, global wiki root, alias pages or global latest-note selector.

This is one end-to-end contract: artifact-only work is not permission to activate incomplete consumers. Gate A: user reviews spec/plan and Luna/Sol resolve contract objections before any architecture implementation. Gate B: reviewed synthetic implementation remains disabled. Gate C: separate explicit operational authorization covers materialization/migration and the exact three-source/two-attempt pilot. Existing generation blockers require their own decisions.

### Task 1: Immutable Artifact Core

**Files:** Create `reports/wiki_source_notes.py`; test `tests/test_wiki_source_notes.py`. Read/reuse `reports/wiki_source_revision.py`, `reports/wiki_source_compiler.py`, `reports/wiki_source_chunks.py`, `reports/wiki_source_journal.py` and `safe_io.py` contracts without changing legacy formats.

**Interfaces:** Produce `materialize_source_note(revision_id: str, *, journal: Journal, now: datetime, apply: bool = False) -> dict`, `pin_source_note(note_id: str, *, journal: Journal, now: datetime) -> dict`, and `revalidate_source_note(pin: dict, *, journal: Journal, now: datetime) -> None`. Pin is local-only: `note_id`, `revision_id`, `projection_version`, `manifest_sha256`, raw/text/compile hashes, parsed `note` and exact verified output bytes; local paths never enter public projections. Dry-run returns computed identity/validation results without creating files.

- [ ] **RED:** Add `test_identity_is_id_free_and_deterministic`: independently hash canonical identity `{identity_version: 1, source_protocol_version, revision_id, projection_version: 1, canonical_url, raw_sha256, text_sha256, compile_sha256, note_json_sha256, note_md_sha256}`; assert equality to `note_id`, no ID/final path/manifest hash in either hashed output, and date/title/alias/claim/limitation retention. Assert operational timestamp changes do not change ID; output/compiler/version changes do.
- [ ] **RED:** Add parameterized tests rejecting private/login/expired/unknown-policy sources, mismatched revision/archive/compiler/anchor, duplicate or broken competing claims, corrupt existing output, escape paths, symlink substitution (including open-time race). Add resumed-chunk tail claim retention, no-model/no-reservation spies, dry-run no-write, and before/after byte inventory assertions for all existing source/editorial/v1/v2 files.
- [ ] Run `pytest tests/test_wiki_source_notes.py -q`; expect failures for missing new interfaces, not fixture/import errors.
- [ ] **GREEN:** Implement deterministic ID-free `note.json` and `note.md` plus `manifest.json` under `journal.root/source-notes/<note_id>/`. Use compiler-retained title only; unknown dates remain unknown. Validate normal compilations and existing resumed aggregate/chunk anchor semantics without invoking compilation. Store the complete validated compilation projection without truncating; existing context limits apply later, not to stored claim count.
- [ ] **GREEN:** Use sorted source locks, same-filesystem staging, trusted-root non-symlink regular-file reads, file/directory fsync and atomic rename. Verify complete existing artifacts before reuse; refuse corruption without overwrite. No artifact/store lock inversion, archive mutation, pruning or implicit refetch.
- [ ] Add/run crash tests before/after each file fsync, directory fsync and rename: incomplete staging is undiscoverable; post-rename retry fully verifies and reuses. Run `pytest tests/test_wiki_source_notes.py tests/test_wiki_source_chunks.py tests/test_wiki_source_validation.py -q`; expect PASS.
- [ ] Stage only task files; commit `add) 불변 공개 소스 노트와 검증 경계 추가`. Body records work, benefits and disk/validation trade-off; use this body convention for every task commit.

### Task 2: Snapshot Context Without Editorial Authority

**Files:** Create `reports/wiki_topic_snapshot_context.py`; test `tests/test_wiki_topic_snapshot_context.py`. Leave `reports/wiki_topic_context.py` page-backed APIs unchanged.

**Interfaces:** Consume Task 1 pins. Produce `select_snapshot_context(compiled: dict, *, note_ids: list[str], journal: Journal, now: datetime, surface: str) -> dict`, `snapshot_context_model_input(context: dict) -> dict`, `revalidate_snapshot_context(context: dict, *, journal: Journal, now: datetime) -> None`. Context retains `notes`, `dependencies`, `omitted`; dependencies have `kind: source_note_snapshot`, note/revision/projection identities, manifest/raw/text/compile hashes and surface, never page IDs/fingerprints. Explicit note IDs pin projection versions; no silent compatible-version substitution.

- [ ] **RED:** Add `test_editorial_changes_do_not_change_snapshot` covering title/body/private additions/archive/split/deletion: pinned source projection remains identical, private additions are absent. Assert raw/compile/note mutation, expiry and policy revocation fail revalidation independently.
- [ ] **RED:** Test deterministic ranking (existing financial term overlap, then note/revision identity), revision deduplication, exclusion of current revision, surface checks, four-note maximum, 12,000-character per-note and 24,000-character aggregate canonical JSON budgets. Oversized exceptions are omitted with reasons, not clipped. Test paths, dependencies, diagnostics and mutable editorial fields absent from model input.
- [ ] Run `pytest tests/test_wiki_topic_snapshot_context.py -q`; expect missing-API failures.
- [ ] **GREEN:** Implement bounded detached notes-only projection: compiler title, canonical URL/revision, terms/aliases, original dates, attribution, claims/dates/scopes/anchors/competitors/limitations. Revalidate immediately before exposure using pinned bytes plus current source policy. Notes have no fabricated `page_id`; source maps use revision/canonical URL.
- [ ] Run `pytest tests/test_wiki_topic_snapshot_context.py tests/test_wiki_topic_context.py tests/test_wiki_source_context_dedup.py -q`; expect PASS and unchanged legacy page guards.
- [ ] Review, stage task files and commit `add) 편집 문서와 분리된 스냅샷 문맥 추가`.

### Task 3: V3 Proposal And Final Provider Boundary

**Files:** Create `reports/wiki_topic_v3.py`; modify `scripts/run_public_wiki_rollout.py:validate_topic_prompt` and its checked-provider caller; test `tests/test_wiki_topic_v3.py`, `tests/test_wiki_topic_snapshot_prompt_boundary.py`.

**Interfaces:** Produce `propose_topic_integration_v3(compiled: dict, source: dict, context: dict, targets: list[dict], *, llm_fn, journal: Journal, records: list[dict], now: datetime) -> dict` and `validate_topic_integration_v3(payload: dict, *, compiled: dict, source: dict, context: dict, targets: list[dict]) -> dict`. Proposal has `protocol_version: 3`, current revision/role/observed evidence, pinned snapshot context, targets and refusal. Extend `validate_topic_prompt(data, *, journal, reserved_revision_id, now, context_mode="legacy_pages", pinned_context=None)`; legacy return contract stays unchanged, snapshot mode returns the validated current source as before.

- [ ] **RED:** Test genuine seed/follow-up proposals, preserved earlier synthesis, dates, attribution, competing/qualifying claims and refusal. Reject fabricated citations/contributors/roles/observed IDs, URL smuggling, duplicate/nonfinite JSON and cross-surface targets. Assert independently private/changed editable targets are rejected even with valid notes.
- [ ] **RED:** Test final parsed envelope tampering in every projection, missing/wrong caller pins, model-supplied dependencies, source expiry between selection and provider call, and a spy proving `_note` is never called in snapshot mode. Assert no manifest/local paths in the actual provider string and unchanged editable-target budgets/before hashes.
- [ ] Run `pytest tests/test_wiki_topic_v3.py tests/test_wiki_topic_snapshot_prompt_boundary.py -q`; expect missing-v3 failures.
- [ ] **GREEN:** Build v3 source maps/claims from verified note bytes plus verified current compilation, enforce existing addition/relation/citation constraints without page-shaped source identities. Keep editable targets separately receipt/witness-eligible. Validate both construction and exact outgoing envelope at final provider boundary; trusted pins travel through a local closure/keyword, never model JSON. Preserve reservation revision checks and no-search provider behavior.
- [ ] Run the new tests plus `tests/test_wiki_topic_prompt_boundary.py` and `tests/test_wiki_topic_integration.py`; expect PASS.
- [ ] Review, stage task files and commit `add) v3 주제 제안과 최종 프롬프트 검증 추가`.

### Task 4: V3 Journal, Frozen Batch And Atomic Witness

**Files:** Modify `reports/wiki_source_journal.py`; create `reports/wiki_topic_transaction_v3.py`; test `tests/test_wiki_topic_journal_v3.py`, `tests/test_wiki_topic_transaction_v3.py`. Reuse canonical store and existing lossless split APIs; no concurrent `wiki.py` fix edits.

**Interfaces:** Add `Journal.load_topic_operation_v3(operation_id: str) -> dict | None`, `prepare_topic_v3(batch: dict) -> dict`, `transition_topic_v3(operation_id: str, **kwargs) -> dict`, `pending_topic_operations_v3() -> list[dict]` using `topic-operations-v3/`, schema/protocol version 3. Produce `prepare_topic_batch_v3(proposal: dict, *, journal: Journal, records: list[dict], now: datetime) -> dict`, `recover_topic_operation_v3(operation_id: str, *, journal: Journal, now: datetime) -> dict`, `topic_commit_record_v3(batch: dict) -> dict`.

- [ ] **RED:** Test strict separate loaders/transitions and unknown versions before dependency access; reject cross-directory/version confusion and fabricated receipt/witness. Test a genuine artifact-backed seed/update, deterministic operation/batch identity, and exact snapshot identities bound alongside target before/after hashes.
- [ ] **RED:** Test role/evidence changes before unwitnessed commit invalidate preparation. Freeze source receipt inputs as `source_inputs` keyed by revision: evidence-by-role, observed IDs, attribution/reference projection and `input_sha256`. Freeze public-only publication inputs as `public_proof`; bind both into batch hash. Changing either must invalidate witness verification.
- [ ] Run both new test files; expect missing-v3 failures.
- [ ] **GREEN:** Under operation lock then sorted source locks, freeze inputs and rebuild additions/citations/counterclaims from verified evidence. Discover target/split-child identities before acquiring sorted target/child locks, then reread canonical rows under store lock; restart lock acquisition or fail stale if child closure changes. Never acquire source/artifact locks under store lock. Rebuild complete expected batch and atomically store upserts plus a distinct non-wiki v3 witness.
- [ ] **GREEN:** Keep snapshots out of wiki `expected_before`, upserts, deletes and navigation IDs. Retain mutable target/child CAS, prose/claim/limitation/competitor preservation and existing lossless splitting; editorial source navigation is optional and only points to existing pages.
- [ ] Add barrier-controlled simultaneous writers, changed split closure, stale target, expiry before commit, crash before/after atomic store and interrupted receipt transition tests. Matching witness proves history even if current receipts later change; stage alone never proves commit. Run new tests plus `tests/test_wiki_topic_transaction.py` and `tests/test_wiki_source_atomic_integration.py`; expect PASS.
- [ ] Review, stage task files and commit `add) v3 고정 배치와 원자적 커밋 증명 추가`.

### Task 5: Versioned Public Eligibility And Worker Dispatch

**Files:** Modify `scripts/run_public_wiki_pilot.py`, `reports/wiki_source_pipeline.py`, `scripts/run_public_wiki_rollout.py`; test `tests/test_wiki_topic_public_targets_v3.py`, `tests/test_wiki_topic_pipeline_v3.py`.

**Interfaces:** Extend public-target discovery/hydration/validation by explicit validated v3 receipt/witness branches; preserve existing signatures. Add `context_mode="legacy_pages", note_ids=None` keyword arguments to `run_public_topic`; snapshot mode is accepted only when `AGENT_CONSOLE_PUBLIC_SOURCE_NOTES_V3_ENABLED=1` (default `0`) and existing public-topic enablement/reservation checks pass. Consume Tasks 2-4 APIs; enumerate v2 and v3 pending receipts separately before dispatch.

- [ ] **RED:** Test v1/v2/v3 discovery, hydration, current public-topic receipts and version-specific witnesses; reject private editorial drift, stale after hashes, forged stages and unknown versions. A valid snapshot never authorizes an independently ineligible target.
- [ ] **RED:** Test retry/resume, role selection, operation enumeration and old v2 fields lacking v3 dependencies; ensure no v2 receipt enters v3 code and no committed historical operation becomes fresh work on expiry. Assert disabled flags produce no writes/provider calls, and no quota or activation policy changes.
- [ ] Run new test files; expect missing-v3 behavior failures.
- [ ] **GREEN:** Version every discovery/loader/retry/dispatch consumer, including rollout topic-stage resumption and public worker paths. New work revalidates current policy; witnessed recovery goes to frozen effects only. Use explicit note ID selection, existing shared reservation, current public target validation and Task 3 final-envelope pins. Do not enable standing automation.
- [ ] Run new tests plus `tests/test_wiki_topic_public_targets.py`, `tests/test_wiki_topic_pipeline.py`, `tests/test_public_wiki_rollout.py`, `tests/test_wiki_public_budget.py`; expect PASS.
- [ ] Review, stage task files and commit `add) 공개 대상과 재시도 경계의 v3 분기 추가`.

### Task 6: Frozen Publication, Search And Log Recovery

**Files:** Modify `reports/wiki_publication_effects.py`, `agent_console/wiki_publication.py`; test `tests/test_wiki_publication_v3.py`.

**Interfaces:** Extend `_receipt`, `_binding`, `_check_commit`, `dispatch_effects` and transition selection with validated explicit v3 branches. V3 `_binding(receipt, journal) -> str` derives only from frozen batch, `source_inputs`, `public_proof`, pinned note hashes and matching v3 store witness; it must not call `journal.load_source()`. Frozen proof includes public attribution/reference data and the committed export inputs required for generation reconstruction, all hashed before the atomic witness.

Extend `capture_snapshot(*, source_note_assets: dict[str, dict] | None = None) -> dict` with an optional hash-covered asset map only when supplied. Absent assets retain legacy snapshot bytes/fields. Each asset pins the ID-free note JSON/Markdown bytes and manifest identity verified by Task 1; a source-note link requires an included matching asset, not a later filesystem lookup. `render_candidate(snapshot) -> dict[str, bytes]` and `validate_candidate(files) -> dict` remain pure and accept legacy snapshots unchanged. V3 effect capture uses frozen assets from `public_proof`, never live note discovery; missing/mismatched assets block publication.

- [ ] **RED:** Commit then delete/change mutable source receipts, roles, routing and evidence; assert identical binding and recoverable frozen effects. Replace `load_source`, provider and reservation with raising spies during recovery. Missing/tampered frozen proof leaves historical commit present and effects pending/unavailable.
- [ ] **RED:** Test current target drift, expired originals, changed display pages, crashes at publication/search/log boundaries, actual indexed generation identity with stubbed index runner, and newer active generation. Assert no quota spend, source reselection, canonical overwrite, old-generation reactivation or v1/v2 sidecar rewrite.
- [ ] **RED:** Freeze a source-note asset in a publication snapshot, mutate or remove its live file, and assert candidate bytes remain deterministic. Change frozen asset bytes without updating the snapshot identity and expect rejection; remove a cited asset and expect publication failure. Assert legacy snapshot serialization is unchanged when the new argument is absent.
- [ ] Run `pytest tests/test_wiki_publication_v3.py -q`; expect unsupported-v3 failures.
- [ ] **GREEN:** Separate current new-use validation from witnessed historical repair. Reconstruct only frozen committed effects, check current target identity before replay side effects, and preserve generation predecessor/activation proofs and default-off behavior. Missing proof is not permission to consult a newer source receipt. Keep legacy artifact/search/log fallback explicitly versioned too, without mutable source lookups for v3.
- [ ] Run new tests plus `tests/test_wiki_publication_recovery.py`, `tests/test_wiki_publication_activation.py`, `tests/test_wiki_publication_final_integration.py`; expect PASS.
- [ ] Review, stage task files and commit `add) 고정된 v3 출판 증명과 효과 복구 추가`.

### Task 7: Artifact Citation Viewer And Export

**Files:** Create `reports/wiki_source_note_viewer.py`; modify `dashboard/wiki_browser.py`, `agent_console/wiki_publication.py`; test `tests/test_wiki_source_note_viewer.py`.

**Interfaces:** Produce `source_note_citation(note_id: str, *, journal: Journal, now: datetime) -> dict` with note/revision identity, canonical URL, compiler title, dates, claims, limitations and availability status; no filesystem paths. Use `?source_note=<64-hex-note-id>` for local viewer navigation and `source-notes/<note_id>.md` inside a frozen generation; never interpret query text as a filesystem path. Historical unavailable/expired evidence displays status, not renewed eligibility.

Within a pinned generation, use `GenerationReader.read("source-notes/" + validated_note_id + ".md")` and the corresponding hash-bound JSON metadata, never resolve the local-viewer query as an OS path. Task 6 includes the complete asset map before manifest generation; Task 7 renders only those bytes and tests emitted paths against that same pinned reader. A missing asset returns unavailable without falling through to an unrelated current generation or archive.

- [ ] **RED:** Test citation click/readback after display-page rename/delete, exact revision/URL attribution, unavailable/expired status, HTML/script escaping, traversal/symlink rejection and no private/path leakage. Assert no blank alias pages or artifact IDs in `wiki.links`, no invented legacy-link targets, and unchanged 1,600-character chatbot excerpts.
- [ ] Run `pytest tests/test_wiki_source_note_viewer.py -q`; expect missing-viewer failures.
- [ ] **GREEN:** Render trusted pinned note projections in existing citation UI and versioned frozen export assets from Task 6. Validate every emitted source-note link against exported assets; keep existing page links and legacy unresolved-link diagnostics unchanged. Citation availability never grants topic-context authority.
- [ ] Run new tests plus `tests/test_wiki_browser_source_reference_identity.py`, `tests/test_wiki_publication_legacy_links.py`, `tests/test_wiki_chat_provenance.py`; expect PASS. Inspect synthetic browser/citation rendering only.
- [ ] Review, stage task files and commit `add) 불변 소스 노트 인용 뷰어 추가`.

### Task 8: Same-Path Pilot Migration And Operational Gate

**Files:** Create `reports/wiki_topic_pilot_state.py`; modify `scripts/run_public_topic_pilot.py`; test `tests/test_public_topic_pilot_v3.py`. No operational migration or model execution in this task.

**Interfaces:** Produce `inspect_pilot_authorization(*, journal: Journal) -> dict`, `migrate_empty_pilot_state(*, journal: Journal, expected_sha256: str, authorization_id: str, apply: bool = False) -> dict`. Retain canonical `journal.root/topic-pilot.json`; migrate schema 1 to schema 2 with `context_mode: source_note_snapshot` and explicit pinned note IDs. Store exact prior bytes and migration intent under `topic-pilot-migrations/<authorization_id>/`; persist one-time authorization/consumption guard at `topic-pilot-authorizations/<authorization_id>.json`, independent of replaceable state. Authorization ID binds original approved revision tuple and retained operator approval record, never current date or state filename.

The operator approval payload is local strict JSON, created only by an explicit separately approved initialization/reconciliation action: `{schema_version: 1, approval_ref, revision_ids, max_attempts: 2, daily_limit: 20, decision: "approved"}`. `approval_ref` is the retained original operator approval identity, not model text or a newly generated random ID; duplicate delivery of that same approval derives the same `authorization_id = SHA256(canonical approval payload)`. The external guard contains exactly `{schema_version: 1, authorization_id, approval, state_path, state_sha256, history_proofs, lifecycle, attempts}`: immutable nested `approval`, canonical old state path, exact state-byte hash, history proof hashes, durable attempt identities and lifecycle (`initialized`, `migrating`, `active`, `needs_review`, `exhausted`). Operational guard fields do not enter the authorization ID. Reject extra approval/guard fields and changed revision order/limits/approval identity rather than mint a new allowance.

Schema-2 state has exactly `{schema_version: 2, authorization_id, context_mode, revision_ids, note_ids, baseline, attempts, topic_ids}`. `note_ids` maps each approved revision to one validated immutable note; attempts retain stage, revision, durable intent ID, reservation, status and optional operation/version from the original protocol. Maximum two entries, seed before follow-up, one draft target after committed seed, no second attempt after refused/failed/interrupted first. Validate reservation membership and committed witness/operation binding by actual version before accepting state.

All inspection/migration/execution changes take the existing `automatic-run` lock first, then the authorization/state file locks in a fixed documented order; atomic files and migration intent preserve pre/post state hashes. Reconcile state intents, external guard, v2/v3 operation witnesses and shared reservations one-to-one by persisted IDs. Never equate number of receipts with number of attempts or count the same reservation twice. Missing or conflicting IDs, unaccounted same-scope reservations, relocated roots, state/guard loss or unreadable history return `needs_review`; no automatic zero inference. The separately approved operator history decision may establish a genuinely untouched initialization, but cannot silently reset spent attempts.

- [ ] **RED:** Test CAS migration requires empty attempts, empty topic IDs and no baseline; any existing attempt halts for review. Test exact old-byte retention, crashes before/after record/state rename, idempotent reconciliation and rejection of changed authorization. Missing/corrupt/unreadable/relocated/ambiguous state plus zero daily usage still halts after checking canonical old path, retained records, reservations and operation/witness history. Deleting state/changing version cannot renew allowance.
- [ ] **RED:** With synthetic sources/provider only, test two-source draft then third-source update to the same verified topic. Failed/interrupted/refused attempts consume the original total two tries across modes; actual-time shared daily 20/KST quota applies with at most two reservations. Test no date override in real CLI, no new filenames as allowance reset, unchanged source hashes and default-off publication. A genuinely first initialization requires an explicit separately recorded operational decision.
- [ ] Run `pytest tests/test_public_topic_pilot_v3.py -q`; expect missing migration/guard failures.
- [ ] **GREEN:** Implement dry-run/apply migration and explicit snapshot pilot mode using Tasks 1-7. Reuse `APPROVED_REVISIONS` exactly from the existing pilot; no expansion to the 105 targets. Preserve durable attempt intent and reservation reconciliation. Keep snapshot materialization, migration and model execution as explicit separate actions; no missing-file initialization side effect.
- [ ] Run new tests plus `tests/test_public_wiki_pilot.py`, `tests/test_wiki_topic_acceptance.py`, `tests/test_wiki_public_budget.py`; expect PASS. Review, stage task files and commit `add) 기존 승인 한도를 보존하는 파일럿 이전 추가`.
- [ ] **Separate operational approval, not implementation completion:** Obtain authorization before reading/materializing the exact three real originals or changing pilot state. Record dry-run/apply before/after original/archive/compile/editorial hashes; no model/reservation for materialization. Any existing attempt or uncertain consumption stops for review. Only separately authorized execution may use the two shared attempts, then report `awaiting_semantic_review`.

### Task 9: End-To-End And Legacy Acceptance

**Files:** Create `tests/test_wiki_source_notes_end_to_end.py`. No live acceptance writes or new architecture code in this task.

**Interfaces:** Consume all named APIs with synthetic temporary archive/journal/store/publication roots; exercise the actual provider-envelope validator, witness loader, public-target discovery, generation index identity and citation viewer.

- [ ] **RED:** Add `test_materialize_seed_update_recover_and_cite`: create real synthetic artifacts, seed from two sources, update same topic with the third, force split and interrupted commit/effects, verify retained prior prose/dates/limitations/competitors and witness-backed replay. Assert original/editorial preservation during materialization and unchanged quota on recovery. Add version-confusion/private-target/expiry-between-selection-provider-commit variants.
- [ ] Run `pytest tests/test_wiki_source_notes_end_to_end.py -q`; expect uncovered integration assertions to fail before repairs; never manufacture a failure if all approved contracts already pass.
- [ ] **GREEN:** Repair only failing v3 integration boundaries in their owning task files; rerun end-to-end tests. Require Luna/Sol review of privacy, role freezing, lock ordering, full version dispatch, frozen publication and pilot allowance; do not bypass unresolved findings by enabling flags.
- [ ] Run `pytest tests/test_wiki_*.py tests/test_public_wiki_*.py tests/test_public_topic_pilot_v3.py tests/test_karpathy_llm_wiki.py tests/test_llm_wiki_acceptance.py -q` with isolated test configuration; expect PASS, no real provider/network/index/activation calls. Distinguish existing unrelated failures from regressions and report both. Inspect the final diff for v1/v2 byte-contract changes and concurrent tag-fix ownership.
- [ ] Run the repository command `.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/wiki-source-notes-full.xml` on frozen code. Wait for its actual terminal result; record every failure and individually explain skips. Focused green is not a substitute for this full-suite gate. Keep actual-QMD opt-in verification separate and explicit if included.
- [ ] Stage reviewed task files and commit `add) 불변 소스 노트 전체 경로 회귀 검증 추가`.
- [ ] Hand off separate operational acceptance: compare actual pilot output against all three originals without model-based certification; verify attribution, dates, counterevidence and retained earlier synthesis. Current collection checks and unresolved generation blockers remain independent gates. Flag rollback stops new v3 work without deleting artifacts/witnesses or denying historical commits.

## Contract Choices For Review

Proposed here, requiring Gate A review: projection/identity version 1 and exact identity keys (Task 1); separate v3 modules/directory and explicit enable flag (Tasks 3-5); frozen `source_inputs`/`public_proof` fields (Tasks 4/6); local viewer query/export paths (Task 7); pilot schema 2 and external authorization-record paths (Task 8). These are internal choices, not authorization to implement.

Unresolved operational input: the retained original approval record must identify the one-time `authorization_id`. If it cannot be established unambiguously, migration/first initialization must halt for explicit recovery review. Existing attempt consumption is intentionally not inspected in this planning-only turn. No other contract is intentionally delegated to an implementer's guess.
