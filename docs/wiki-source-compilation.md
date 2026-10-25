# Source-Compiled Wiki Operations

## Current Rollout

The source worker is implemented but defaults **off**. Existing collected data
and wiki knowledge are retained. Structural tests use synthetic sources and
injected models; they do not establish model accuracy or authenticated access.
Do not enable production before reviewing a permitted-source probe, payload
destination and semantic evaluation. The chatbot's 1,600-character body excerpt
contract and existing raw retention remain unchanged.

## Stages And Evidence

- Discovery verifies stored original and extracted text, complete hashes,
  provider, URL and scope. Missing or expired originals remain `unavailable`.
- An individual source above 20,000 characters remains `pending_chunking`;
  it is not truncated into a successful compilation.
- Compilation stores complete reusable prose, stable dated claim IDs and
  limitations. A durable skip records its reason, not an empty wiki page.
- Routing retains examined, selected, rejected and unexamined candidate IDs.
  Candidate exhaustion does not prove semantic completeness.
- Integration appends dated draft claims to complete target history. Original
  prose, exceptions, existing trust state and source references are retained.
  Oversized bodies/references use discoverable child pages. A compiled draft
  note links to related entities/topics, even without an entity target.
- A prepared operation contains fixed normalized records and semantic guards.
  Recovery recognizes all-before, all-after or stale-base states. Changed
  targets require a new proposal; stale/failed operations remain visible.
- `data_committed` means verified storage, not factual correctness. Markdown/
  index, search and operation log effects have separate retryable states.
- Retry and role changes reuse compilation; only actually observed evidence
  IDs are associated. A recovered old operation cannot roll back routing.
- Advancing routing invalidates integration completion until the prepared
  batch actually commits. A failed final pass remains retryable even without
  new evidence IDs. Recovery of an earlier committed operation preserves the
  completion flag of a newer routing pass rather than marking it finished.
  Generated child ordinals use the terminal numbered suffix.
- Provider prompts use a knowledge-only target projection. Raw records,
  conversation messages, usage queries and context packets are not included.
  Full knowledge prose can still be private, so payload approval remains a gate.
- Artifact and complete search publications share a global file lock acquired
  before reading a fresh snapshot. This prevents an older concurrent rebuild
  from overwriting newer output; it serializes publications rather than commits.

## Merge, Cleanup And Context

- `_merge_pages` (chat curation, autonomous curator, dedup) refuses to mix
  source-backed and unverified pages. The result keeps the least reviewed input
  status; any LLM synthesis makes it `draft`.
- A merge reads each page's full semantic snapshot, split children included,
  unions claims (one `claim_id` with different facts refuses the merge),
  limitations, relations and integrations, and keeps references/evidence beyond
  parent caps in provenance children. Bodies over the limit use the lossless
  split rule. Every affected parent and child is written in one fingerprint-
  guarded batch; source children are archived with the source, never left
  active under an archived parent.
- `wiki_cleanup.apply_cleanup` rechecks, backs up and writes under the store
  lock; a page changed after planning is `skipped_stale`.
- `build_context_section` drops archived pages from retrieved and injected
  pages unless `include_archived=True`; `list_pages` keeps the full history view
  by default for the browser and batch jobs.
- The autonomous curator spends `WIKI_AUTONOMOUS_MAX_JUDGMENTS` (default 12)
  per run before each LLM call, including failures and skips, and remembers
  unapplied outcomes per input hash in
  `<wiki artifacts>/autonomous-curator-state.json` (failures 1 day, others
  30 days) so unchanged candidates are not re-judged.

## Safe Inspection

Run from `/home/ubuntu/projects/stock-report`:

```bash
WIKI_SOURCE_PIPELINE_ENABLED=0 .venv/bin/python -m reports.wiki_source_pipeline --dry-run
WIKI_SOURCE_PIPELINE_ENABLED=0 .venv/bin/python -m reports.wiki_source_pipeline --status
WIKI_SOURCE_PIPELINE_ENABLED=0 .venv/bin/python -m reports.wiki_source_pipeline --recover-only --dry-run
```

Dry-run does not invoke a model, change wiki data or write receipts. Status
reports existing journal operation stages, not all historical source access.
Receipts/artifacts live under the configured reports root in `wiki-source-v1`.
Operational logs contain codes and IDs, not article bodies or credentials.

## Activation And Recovery

After separate activation and provider-payload approval, enable
`WIKI_SOURCE_PIPELINE_ENABLED=1` in the worker environment. Use one entrypoint:

