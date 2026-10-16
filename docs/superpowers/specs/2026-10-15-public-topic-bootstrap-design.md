# Public Topic Bootstrap And Accumulation

Status: proposed specification; conversational direction approved, specification
and implementation plan not yet approved. No runtime change is authorized by
the existence of this document.

## Intent And Evidence

Build a persistent wiki that accumulates related source knowledge, rather than
only creating independent summaries. Preserve original bytes, source-specific
summaries, attribution, dated exceptions and opposing claims. The existing
1,600-character chat excerpt and public-only operating limits stay unchanged.

The inspected public sample contains 35 compiled sources and 39 attributable
pages. Thirty-five pages are intentionally source-specific and excluded from
update targets. Only four reusable topic candidates remain; none covers repeated
Riyadh airport, Houthi, Hormuz or energy-ceasefire sources. No inspected page has
claims from more than one revision. This is a topic bootstrap gap, not evidence
that privacy filtering should be disabled.

## Selected Approach

Keep source notes read-only. Supply a small, verified set of related public
source-note evidence to the routing model as context, distinct from editable
topic targets. The model may propose a separate topic or update an existing one.
It must explain the relationship and preserve disagreements; lexical similarity
only selects candidates and never establishes truth or authorizes a merge.

Alternatives rejected: making source notes ordinary editable targets would mix
source-specific and cross-source accounts; merging on keywords would confuse
homonyms, dates and opposing reports; waiting for topics to appear spontaneously
does not address the observed bootstrap gap.

## Public Evidence Boundary

1. Reuse the committed-operation fingerprint allowlist and public revision gate.
   Reject private, changed, archived, missing, unverifiable or expired originals.
2. Verify each context note against its journal compilation, original/text hashes
   and current page fingerprint before model exposure. Use archive-aware reads.
3. Build an allowlisted projection of retained compiled claims, dates, scope,
   limitations and source URLs. Do not serialize arbitrary page metadata or
   nested fields. Context carries explicit source-note and revision identities.
   Omission diagnostics and dependency hashes remain local. Model input uses a
   dedicated notes-only projection after validation, never the full context.
4. Related source notes are never included in the editable-target allowlist.
   Model instructions embedded in source prose remain untrusted data.
5. Revalidate every dependency before commit. A concurrent private edit or hash
   mismatch invalidates the proposal; no stale public snapshot may be published.

## Candidate Discovery And Budgets

Search only validated public source-note metadata and compiled terms, within the
current surface. Reuse explicit verified aliases where present; do not silently
invent bilingual aliases. Rank by non-generic term overlap, stable ID tie-break.
Exclude the current source and duplicate revisions. Return at most four context
notes, alongside the existing editable-topic candidates.

Context uses complete claim units, never clipped claim text. Each admitted note
must fit a 12,000-character serialized projection; total context is at most
24,000 characters. Oversized notes are omitted with an explicit reason, not
presented as fully reviewed. A later resumable evidence-selection pass may cover
omitted notes; it is not silently substituted in this change.

All model work occurs within the existing source attempt reservation: at most
five source attempts per run and twenty per KST day, failures included. Context
selection introduces no unreserved background calls or unlimited retries.
Historical backfill requires an explicit reserved attempt; existing completed
receipts are not silently reset or replayed.

## Routing Contract

The prompt separates `editable_topics` from `read_only_source_context`. Its
response classifies editable targets as before and may propose a reusable topic
with title, kind, surface, purpose, contributing revision IDs and a concise scope
explanation. Only supplied revisions can contribute. The model may decline when
the evidence is unrelated or insufficient; topic creation is not a success quota.
New reusable topics use kind concept and purpose topic_synthesis; source notes
retain their current purpose and identity rules.

Topic identity is allocated once by the server using the existing deterministic
new-spec mechanism; later sources route to that topic ID. A coincident title
does not identify a topic. Concurrent overlapping topic proposals are reported
for review rather than auto-merged by title.

## Multi-Source Integration Contract

The current single-source validator only permits current compiled claim IDs and
resolves `[S1]` to the current URL. It must not be reused unchanged for a
multi-source seed. Introduce a distinct validated proposal with an explicit
server-built source map and a claim allowlist per revision.

- Every addition identifies its contributing claim IDs and source revision IDs.
  IDs, dates, scope and URLs must resolve to supplied compiled evidence.
- Citations resolve through the supplied source map, never arbitrary model URLs.
  An addition combining sources cites each contributing source.
- Claims retain their original IDs and revisions; imported prior claims are not
  reassigned to the current source. Limitations are unioned without deletion.
- Relations resolve both endpoints, revisions and dates. Missing relationships
  remain unresolved, not adjudicated. All newly synthesized content stays draft.
- Existing prose, counterevidence, source refs and children are preserved. The
  proposal appends a scoped synthesis; it cannot rewrite previous claims.
- New topics must actually retain at least two verified contributing revisions
  to count as multi-source bootstrap. A one-source proposal may remain a normal
  single-source topic but cannot satisfy the accumulation acceptance gate.

## Durability And Recovery

Prepare one journaled topic transaction containing dependency fingerprints,
compiled-artifact hashes, target before hash, per-revision claim sets, source map
and expected after fingerprint. Commit topic data once under ordered locks.
Source-note records and original archives must remain byte-for-byte unchanged.
Retry validates dependencies and resumes the same transaction without duplicate
claims or additions. Failed dependencies require rerouting, not overwriting.

Index/search/log effects retain their recoverable receipt stages. This feature
does not claim to solve the separately pending multi-file publication design.
Record all contributing revisions in the topic ingest log without raw bodies.

## TDD And Acceptance Gates

Synthetic tests first, no production sources sent during development:

1. Two related source notes bootstrap a separate draft topic; originals and
   source-note fingerprints remain unchanged; both revisions survive export.
2. A third source appends to that same topic without deleting prior prose,
   exceptions or IDs. Opposing claims retain dates and unresolved relationships.
3. Shared generic terms, homonyms or unrelated scopes do not force topic merge.
4. Private/changed pages, expired or missing originals and forged revisions never
   reach model context or commit. Context cannot become an editable target.
5. Forged claim IDs, wrong dates, cross-source citation substitutions and unlisted
   URLs fail validation. Nested private metadata is excluded from prompts.
6. Crash before/after commit and effect failures resume idempotently; concurrent
   dependency changes reject stale proposals. Context budgets are deterministic.
7. Long-topic splitting remains lossless across claims, provenance and relations;
   Markdown, widget navigation and chat citation contracts remain compatible.
8. Existing quota, public-scope, source integration and whole-wiki tests pass.

After separate operational approval, use a reserved public-only attempt to test
one related pair and one follow-up source. Inspect generated synthesis against
the retained originals, not just counters. Success requires actual retained
multi-source content with qualifications, a subsequent update, intact originals,
resolvable citations and zero unauthorized exposure. No private article model
processing, trust promotion, quota reset or broad activation is included.

## Approval Sequence

Review this specification first. After approval, write the implementation plan
with disjoint tasks, failing tests, validation commands and rollback steps.
Select execution mode before product changes. Existing SaveTicker human auth,
latest-list activation and publication-design approvals remain independent.
