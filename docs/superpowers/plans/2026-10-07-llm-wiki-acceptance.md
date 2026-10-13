# LLM Wiki Acceptance Plan

**Goal:** Verify source-derived, persistent knowledge, safe maintenance and readable child pages, rather than a link catalog.

**Reference:** https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
The reference describes a modular idea, not a formal certification. We test its
core three-layer ingest/query/lint/index/history pattern. Optional slides,
embeddings and image downloads are not prerequisites for this text-first service.

**Architecture:** Keep the existing raw archive, shared-memory wiki and markdown
export. Preserve user edits and immutable originals. Source text is untrusted
data; failures remain visible. Generated knowledge remains draft until reviewed.

## Tasks

- [x] TDD: valid source bodies enter distillation; links/excerpts alone do not.
- [x] TDD: complete structured output survives parsing; malformed output fails.
- [x] TDD: refresh preserves existing knowledge and records divergent drafts.
- [x] TDD: raw bytes remain unchanged through ingest and maintenance.
- [x] TDD: persistent pages support retrieval/citations and answer filing.
- [x] TDD: markdown index, schema and chronological history remain available.
- [x] TDD: long pages retain every section in linked child pages.
- [x] TDD: cleanup is previewable, reversible and protects evidence/history.
- [x] Read linked bodies with Luna; publish only successfully read drafts.
- [x] Run bounded live cleanup/backfill with pre-change snapshots.
- [ ] Verify tests and deployed health; record gaps before completion.

## Ownership

Luna workers own source collection, distillation, and wiki history/splitting
separately. Parent owns contract tests, schema, operational recovery and receipt.
No paid external models, access-control bypass or unchecked bulk deletion.

## Current Evidence

The focused contract/acceptance/maintenance/distillation suite passed 66 tests.
The maintenance/cleanup suite passed 25 tests. Four source-body-derived drafts
were stored with citations and links; Telegram raw hashes were unchanged.
These tests establish software behavior, not truth certification for the entire
wiki. A larger regression run and live maintenance receipt are still pending.

The original 66-test result above is historical. The latest verification and
remaining gaps are recorded below; it must not be read as current completion.

## Added Source Recovery Scope

The user added recovery of blocked/parsing-failed sources and selected Hermes
collection via webhook. SaveTicker's public server and desktop requests both
returned a Cloudflare challenge; login-protected content requires a user's
authorized browser. The new webhook design is in
`docs/superpowers/specs/2026-10-08-hermes-source-webhook-design.md` for review.
The goal is not complete until a real authorized body delivery is verified.

## Live Maintenance Receipt (2026-10-08)

Fifteen long parent pages were split into 32 linked child pages with exact body
reconstruction and preserved parent evidence/history. The pre-change snapshot
remains in the wiki backup directory. Cleanup preview found zero safely eligible
empty/duplicate pages, so no additional pages were archived or deleted.

Index/schema/log were rebuilt. Live lint still reports 10,441 signals: 8,431
missing-cross-reference warnings, 1,660 open-question notices and other findings.
This is not a claim that the full wiki is factually verified or clean. Four body-
derived drafts are demonstrations, not validation of all existing knowledge.

## Continued Verification (2026-10-08)

- TDD fixed provider-level truncation of valid long JSON, full community/series
  note eligibility, event/manifest body duplication and lost channel provenance.
- Actual public Telegram bodies were delivered through the signed receiver.
  Codex CLI read all 695 characters of message 65448 and generated the draft
  `distill-8cb778e9e5298a23bd46`, with a source URL, raw hash and confidence 0.5.
  The 2,363-character purchase table in message 65558 was intentionally skipped:
  the model did not invent an investment thesis from the ranking.
- Both outcomes were stored with source pages and meaningful existing-page
  links. Fresh reload verified citations, unchanged original hashes, no repeated
  selection of completed/unchanged input and append-only history.
- The Markdown mirror no longer clips bodies at 8,000 characters. It exports
  ID-based graph links and title aliases. All 3,978 pages were exported and QMD
  update succeeded; export success is not factual review of those pages.
- Automatic age cleanup now preserves accumulated knowledge, cited evidence,
  child pages and referenced originals. Staleness remains visible for review.
- Latest scoped regression: 627 passed, 1 skipped. The full project run found
  3,685 passed, 2 skipped and 1 failure in
  `tests/test_stock_advisor.py::test_ask_portfolio_advisor_uses_configured_codex_model_runner`.
  Its expected hardcoded model differs from the configured runtime model. No
  full-suite success is claimed; the full run preceded the final scoped fixes.
- Live lint has 7,482 signals, including 127 active orphans and 5,427 missing
  cross-references. Excluding archived pages fixed inflated relational counts;
  remaining findings have not been dismissed as harmless or automatically merged.