```bash
.venv/bin/python -m reports.wiki_distillation --limit 5
# Or run the source worker directly, not both owners concurrently:
.venv/bin/python -m reports.wiki_source_pipeline --source-limit 5 --target-limit 20
.venv/bin/python -m reports.wiki_source_pipeline --recover-only
```

The legacy distiller CLI dispatches exclusively to the new worker when enabled.
Its direct `run()` excludes source digests under that flag; manual/chat APIs
remain unchanged. No new cron is installed automatically. Source and sorted
target file locks serialize workers; semantic guards reject unrelated edits.
Before backfill, preview exact eligible revisions and take existing store
backups. Never reconstruct missing evidence from URL equality alone.

Rollback disables the flag. It does **not** erase committed knowledge or replay
legacy receipts into the new journal. Re-enable only after diagnosing unresolved
operations and checking the existing wiki snapshot.

## Semantic Review

`tests/fixtures/wiki_source_semantics.json` is a manually authored synthetic
review rubric: capacity versus orders, differing horizons, attributed community
claims and OCR uncertainty. Its pending verdict fields are the original authored
rubric, not auto-generated correctness labels. Evaluate entailment, mechanisms, dates, scope, attribution,
overconfidence and routing omissions separately. A requested 6.1 Sol independent
code review identified retry, metadata privacy, child ordering and publication
race defects. Each was reproduced and corrected with regression tests; bounded
re-review found a combined old-effect/final-pass failure, also reproduced and
corrected. This code review is not a certificate of factual correctness.
Recorded Sol model samples are stored separately from the authored rubric in
`tests/fixtures/wiki_source_semantic_outputs.json`; they pass through the runtime
compiler schema in tests. Independent Luna semantic assessment remains separate
from structural validation and from a live-source/provider rollout.
`tests/fixtures/wiki_source_semantic_review.json` records Luna's independent
assessment: all four synthetic compilation samples passed those semantic checks.
Routing omissions are explicitly `not_tested`; live-source/provider behavior and
production activation remain unverified. These samples are evidence of a bounded
experiment, not a general model accuracy score.

### Public-Source Probe On 2026-10-11

The user approved a small public-Telegram-only Codex probe, excluding login
articles and personal chat. The first sample was rejected because the model
added claim `id` fields. The validator was not weakened; the compiler prompt now
illustrates the exact permitted schema and explicitly reserves IDs to the server.
A TDD regression preserves this prompt contract.

The original probe observed a changing production-store hash, so a retry was
blocked by safety review. No probe page IDs were found in the live store, but
that alone does not prove store immutability. Isolation was strengthened with a
Python audit guard rejecting writes/SQLite connections outside the temporary
root and restricting subprocesses to read-only Codex in that root. Offline
replay blocked an attempted outside write; all 34 write attempts stayed isolated.
The reviewed guarded retry compiled the public source successfully, with all
45 Python write attempts isolated, but routing failed and no wiki batch committed.

There is a further shared-provider compatibility issue: valid machine-only
routing JSON is rejected by the chat language filter when the source prompt is
Korean but IDs/keys are English. A direct local reproduction accepted the same
JSON with an English prompt and rejected it with a Korean prompt. Actual raw
routing output was not retained, so this is a verified compatibility defect,
not a proven exclusive explanation of the probe failure. A general structured
provider path still needs implementation and fresh live validation. No production
activation or successful real-source end-to-end result is claimed.

Final structural verification on 2026-10-11: whole-project tests 3,967 passed,
2 skipped, 41 warnings; planned focused tests 235 passed; a staged-only checkout
passed 150 source/schema/health tests. Warnings remain reported, not silently
discarded. These green results do not erase the failed real-source routing probe.

## Hermes Incident

The receiver can be healthy while no browser collection job is running.
`HERMES_SOURCE_COLLECTION_MODE=on_demand` is the passive ingress default: delivery
gaps are visible but are not treated as a scheduled 24-hour SLA. These sources
do not inflate the healthy scheduled-source count. Existing `last_success` is
never reset to silence an alert. Invalid mode configuration fails closed to
scheduled monitoring.

Set `HERMES_SOURCE_COLLECTION_MODE=scheduled` only after a recurring authorized
sender is working; delivery gaps then alert normally. This policy does not hide
other sources' failures or receiver downtime. On 2026-10-10 the receiver was
restarted and the actual bot gap checker returned no Hermes delivery warning.
Authorized SaveTicker login and unattended collection remain unverified. A
healthy webhook endpoint is not evidence of fresh articles.
