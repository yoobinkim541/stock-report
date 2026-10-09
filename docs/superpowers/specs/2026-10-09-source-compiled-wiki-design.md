# Source Compilation And Persistent Wiki Integration

Status: design draft awaiting written-spec review. Not implemented.

## Intent And Agreed Constraints

The user selected source-by-source compilation followed by integration into
related wiki pages. This must make the wiki a persistent synthesis, not a set of
source links or a fresh reconstruction on each chat question. A source revision
is read once for compilation; its knowledge can affect several existing pages.
Old conclusions, exceptions, counterevidence and attribution must not disappear
when new information arrives.

Keep the user's query-time 1,600-character wiki excerpt preference. That is not
a source-ingest or stored-knowledge truncation policy. Keep existing source
classification, authorized Hermes ingress, raw archive, wiki APIs, markdown
mirror, index, append-only history and search. Do not create another chatbot.

The selected architectural direction permits this design draft, not automatic
implementation, changes to retention, new credentials or provider calls with
previously rejected private payloads.

## Current Evidence

- `reports/wiki_distillation.py` currently compiles a source digest, which may
  contain several articles. One incomplete input blocks the whole digest.
- Its stable IDs are based on digest ID and kind. Refresh can preserve an
  existing same-kind target, but a single result does not maintain all affected
  entity/topic pages. Lexical similarity now creates a reciprocal link rather
  than discarding independently cited new prose.
- `reports/hermes_source_webhook.py` already supplies complete body, raw hash,
  extraction scope, provider and source URL to the normal event/archive path.
  An accepted delivery means source persistence, not knowledge integration.
- Evidence IDs hash full event JSON. Repeated collection metadata can change
  those IDs without changing the underlying source body. Do not redefine them
  or invent historical IDs during migration.
- Wiki storage already supports lossless long-body child pages and revision
  snapshots. Integration must hydrate their content, not only the parent
  overview. Chat query snippets remain separate.
- A captured 2026-10-09 audit checked 1,630 active source digests. Five reached
  the model boundary; 1,592 lacked eligible article/citation input, 31 had an
  incomplete body and two exceeded the eight-article aggregate budget. No model
  was called. Concurrent writers changed the live store during this read-only
  audit, so the counts describe its captured snapshot, not a locked final state.

## Alternatives

1. **Source compilation then page integration (selected):** isolate failures,
   reuse compiled knowledge, record per-target completion and retain the history
   of revisions. Cost: a journal, more version metadata and recovery logic.
2. **Keep aggregate-only processing:** fewer moving parts, but unavailable
   inputs continue to hold up readable sources and completion is ambiguous.
3. **Direct source-to-page updates without compilation receipts:** less storage,
   but routing retries reread/recompile the source and increase duplicate writes
   and provider cost. Not selected.

## Layers And Source Identity

Use three existing conceptual layers: immutable raw/text evidence, a compiled
source revision, and maintained wiki pages. A compiled revision is local,
source-backed draft knowledge; it is not automatically a reviewed fact.

The source revision key hashes a versioned structured object containing:

- normalized source provider and an allowed canonical source URL;
- full raw SHA-256 and full extracted-text SHA-256;
- extraction scope and provenance class;
- the source compilation protocol version.

Do not shorten URLs, guess hashes or use title similarity as identity. Full
identifiers remain in the journal. Exact event IDs are associated with this
revision as actual observed deliveries, not synthesized from a matching URL.
Publication, collection and ingestion times remain separate.

Repeated deliveries of identical revision content reuse compilation. Changed
raw/text hashes or extraction scope create another revision. A role change
(supporting/conflicting) creates new integration work without necessarily
recompiling identical source text. Compilation and integration fingerprints
therefore differ.

## Discovery And Complete-Body Gate

Discover persisted permitted events and exact archive manifests through the
existing collector/archive APIs. Keep digest pages as source-navigation views,
but do not make one aggregate's completeness the scheduling prerequisite for
an independent source revision.

Before a model call, require an eligible complete extracted body, provider/URL
agreement and verifiable archive identifiers. Login/challenge/preview content
is not an article. OCR is marked as OCR, community claims retain attribution,
and FRED notes are not treated as observed macro data.

Raw originals never change during compilation or maintenance. If the original
has legitimately expired, retain that fact: a text hash cannot stand in for
verification of unavailable raw bytes. An expired or missing provenance manifest
cannot be reconstructed from a guessed URL prefix.

Oversized sources stay explicitly pending for chunk processing. This design
does not silently enable the separately proposed resumable chunk workflow or
alter its budgets. No truncated input may receive a complete-compilation receipt.
Likewise, durable provenance after raw expiry remains a separate retention
decision, not permission to retain all raw content forever.

## Compilation Contract

The compiler receives the complete permitted source body plus source metadata,
with external instructions treated as data. It produces:

- a reusable prose synthesis with scope, dates, mechanisms and uncertainty;
- claims with stable local IDs, actual source revision citations and optional
  exact text-offset anchors;
- extracted entity/concept/topic terms and typed competing-claim candidates;
- explicit source limitations and a reason when there is no reusable knowledge.

Text anchors must match the stored extracted body; model-supplied source URLs,
hashes and event IDs are never authoritative. Matching an anchor proves
attribution, not semantic entailment or factual truth. A model cannot promote
its own output to reviewed/stable status. Missing claims or uncertain meaning
remain visible rather than being converted into strong causal conclusions.