Evidence: `/tmp/wiki-live-persistence-receipt.json`,
`/tmp/wiki-live-model-65448-receipt.json` and the pre-change
`live-model-probes-*.json` snapshot in the live wiki's backup directory.

## Additional Verification (2026-10-08)

- TDD exposed and fixed the any-source validation hole: every attached article
  must qualify, provider-specific source checks cannot fall through to generic
  news checks, and unsafe URL schemes/embedded credentials cannot be cited.
  Valid Arca/Telegram info, breaking and premarket classifications retain the
  unverified community policy. URL-only evidence no longer raises KeyError.
- A real webhook-to-wiki regression exposed FRED notes mislabeled as numeric
  snapshots. Complete authorized notes now use `series_notes`; numeric snapshots
  remain excluded from article synthesis without poisoning a valid article.
- Latest targeted regression passed 166 tests. The project-wide run passed
  3,720 tests with 2 skips and 41 warnings. That run began before the final FRED
  and URL-only regressions were added; those later changes are covered by the
  targeted run, not claimed as a new post-change project-wide run.
- The advisor model test failure was reproduced under an explicit alternate
  environment value. The test now supplies a configured test model and asserts
  its CLI argument; the production model selection was not changed.
- Read-only live audit found 3,978 pages, 3,118 active, and 184 failed source
  digests (84 invalid JSON, 60 legacy kind-refresh failures, 40 unavailable body
  failures). Ten pages per failure reason were sampled; none had eligible full
  article input. This is not a representative whole-wiki quality estimate.
- Public M2 and high-yield OAS notes were delivered from the Hermes Python
  environment: 1,208 and 6,866 body characters, original notes DOM fragments,
  successful signed delivery and duplicate retries. Source classification is
  `series_notes`. These are definition notes, not historical observations.
- A proposed model probe with the M2 body and an existing internal draft was
  rejected by safety review before execution. Explicit approval was requested
  for that payload and external Codex destination. No new model result or wiki
  refresh is claimed for these two FRED deliveries.
- The private webhook receiver was restarted and health passed; dashboard
  loopback health passed. SaveTicker authentication is still unverified.

Evidence: `/tmp/wiki-current-source-audit.json`,
`/tmp/hermes-fred-M2SL-receipt.json`, `/tmp/hermes-fred-BAMLH0A0HYM2-receipt.json`.

## Source Identifier Verification (2026-10-08)

- TDD exposed clipping and local-path masking across shared memory, wiki
  persistence, evidence cards, curation and article context. Bounded references
  now retain full identity, including archived raw/text paths; unrelated personal
  paths and sensitive values remain masked. Oversized identifiers are rejected,
  not turned into different URLs. Fresh reload, splitting and opening a real
  archived body are covered by regressions.
- Full identifiers also prevent unrelated long-URL prefixes from becoming false
  source relationships in lint and curation. A real archived-snapshot regression
  verifies that excluded structured data cannot re-enter article input through
  a stored text reference.
- Latest scoped wiki/source regression: 657 passed, 1 skipped. A fresh full
  project run after these code changes passed 3,739 tests with 2 skips and
  41 warnings in 728.60 seconds. These results establish tested behavior, not
  authenticated source access or factual verification of historical knowledge.
- Read-only audit found 3,979 pages, 3,119 active and 8,852 stored active refs.
  There are 154 clipped-reference markers and 2,940 local-path markers across
  2,907 pages. These are suspect pointers, not a count of false claims. No
  historical pointer was guessed, repaired or deleted by this audit.

Evidence: `/tmp/wiki-reference-audit.json`.

## Historical Recovery Audit (2026-10-08)

- A subsequent read-only snapshot found 2,901 affected active pages. Comparing
  31,523 loose manifests found exact URL matches for 1,961 pages; 272 pages had
  multiple raw versions. URL equality alone is not a safe recovery rule. These
  counts differ from the earlier snapshot because live maintenance continued.
- The historical-event audit read 61 files with 481,648 rows and matched 1,664
  wanted evidence IDs. Only four inputs, linked to five affected pages, passed
  exact event/manifest identity, original SHA, extracted-body equality and full-
  body flag checks. Loose-manifest inventory is not a complete cold-bundle audit.
- A restoration preview also exposed incomplete unrelated inputs on the broad
  `source-type-analysis` page. Adding one valid body did not repair that whole
  aggregate; its failure was retained rather than bypassed.
- Three narrowly scoped records were restored: the previously model-tested
  Telegram messages 65448/65558 and the owned draft
  `distill-8cb778e9e5298a23bd46`. An atomic, lock-protected update changed only
  their `artifacts` field after an exact-record concurrency check and a 0600
  backup. Bodies, dates, confidence, other records and raw bytes were preserved.
- Before restoration, each of these three pages had zero readable archived
  articles without the event queue; after persistence/reload, each had one.
  Index/history were updated and all 3,979 Markdown pages exported successfully.
  No model was called. Older manifests lack evidence IDs, so archived inputs
  still carry the explicit `unmatched` evidence role; no supporting/conflicting
  role was guessed from a URL. FRED drafts were not exported or model-refreshed.
- A separate retention gap was confirmed in existing code/tests: loose raw TTL
  cleanup deletes manifests despite keeping text; expired bundles can delete
  both text and provenance. A bounded design to preserve derived text/provenance
  while retaining raw TTL was presented for approval, not implemented.
- Current scoped acceptance/reference/archive regression: 28 passed. These
  tests still encode the existing TTL behavior and do not prove durable
  provenance after expiry. The last full code regression remains 3,739 passed,
  2 skipped; no product code changed during this recovery audit.

Evidence: `/tmp/wiki-reference-recovery-audit.json`,
`/tmp/wiki-reference-repair-receipt.json`, and the referenced local pre-change
JSONL snapshot. These receipts do not establish whole-wiki factual quality.

## Dashboard Source Identity And Runtime Verification (2026-10-08)

- TDD exposed a second reference-clipping path in the dashboard adapter:
  120-character pointers and a 12-reference cap could discard identity despite
  the core retaining it. The adapter now uses the core's bounded, privacy-aware
  normalization for records, dictionaries and dataclasses. Oversized references
  are rejected rather than shown as source-backed; unrelated sensitive paths and
  credentials stay masked. Eight new regressions cover these contracts.
- The runtime freshness check omitted the imported `reports` tree. A behavioral
  shell test reproduced the failure with a real controlled worker process. The
  watchdog now monitors that tree too; a second test verifies that older files
  leave a healthy worker running. Its existing opt-in gate is unchanged.
- Focused dashboard/watchdog/mesh verification passed 56 tests. Actual-data,
  read-only Streamlit AppTest rendered both the console wiki tab and standalone
  wiki page with zero exceptions and zero model calls. This is not verification
  of browser canvas rendering or interactive graph selection.
- The receiver was restarted after its imported source changes. The dashboard
  restarted at 02:15:01 UTC, after the 02:14:54 adapter edit. Fresh loopback
  checks returned dashboard `ok` and receiver `configured: true, ok: true`.
  Tunnel settings and credentials were not changed. Health does not establish
  authenticated collection or complete source-to-wiki synthesis.
- A fresh full-project regression after these dashboard/watchdog changes
  passed 3,749 tests with 2 skips and 41 warnings in 749.72 seconds. This
  verifies tested contracts, not the factual accuracy of every wiki page.

Evidence: `/tmp/wiki-live-ui-receipt.json`, the focused test output and the
local service start-time/health checks. SaveTicker login remains unverified.

## Full Source-Digest Readiness Audit (2026-10-08)

- Two read-only audits used the current corpus and real local event/archive
  reader, then evaluated the production distillation gates. A sentinel stopped
  execution at the model boundary; no provider was called and no source state,
  wiki body or references were changed. The shared-memory JSONL hash was equal
  before and after both audits.
- All 182 currently failed active source digests were checked, not sampled.
  Stored reasons were 82 invalid JSON, 60 legacy kind-refresh failures and 40
  unavailable-body failures. Their current gates instead show 158 without usable
  body/citation and 24 with incomplete body; none was ready for a model call.
  This describes current availability, not the cause of each historical failure.
- All 1,631 active source digests were also checked: 1 pending, 1,339 created,
  109 skipped and 182 failed. Only three reached the model boundary: the new
  FRED series-notes page (two articles, 8,074 characters) and the previously
  verified Telegram 65448/65558 source pages. The other pages had 1,593 missing
  usable-body/citation inputs and 35 incomplete-body inputs.
- Of 1,339 previously created digests, 1,327 currently have no usable archived
  body/citation, 11 have incomplete body and one remains ready. A created record
  does not demonstrate current re-readability or factual accuracy. Conversely,
  current unavailability does not prove a historical model never read the body.
- The next recovery priority is authorized complete-body delivery and durable
  provenance, not replaying historical JSON failures with missing input. The
  pending FRED page was not compiled as an alternative way to bypass the open
  external-model payload approval. No protected-site login was attempted.
- Product code was unchanged in this audit; the latest project regression
  remains 3,749 passed, 2 skipped, 41 warnings. This corpus audit is not a
  semantic or factual review of all compiled knowledge pages.