Persist the complete synthesis before marking compilation complete. Use the
existing prose/redaction/split storage conventions; a long synthesis is not
silently cut to fit one wiki page. A skipped source has a durable reason and is
not reported as integrated knowledge.

## Routing And Target Hydration

Use the existing index/search and verified aliases to find active relevant
entity, topic, concept, risk and playbook pages. Routing proposals name exact
existing IDs and explain the affected claims. Do not merge solely because of
similar titles, reinterpret generic source tags as ticker symbols, or resurrect
archived targets. New pages require a real knowledge purpose, not empty shells.

Process targets in bounded passes with a persisted cursor/backlog. A pass limit
does not mean all affected pages are integrated. Record examined candidates,
selected targets, rejected candidates and unprocessed work. Routing coverage
still requires an evaluation; it is not guaranteed by structural tests.

Hydrate the complete selected target and ordered active child bodies before
proposal generation. Bind the proposal to a semantic revision hash covering
the body, child bodies, source refs, evidence roles and knowledge state. Query
excerpts and usage counters must not substitute for this revision.

## Integration And Contradictions

Generate dated additions and explicit relations such as support, qualification,
supersession or competing interpretation. Every new claim cites the actual
compiled revision. Every claimed contradiction identifies both claim/source
versions, their dates and scope; disagreement on different horizons or measures
must not be collapsed into a fabricated contradiction.

Preserve prior prose and provenance as historical knowledge. A claim that is
superseded is marked and linked to the new dated claim, not silently deleted.
Prefer validated append/relation operations over unrestricted whole-page
replacement. Current summaries may change, but must remain traceable to the
retained claim history. Do not lose historical references through list caps;
overflow provenance belongs in discoverable child/provenance sections.

Proposals are drafts. Validation checks identity, allowed targets, evidence
membership, anchors, retention of old content and draft status. These checks
do not certify economic conclusions. Contradiction extraction and source-faithful
synthesis need separately labelled semantic evaluation fixtures and review.

## Durable Journal And Crash Recovery

Use a local versioned receipt/journal under the configured reports root, with
source revision and integration operation IDs. Distinguish at least:

1. source eligible / source unavailable;
2. compilation pending / compiled / intentionally skipped / failed;
3. routing pending / targets selected / unexamined backlog;
4. integration prepared / applied / committed / stale-base conflict / failed.

Each prepared operation records exact target IDs, before-revision hashes,
expected normalized after-revision hashes and the source revision. Validate the
whole proposed batch first. Compare target revisions under the existing wiki
store lock before using its atomic batch-write path. Do not nest its lock-taking
public methods inside that lock; introduce a documented guarded-batch API if
the implementation needs one.

Receipt files and the wiki JSONL are not one filesystem transaction. A durable
prepare record precedes the write. After a crash, verify actual target hashes
against the prepared result before declaring commitment or replaying. A changed
base is replanned; it is not overwritten. Serialize work for the same source and
target, and never claim that an uncommitted operation was fully integrated.

Rebuild markdown/index through the current artifact mechanism. Append logs with
operation IDs without raw bodies, credentials or private conversation text.
Index/log failures remain visible and retryable independently of the data
commit. Recovery must not duplicate prose, wiki pages or log operations.

## Compatibility And Rollout

Introduce an opt-in worker path first. Keep the existing digest curator and
manual/chat page APIs. Once the new worker owns source compilation, avoid
running the old aggregate distiller for the same sources; preserve old knowledge
and receipts instead of rewriting their completion state en masse.

Legacy source digests are navigation/evidence containers, not proof that the
new source revision was compiled. Backfill only exact permitted available
revisions. Leave missing originals unresolved; no blind retry reset, invented
evidence ID, credential transfer, access-control bypass or protected-source
recovery claim is allowed.

Do not enable unattended Hermes collection until an actual authorized protected
article passes body/hash/ingress/wiki verification. No external private-payload
approval, raw-retention approval or paid compute authorization is inferred from
this architectural choice.

## Acceptance Evidence Required Before Activation

Write failing regressions before each implementation stage:

- readable source A compiles while unavailable source B stays pending;
- identical revision/retry reuses compilation; changed text creates a revision;
- exact event IDs remain associated without fabricating old identifiers;
- one source updates two existing pages and can create a justified new page;
- existing target and child content, exceptions and historical citations survive;
- competing dated claims are retained, scoped and linked with both sources;
- forged refs/anchors/targets/status promotion and cross-surface writes fail;
- long synthesis is reconstructed exactly from its stored children;
- raw bytes and unrelated wiki pages remain unchanged;
- stale targets, two workers and crashes at every journal/write boundary do not
  lose knowledge or duplicate committed work;
- markdown/index/log persistence and query file-back remain interoperable;
- oversized sources and unavailable provenance are not labelled complete;
- query-time 1,600-character excerpts remain unchanged.

Then run focused and integration tests, a clearly labelled semantic evaluation,
and a supervised permitted real-source probe. Report compilation, integration,
source accessibility and factual-review results separately. Structural green
tests alone are not the final acceptance condition.

## Next Gate

Review this written spec with the user. After approval, write a TDD implementation
plan with ownership, migrations, recovery tests and activation gates, and have
the user select execution. No new subsystem code has been added for this design.

## Spec Self-Review

Parent review checked source-vs-integration identity, independent partial
completion, long-target child hydration, journal recovery and ownership of the
existing store lock. Query limits and new retention/provider permissions remain
separate decisions. There are no placeholder stages presented as implementation.
This is not an independent model review: both requested new audit workers
terminated at their usage limits before producing findings. Written user review
is still pending.