Evidence: `/tmp/wiki-all-failed-source-audit.json` and
`/tmp/wiki-all-source-readiness-audit.json` (0600 local receipts). Stable snapshot
SHA-256: `7defd914422960b91959846308872c6d6c0fdcc21a767b889e626af79856346a`.

## Public Telegram Full-Body Recovery (2026-10-08)

- The ordinary public Telegram collector clipped message text twice at 3,000
  characters and archived it without completeness metadata. TDD reproduced
  truncation, archive re-readability and preview-upgrade failures in eight tests.
  Full extracted message text is now retained; only display/classification text
  is capped. Complete coverage requires a canonical message card with an exact
  same-channel message URL. Title-only and unbound-URL fallbacks remain partial.
- Coverage metadata is saved in the raw manifest as well as the event. An
  authorized native full-message revision can be appended over a previous preview
  without deleting it; identical full-body retries are deduplicated. Existing
  page-level HTTP size limits and wiki input budgets are unchanged. The archived
  HTML is a serialized message DOM fragment, not byte-identical response HTML.
- The eight new regression cases were observed failing before the fix. Focused
  Telegram/source/archive/webhook regressions then passed 117 tests. The fresh
  full-project regression passed 3,757 tests with 2 skips and 41 warnings in
  614.90 seconds. This validates tested behavior, not whole-wiki factual truth.
- An actual public message at `https://t.me/insidertracking/65745` supplied 3,517
  characters, exceeding the old display limit. Its complete text was re-read
  through the archive-only wiki input path; raw SHA-256 remained unchanged.
  Signed loopback delivery persisted one event and a retry returned duplicate.
  This probe made no model call and did not compile or modify a wiki page.
- The receiver restarted at 16:31:30 UTC after the collector edit; its health
  returned `configured: true, ok: true`, and dashboard health returned `ok`.
  No authentication session, credential or unattended Hermes job was created.

Evidence: `/tmp/wiki-telegram-fulltext-live-receipt.json` (0600), focused test
output and local service health/start-time checks. Webhook `wiki_status: pending`
means ingestion only, not semantic synthesis or factual review.

## Exact Public-Body Reference Recovery (2026-10-08)

- The current failed NVDA source digest had a 1,071-character Telegram body but
  no complete-body metadata. A normal public re-fetch of message 65664 found
  exactly one canonical same-channel message, whose complete extracted text
  exactly equaled the archived text. The new DOM is a newly retrieved public
  source, not a reconstructed historical HTTP response.
- An archive-only source-gate sentinel observed the two current source digests fail before
  reference replacement and reach the model boundary afterward, without calling
  any provider. The analysis digest retained the separately verified message
  65448 as its second complete input. These checks did not validate all historical
  evidence IDs, facts or source versions in either aggregate page.
- The first conditional write rejected a concurrently changed wiki record and
  made no wiki changes. The preview was recomputed from current state, then a
  locked, checksum-guarded write changed only `artifacts` in
  `source-ticker-nvda` and `source-type-analysis`. Legacy extracted-text pointers
  were replaced with the newly verified text; original DOM pointers and all
  unrelated references were retained. The old text pointers remain in the full
  private backup. No source file, body, status, decision, evidence ID, timestamp
  or unrelated record was altered by the repair.
- Post-write archive-only checks reloaded the persisted records: one complete
  input for NVDA and two for analysis. Original raw hashes were unchanged.
  Index/log artifacts were rebuilt and 3,980 markdown pages were exported for
  local search. Persisted failure state was not relabeled as a synthesized result.

Evidence: `/tmp/wiki-public-source-recovery-preview.json` and
`/tmp/wiki-public-source-recovery-receipt.json`. The full 0600 backup is
`~/.local/share/stock-report/shared-memory/llm-wiki/backups/public-source-recovery-20261008T164706.jsonl`.
The repair made no model call. Login-protected ingestion and semantic synthesis
remain separate incomplete gates.

## Readiness Snapshot And Lossless Organization (2026-10-08)

- A newly locked snapshot at 16:53 UTC contained 3,980 total pages and 1,633
  active source digests. Their stored states were 1,334 created, 109 skipped and
  190 failed. Production input gates reached the model boundary for only four;
  1,593 lacked a usable body/citation, 34 had incomplete bodies, and two exceeded
  the eight-article budget. No model was called. Snapshot capture and end-of-run
  store hashes matched in this run; these are snapshot counts, not guarantees
  that a continuously updated corpus remains unchanged later.
- The normal queue+archive reader still rejected the two newly repaired pages:
  a matching legacy incomplete event set the error before the complete archive
  reference could be used. Their earlier archive-only checks remain valid, but
  do not prove full-pipeline readiness. This is now a targeted integration
  regression, not grounds to relabel the source state as successful.
- A read-only organization check found no active top-level body above the
  12,000-character body limit, 25 source digests above the 2,600-character
  readability target and one knowledge draft above that target. The conservative
  empty/exact-duplicate cleanup planner found zero safe candidates; no records
  were deleted merely for being old or unreviewed.
- Existing split contracts passed 15 focused regressions. A current 3,116-character
  risk draft was previewed and then checksum-guarded into an overview and two
  children. Reloaded child bodies concatenate to the exact original; sources,
  evidence IDs and parent/child links were verified. Other records were unchanged.
  The full 0600 backup and original page revision were preserved, the log/index
  rebuilt and 3,982 markdown pages exported. This was mechanical organization,
  not semantic or factual review. Source digests were not split in this operation.
- The updated objective explicitly requests GPT-6.1 Sol and GPT-6 Luna. The
  parent dispatched those model overrides for independent ingestion/integration
  and query/file-back audits. Implementation and combined verification of the
  findings are tracked in `/tmp/wiki-integration-safety-ledger.md`; a worker's
  lack of tools to spawn further workers is not evidence that parent delegation
  failed.

Evidence: `/tmp/wiki-source-readiness-after-public-recovery.json`,
`/tmp/wiki-live-readability-split-receipt.json`, worker review reports and focused
test outputs. The normal-reader gap and full semantic review remain incomplete.

## Integration Safety And Public Event Recovery (2026-10-08)

- GPT-6.1 Sol added failing synthetic regressions before fixing historical
  citation reassignment, stable-ID kind-cycle overwrites and lexical-match
  knowledge loss. Existing historical label/URL pairs survive; actual current
  citations are required. Independently cited near-matches now persist as
  reciprocal-linked drafts rather than discarding the new body.
- Same-kind targets are loaded before synthesis, including when the last result
  was a different kind. Archived targets are not resurrected. Exact URL/text/
  provider equality can recover an incomplete public-message input from a
  complete permitted archive; historical evidence IDs are never guessed.
- Counterevidence additions, removals and role changes now reopen the ingest
  queue. Same-kind refresh preserves prior conflict IDs. Supporting-only hashes
  retain their old format. Parent health-summary regressions first failed three
  cases; the summary now uses the same fingerprint and recognizes either role.
- GPT-6 Luna added source-provenance and exact-target regressions. Chat filing
  accepts only references associated with actually provided/cited IDs, remains
  draft regardless of model review-status tags, and hydrates the model-selected
  active candidate rather than an unrelated highest-scoring page. Unknown or
  archived targets and create actions that would overwrite existing IDs fail.
- Parent verification after worker fixes passed 247 focused chat/ingest tests;
  the health/conflict suite passed 52. After the runtime harness correction
  described below, the fresh full-project run passed 3,810 tests, with two
  skipped and 41 warnings in 613.81 seconds (exit code zero).
- Later live reread found that scheduled source-digest regeneration had removed
  both manual recovery text pointers described in the earlier archive-only
  receipt. Those historical checks remain valid at their recorded time, but are
  not durable full-pipeline restoration. Repeating the pointer patch would not
  fix the missing event-queue integration.
- The already verified public Telegram message 65664 was then delivered through
  the signed loopback webhook: one append, confirmed duplicate retry, and its
  complete body was visible in the recent event queue. A regenerated analysis
  digest preview reads that body but still fails on other incomplete inputs;
  the current ticker preview does not classify this message into NVDA. Neither
  aggregate is relabeled created or claimed restored.
- An isolated input made from this actual recent event, its actual evidence ID
  and persisted archive was verified through the normal queue+archive reader.
  JSON-decoded model input contained all 1,071 body characters, and raw hashes
  stayed unchanged. The callback stopped at the model boundary: no model call,
  new synthesized knowledge, factual review or live wiki write is claimed.
- The user explicitly chose to retain query-time 1,600-character wiki excerpts.
  Ingest still reads eligible complete source bodies within its existing
  budgets; query does not read complete long wiki pages. The proposed full-body
  query change was not implemented and is no longer a pending approval.

Evidence: `/tmp/wiki-public-recovery-delivery-receipt.json`,
`/tmp/wiki-public-event-readiness-receipt.json`, new integrity/provenance tests,
`/tmp/wiki-integration-safety-ledger.md` and the requested-model audit reports.
Passing tests establish the guarded software behavior, not truth of every claim
or complete multi-page semantic integration.

## Runtime Harness Regression (2026-10-08)

- The first new whole-suite run finished with 3,808 passed, two skipped and one
  failure in `test_agent_answer_hands_validated_citations_to_real_postprocess`.
  The failure was reproduced by importing `sitecustomize` before that test.
- Its obsolete runtime patch replaced `agent.answer`, bypassing the current
  structured-citation, retrieval and postprocessing pipeline and dropping the
  async/context-pack keyword API. A new import/reload contract test reproduced
  the replacement failure before edits. This was a production compatibility
  defect, not grounds to relax the provenance test.
- The old replacement answer, context loader and duplicate fallback formatter
  were removed. The compatibility module keeps merged cross-surface history and
  routing while the native answer function stays intact. Explicit non-market
  defaults retain precedence over incidental recent history for short followups.
- The ordered sitecustomize/provenance/core-answer/LAYA/evidence suite then
  passed 232 tests. The second fresh full-project run then passed 3,810 tests,
  with two skipped and 41 warnings in 613.81 seconds. This success follows the
  correction; the first run remains a recorded failure.
- Before this last harness edit, both real wiki views rendered without errors
  or model calls. Runtime health was `ok` for the dashboard and configured/ok
  for the webhook. Startup was newer than the worker integration files; the
  later harness edit needs its own post-change freshness check.
- The post-harness check at 17:42 UTC confirmed the dashboard worker started
  at 17:39:01, after the latest changed integration module at 17:38:35. Both
  internal health endpoints passed. A fresh process preserved native
  `agent.answer`, both async/context-pack parameters and the 1,600-character
  wiki excerpt limit. The two actual-data wiki views were rendered again with
  zero exceptions and zero model calls. This is AppTest coverage, not a browser
  graph/canvas click test or proof of authenticated collection.

Evidence: `/tmp/wiki-integration-ui-receipt.json`,
`/tmp/wiki-runtime-health-receipt.json`, the ordered regression outputs and
full-suite session 27642 (exit code zero). `git diff --check` also passed.

## Continued Integrity Audit (2026-10-09)

- The prior completed verification turn made concrete progress. A later
  delegated audit attempt ended at the requested models' usage limits; neither
  worker produced a new review, and both handles were confirmed terminal.
  The parent continued directly without changing the requested model choice.
- Three new RED tests showed that distillation persisted model-invented URLs,
  wiki refs and raw hashes despite a valid body citation. The final provenance
  list now comes only from actual input articles, the source digest and previous
  same-kind knowledge. Historical refs remain covered; no live-page deletion or
  blanket reclassification was performed.
- A new RED storage test showed a complete long generated synthesis was cut
  before it reached the existing child-page splitter. The common payload builder
  now redacts without discarding the tail and lets storage split it losslessly.
- Two further RED tests showed chat plans were clipped before JSON parsing and
  replaced by shorter heuristic prose, while an overbudget response could be
  accepted from its truncated prefix. Parsing now accepts complete bounded
  responses up to 100,000 characters or rejects overbudget input, without a
  12,000-character preprocessing cutoff. Chat query snippets remain 1,600 chars.
- GREEN results: 77 targeted tests, then 295 chat/core/distillation/maintenance
  tests; a broader wiki/acceptance/Hermes/source suite passed 410 tests with one
  skipped. The earlier whole-project 3,810-pass run predates these new edits;
  it is not a new whole-project verification claim.
- A fresh read-only all-active-source snapshot checked 1,630 source digests:
  five reached the model boundary, 1,592 lacked eligible article/citation input,
  31 were incomplete and two exceeded aggregate article count. No model was
  invoked. Concurrent writers changed the store during the audit, so its
  captured counts do not prove a final immutable live corpus or factual accuracy.
- The user selected source-by-source compilation followed by related-wiki
  integration. The user subsequently approved the written design at
  `docs/superpowers/specs/2026-10-09-source-compiled-wiki-design.md` and requested
  an implementation plan. Plan approval is still required. This does not
  approve new raw retention, oversized-source chunking or rejected payloads.

Evidence: `/tmp/wiki-source-readiness-20261009.json`, new regression tests and
verification outputs; runtime health at 02:23 UTC confirmed startup after these
module edits. The actual-data console wiki tab and standalone wiki view then
rendered with zero exceptions and zero model calls. Final health checks at
02:28 UTC remained normal. Browser graph/canvas interactions and protected-source
login were not verified. Overall completion remains unproven.

## Post-Plan Acceptance Recheck (2026-10-09)

- Previous goal turn: progress, not a live-process wait. It produced the approved
  design's implementation plan and self-review. The new pipeline remains
  unimplemented and disabled while plan review/execution confirmation is pending.
- A fresh isolated 14-file acceptance/regression run passed **172 tests in
  3.09 seconds**, exit code zero. Scope: existing raw-body distillation,
  provenance, source identity/validation, full-source classes, FRED/community
  handling, chat file-back, lossless child storage, stale-knowledge retention and
  Hermes ingress. It does not cover the not-yet-built source-revision journal or
  atomic multi-page integration, and is not a fresh full-project suite run.
- Test scope was inspected rather than inferred from names. In particular,
  `test_raw_body_is_compiled_once_into_persistent_cited_knowledge` invokes the
  compiler once and checks persistence/citations; it does not repeat a source or
  prove content-identity compilation reuse. Existing conflict tests check
  prompt/provenance roles, not semantic entailment or a persistent dated claim
  graph. Those missing assertions are owned by the new implementation plan.
- A fresh read-only source audit at **02:40:57 UTC** captured **3,982 wiki pages**
  and **1,630 active source digests**. The current gates were: 1,592 without
  eligible article/citation input, 31 incomplete-body failures, two aggregate
  article-budget failures and five that reached the deliberately stopped model
  boundary. No model was called. The store hash matched before/after this audit;
  this establishes the observed interval, not a permanently frozen corpus.
- Five blocked digests contained **20 eligible article associations**: three
  digests failed on incomplete inputs and two on aggregate count. This is
  observed evidence for independent source scheduling, not 20 unique revisions
  or proof that those originals pass the new byte/hash gate. No source repair,
  synthesis, blind retry reset or generated factual review was performed.
- At **02:41:00 UTC**, dashboard health was `ok`, Hermes was configured/ok and
  dashboard PID 434908 had started after the latest integration-module changes.
  Health does not prove rendered graph interactions, protected article access or
  successful source-to-knowledge integration. No restart or activation occurred.

Evidence: `/tmp/wiki-source-readiness-postplan-20261009.json`,
`/tmp/wiki-runtime-health-receipt.json`, pytest session 89319 (exit zero),
source audit session 22490 (exit zero), inspected existing acceptance tests and
`docs/superpowers/plans/2026-10-09-source-compiled-wiki.md`.
Overall goal completion remains unproven; plan review is still pending.

## Source Worker Implementation And Hermes Health (2026-10-10)

This entry supersedes earlier plan-approval/implementation-pending statements;
it does not supersede their dated factual-review or source-access caveats.
The user approved direct TDD implementation on 2026-10-09.

- Tasks 1-6 now implement verified revision discovery, immutable compilation,
  role-specific receipts, complete target hydration, guarded multi-page updates,
  lossless child/provenance storage and independently recoverable effects.
- A staged-only checkout passed 126 source/schema tests. Later real-event
  inspection exposed Telegram records without a top-level kind; a reproduced
  two-case regression now resolves the existing classification or a narrowly
  scoped public-message default after provenance checks. A read-only recheck
  found 100 eligible archived Telegram messages. No real article was sent to
  a model and no production knowledge was changed by that probe.
- The recent queue contained 10,176 events: 100 archived Telegram bodies and
  10,076 without complete archive pointers (mostly market/prediction snapshots).
  They are not counted as successful source compilations. SaveTicker was absent
  from this queue; authenticated collection remains unverified.
- Live wiki inventory: 3,982 total, 3,102 active; 1,633 active source digests,
  924 risk, 461 playbook and 84 concept pages. Of active pages, 2,773 are draft
  and 329 reviewed. These are stored labels, not this worker's factual audit.
- No Hermes browser collection schedule was installed. The receiver is
  configured/healthy and passive (`on_demand`). The bot's real collection-gap
  check returned null with unchanged last delivery `2026-10-09T02:19:23+09:00`.
  Scheduled mode still alerts after its real delivery SLA; other source alerts
  remain intact. This repairs false passive-ingress alerts, not freshness.
- The new worker flag is off and its status showed zero pending operations.
  Semantic evaluation cases and operator recovery/rollback instructions are
  supplied; a 6.1 Sol independent code review is underway. Model-output semantic
  review and live activation remain pending.
- Fresh full-project run passed 3,953 tests with two skips and 41 warnings in
  653.89 seconds. It began before the final native-Telegram classification and
  routed-new-concept retry fixes; those final changes passed the later 235-test
  focused source/acceptance/provenance/maintenance/ingress run. No post-final-fix
  whole-project success is inferred from the earlier run.

### Remaining Completion Gaps

- SaveTicker authenticated server browser access and an actual protected-article
  delivery remain unverified. A credential vault is not a completed login.
- No unattended Hermes browser collection job is enabled by this work. The
  public probe used the Hermes Python environment and explicit sender commands.
- Oversized source compilation needs the proposed resumable chunk workflow;
  current explicit failure is safer than truncation, but is not completion.
- Existing draft knowledge still needs source coverage and semantic maintenance
  review. One real generated draft does not establish quality of the whole wiki.
- New references retain their identity, but historical truncated/masked
  references still need unambiguous manifest-based recovery with TDD before
  claiming complete archive coverage.
- Durable provenance across original TTL and bundle expiry remains incomplete.
  Missing evidence-ID metadata must not be inferred from matching URL prefixes.
- A proposed same-URL refresh defect was ruled out: evidence IDs already hash
  the complete event JSON. Speculative source-fingerprint metadata was removed
  rather than keeping a fix justified only by mocked IDs.

## Public-Only Source Worker Probe (2026-10-11)

The user authorized a small existing-Codex-engine probe with public originals
only. Login articles, personal conversations and production wiki contents were
excluded. The sample was public Telegram message `insidertracking/65880`.

- A Korean source prompt exposed a general provider-contract defect: valid
  language-neutral routing JSON was rejected by the chat-language filter.
  An explicit structured-output mode now propagates through all four provider
  adapters and their race/fallback chain. It accepts a complete JSON object,
  rejects duplicate keys, non-finite numbers, excess nesting/size and mixed or
  partial output, and leaves ordinary chat language/refusal checks unchanged.
- The source worker uses this contract without shortening its configured
  request budget. New regressions were observed failing before implementation;
  222 provider/chat tests and 43 source/structured-output tests passed.
- An offline audit-hook proof blocked writes outside the temporary root.
  The subsequent real probe used only the approved public source and one
  temporary candidate page, with Codex read-only sandbox and no web search.
  Three calls completed compilation, routing and integration: one source
  compiled, one operation data-committed, zero pending effects or stale bases.
  All 145 observed Python write attempts remained within isolation.
- The compiled note and existing concept contain actual source-derived prose,
  attribution, uncertainty caveats and reciprocal wiki links. Temporary
  index/schema/log and Markdown exports were written; the chronological log
  includes the operation's source-ingest entry. The 68-character routing JSON
  was retained exactly, demonstrating the formerly rejected response class.
- This is one isolated sample, not production activation, whole-corpus truth
  verification or proof that protected collection works. Original paths outside
  the relocated archive root are masked in this temporary display; canonical
  public URL and original hashes remain in the compilation/receipt artifacts.
  Production source coverage and historical-pointer repair remain open.
- Live Hermes health remains `configured=true`, `ok=true`, `on_demand`, and its
  service is active. This confirms the receiver, not an unattended collector.

Evidence: `/tmp/wiki-guard-proof-zpwk2dwm/public-evidence.json`, isolated operation
`710e1236f616095e9a1ab2a5c17609e4d7b5516132d1b3fefd1a5e480db8d9f8`.
Independent code/semantic review and a fresh full suite are being completed;
the overall goal and production activation remain open.

### Review Follow-Up (2026-10-13)

- Sol identified a prefix-validation bypass, ordinary-chat bounding regression,
  single-line-fence mismatch and a shorter default worker budget. Twenty real
  adapter-boundary regressions reproduced the first three; the budget mismatch
  was separately reproduced. All were fixed without weakening target schemas.
- A cold-import regression proved the structured validator initialized dotenv
  and root logging through an operational report module. The parser now lives
  in side-effect-free `lib/structured_output.py`; the old report helper remains
  an import alias. Existing report behavior is not loaded by generic validation.
- Luna's single-public-sample audit found no material unsupported causal or
  financial strengthening, but identified omitted examples in the prose summary.
  The stored full claim set is preserved; the page section label now says
  `Source-derived summary`, not `Complete source compilation`. This does not
  make the summary exhaustive or establish the community claims as facts.
- The exact successful public model outputs were replayed after the final code
  changes with zero model calls: one compilation and one committed integration,
  zero pending effects, 136 isolated Python writes. No additional body was sent.
- Final focused validation passed 121 tests; an independent staged-only checkout
  passed 158 source/provider tests. The final additional Sol review was unavailable
  due to usage limits; no independent sign-off for the last extraction is claimed.
- Hermes was rechecked: service active, health configured/ok and on-demand.
  A fresh full-project run is still in progress; no final result is inferred.
- The sample's individual raw/text files had been cold-compacted. The existing
  archive reader resolved both logical paths; original HTML and extracted text
  SHA-256 hashes still matched the approved snapshot. Checking loose-file
  existence alone would have incorrectly reported source loss.

### Final Software Verification (2026-10-13)

The fresh full-project run after the final parser and worker changes completed
with **4,009 passed, two skipped and 41 warnings in 869.96 seconds**, exit zero.
Log: `/tmp/wiki-structured-final-pytest-20261013.log`. Staged-only 158 and focused
121 results above remain narrower checks, not substitutions for this full run.
Both working-tree and staged diff checks passed. This completes this provider-
contract repair, not the overall source-recovery/production-activation goal.
