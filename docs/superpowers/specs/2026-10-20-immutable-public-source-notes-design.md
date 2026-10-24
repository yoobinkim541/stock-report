# Immutable Public Source Notes And Topic Recovery

Status: approved for synthetic implementation through the user's approval of the
reviewed nine-task plan (call_b194b279b36a4a628f35d65bdd4ce042). Real source
materialization, pilot initialization/migration, model execution, deployment and
broader source processing remain separately gated. This approval is not a new
pilot allowance or an authorization-history reset.

## Intent And Evidence

Keep the user's existing editorial wiki intact while restoring genuine,
source-grounded accumulation: extract claims once from originals, retain dates,
limitations and counterclaims, then create and revise separate topic documents.
Do not make mutable editorial prose the proof that an original was verified.

The current pilot fails closed with outer `pilot_source_note_unavailable`;
candidate `_note` checks report `context_page_changed`. Its three approved
public originals still pass archive/compilation verification, but four candidate
note versions differ from their committed upserts in body/links. Across 74
single-source operations, 105 distinct targets lack a matching current receipt;
none passes the current page matcher. This does not prove the editorial changes
are incorrect, or certify every original in that inventory. See
`docs/audits/2026-10-20-topic-pilot-integrity-recheck.md`.

## Chosen Approach And Alternatives

Separate immutable, machine-verifiable source-note artifacts from mutable wiki
pages. Topic workers consume validated source-note projections and continue to
compare-and-swap the actual editorial topic they update.

Rejected: overwrite editorial pages with old receipt contents (loses edits);
recalculate old receipts to match current pages (fabricates provenance); ignore
body changes in public eligibility (could admit unrelated/private prose); reuse
all archives without explicit source policy (broadens authorization).

## Scope And Invariants

- Raw HTML/PDF/text/manifests, existing source receipts, existing compilations,
  existing wiki pages and all v1/v2 topic witnesses remain byte-for-byte intact
  during snapshot creation. No delete, archive or rename is implied.
- Only public, retained, unexpired sources with verified revision identity,
  archive hashes and successful compilation qualify. A URL or `compiled_source`
  tag is insufficient. Login articles and personal conversations are excluded.
- A source note is an extracted knowledge artifact, not a page of links. It
  includes original claim IDs/text, attribution, dates/scopes, limitations,
  competitors and exact validated anchors. It does not certify claim truth.
- Presentation edits cannot mutate a source note. Changed evidence, compiler
  output or projection version creates a different artifact identity.
- The existing chatbot 1,600-character excerpt limit, global public quotas,
  source retention rules and standing activation defaults do not change.
- This repair does not resolve unknown legacy links by inventing target pages.

## Artifact Contract

Use a dedicated directory beneath the existing journal root, not the editable
shared-memory event store: `source-notes/<note_id>/note.json` plus `note.md` and
`manifest.json`. These are proposed internal paths; no new global wiki root.

`note.json` contains a versioned, deterministic projection: revision ID,
projection version, canonical URL/provider/scope, original published/collected
dates when known, compiler-retained title, terms/verified aliases, claims and
limitations. Missing original dates stay unknown. Do not use a renamed wiki
page title, query text, usage counters or current editorial body as input.

The manifest binds the note and Markdown byte hashes, raw/text/compile hashes,
source protocol version, source revision, projection version and canonical URL.
Define `note_id = SHA256(canonical identity payload)` over the version and these
identities/hashes (including both output hashes), excluding operational timestamps
and its own ID. Canonical JSON uses the repository's sorted finite JSON rules.
Store absolute local paths only in local descriptors where required, never in
the public note text or model input. All resolved references stay within trusted
configured roots, with unsafe paths and symlink substitutions rejected.

To avoid a hash cycle, neither hashed `note.json` nor hashed `note.md` contains
the computed `note_id`, the final directory name or the final manifest hash.
The directory/manifest carries the ID after both note outputs have been hashed.
Tests must construct the ID from these ID-free bytes, not seek a hash fixed point.

Creation reuses hash-verified compiled content, not a model call. Validate that
content against the retained source with the established compiler/anchor
contracts, including resumed chunk results. Do not truncate claims to fit a note;
reject inconsistent data or use deterministic lossless parts with a manifest if
the existing validated compilation requires them. Bound model context separately.

Checksums prove consistency, not authenticity against an actor able to rewrite
the whole journal and archive. Do not claim signatures or external trust.

## Durable Creation And Reads

Acquire existing source locks in sorted revision order. Build in a same-filesystem
staging directory, validate all expected files, fsync files/directories, then
rename to its content-addressed directory. Verify an existing directory in full
before idempotent reuse; do not overwrite corrupt artifacts. A crash before the
rename leaves unreferenced staging, not an accepted note. A crash after rename
may safely reuse the fully verified artifact. Never overwrite or prune originals.

Each reader pins verified bytes/identity for its operation. Immediately before
model exposure and again before commit, revalidate the original source's current
eligibility, archive availability/expiry, compile hash, note manifest and exact
projection. Expiry/revocation cannot be bypassed by a previously valid snapshot.
Unknown policy state fails closed. No network refetch happens as an implicit
revalidation side effect, and no automatic future retention extension occurs.

The final directory's complete verified manifest is its publication boundary;
staging artifacts are never discoverable context. There is no mutable global
"latest snapshot" selector. Pin the explicit note ID, deduplicate source
revisions, exclude the current source from support selection, and deterministically
rank candidates under existing limits. Multiple compatible projection versions
must not silently substitute for a pinned version. No retention/pruning worker
may delete these new artifacts in this rollout. Source expiry still revokes new
use, independently of editable display-page status, archive, split or deletion.

## Context And Transaction Contract

Add an explicit snapshot context mode; keep legacy page-backed context unchanged.
New dependencies carry a discriminator such as `kind: source_note_snapshot`,
note ID, source revision, projection version, trusted artifact identity/hash,
source raw/text/compile hashes and surface. They do not masquerade as page IDs
or carry a mutable page fingerprint. Citation identity is the source revision
and canonical URL, not a display page's existence.

`read_only_source_context` contains only the existing bounded notes projection:
claim IDs, text, dates, scopes, limitations and attribution. No manifest paths,
raw local paths, omitted diagnostics, editorial snapshots or mixed-private
records enter that source-context projection. Keep
current maximum four notes and existing per-note/aggregate context limits;
report excluded oversized notes rather than silently clipping exceptions.

The separate `editable_topics` projection remains necessary for accumulating
earlier synthesis. Admit only current, independently public-eligible topic
versions backed by their version-specific receipt/witness checks; snapshot
eligibility never authorizes unrelated or privately edited target content.
Preserve its existing projection budgets and before hashes. The limited pilot
starts a separate draft and updates only that same verified pilot topic.

Both proposal construction and the final provider boundary must validate the
exact outgoing notes projection. `validate_topic_prompt` needs a versioned
snapshot mode with trusted caller-supplied pinned dependencies, then compares
the parsed outgoing envelope with the verified projection. Do not reconstruct
dependencies with legacy `_note(page_id, ...)`, accept model-supplied authority,
or skip the final-envelope check. The trusted side channel stays local.

Snapshot-based proposals/transactions use a new explicit v3 contract and receipt
directory/loader. Do not add new dependency shapes to v2 receipts or route new
work through its unchecked assumptions. Dispatch by validated version before
dependency access. V1/v2 serialization, witnesses, retries and negative guards
remain unchanged; unknown versions fail closed.

Version every necessary loader, transition, operation enumeration/retry dispatch,
public-target discovery and witness constructor, not just the prepare function.
An old v2 receipt must never be parsed through v3 field assumptions.

V3 preparation deterministically rebuilds source maps/claims from verified note
bytes, validates additions/citations/counterclaims, and binds snapshot dependency
identities, target before hashes and expected after hashes in the batch hash.
Keep source locks then sorted target locks then the canonical store lock; do not
acquire a new artifact lock after a store lock. Immutable note identities need
no invented editable-page lock. Existing editable target locks/CAS are retained.

Retain the outer operation lock before sorted source locks, then target/child
locks, then the store lock. Snapshot dependencies are artifact guards, not entries
in wiki `expected_before`; mutable target and split-child fingerprints remain
CAS guards. Snapshot identities cannot become wiki upserts or deletion targets.

Freeze mutable source-receipt inputs used by rebuilding too: evidence-by-role,
observed evidence IDs, attribution/reference projections and their input hash.
Take those inputs under source locks and bind them in v3 preparation. New evidence
or role changes before unwitnessed commit invalidate preparation rather than
silently rebuilding different edits. After a matching commit witness, later
receipt changes do not rewrite the frozen committed batch or historical record.

Commit revalidates evidence and rebuilds the complete batch from fresh canonical
rows before atomically storing topic edits and a version-specific non-wiki commit
witness. Preserve prior prose, claims, limitations and competitors; use existing
lossless child splitting. Verify the witness, not receipt stage alone, on replay.
Navigation may reference an editorial source page only if it exists, but that
page is not evidence authorization. Artifact-only references must not enter
`wiki.links` as nonexistent page IDs. Expose provenance as source-note links in
the citation layer with an appropriate local viewer, not blank alias pages.

Publication/log/search effects follow the existing default-off generation policy.
Add explicit v3 support at those boundaries; neither a v1 nor a v2 committed
receipt is upgraded in place. Crash recovery repairs missing effects without
reinvoking a model, spending quota, overwriting canonical edits or activating an
older generation. Recheck current target identity before replay side effects.

Distinguish new-use validation from postcommit recovery. A witnessed commit
remains a historical commit if an original expires or a display page later
changes. Repair only the frozen committed effects; do not rerun source selection,
rebuild a different batch, spend quota or overwrite newer canonical versions.
If required frozen artifacts are unavailable, report effects pending/unavailable
instead of declaring the commit nonexistent or silently certifying fresh use.
Public eligibility for a new operation still checks current evidence policy.

In particular, v3 publication `_binding` is a separate versioned branch: derive
its binding from the frozen v3 batch, frozen source-receipt input projection,
pinned note hashes and matching v3 store witness, not `journal.load_source()` at
effect-repair time. Freeze those public projections before the commit and bind
them in the witnessed batch; no v1/v2 publication sidecar is rewritten. This is
required even when a current source receipt disappears or its routing/evidence
metadata changes. Missing frozen proof leaves historical commit recorded and
effects pending; it never manufactures proof from a newer mutable receipt.

## Bounded Recovery And Pilot

Start with the exact three previously approved public revisions, not all 105
changed target pages. Verify and materialize their source notes in dry-run/apply
phases. Record original/archive/compile/current-editorial hashes before and after;
all existing bytes remain unchanged. This phase invokes no model and consumes
no model reservation.

Preserve the same pilot state and two-attempt total cap across old/new modes.
Explicitly version a new empty state if necessary; do not create another state
filename to reset consumed attempts. A failed/interrupted/refused attempt still
counts under the existing rules. If any attempt already exists, migration must
halt for explicit review rather than starting a fresh pilot.

For an existing empty state, migration is an explicit versioned CAS on the same
pilot state path, retaining its prior exact bytes in an immutable local migration
record. Require attempts empty, topic IDs empty and no baseline; otherwise stop.
Crash/retry reconciles that record and the unchanged authorization identity.
Creating an additional state file is never a second allowance.

Missing, corrupt, unreadable, relocated or ambiguous legacy pilot state is NOT
an empty state and cannot automatically initialize a v3 allowance. First inspect
the canonical configured old path, retained authorization/migration records,
budget reservations and operation/witness history. If prior attempt consumption
cannot be established, halt for explicit recovery review, even when the shared
daily counter is zero. Any genuinely first initialization is a separately
recorded one-time operational decision tied to the original authorization, not
a side effect of a missing file. Retain that guard outside replaceable pilot
state so deleting a state file or changing versions cannot renew the two tries.

With implementation and operational approval, first combine the two advisory
sources into one draft topic; then add the third attributed report to the same
topic. Use at most two shared reservations, within the existing daily 20/KST
limit. Recheck quota at real execution time; no date injection or separate cap.
No additional sources, standing topic automation or trust promotion is implied.
Successful commits report `awaiting_semantic_review`, never factual completion.

Human/model-independent acceptance must compare actual output to all three
originals: attributed reports remain attributed, dates and conflicting evidence
survive, claims do not become unsupported facts, and the follow-up preserves the
earlier synthesis while integrating the new report. Model output alone, a green
counter or a manifest is not that semantic evidence.

## Verification Requirements

TDD must cover deterministic note identity; compiler/anchor/source rejection;
chunk-tail retention; no model calls during materialization; byte-for-byte
preservation of originals and editorial pages; file corruption/symlinks/path
escape; staging/rename/fsync crash recovery and idempotency.

Context tests must prove editorial body/title changes no longer invalidate a
snapshot dependency, while raw/compile/note changes, expiry and public-policy
changes do. Explicitly prove that private editorial additions never enter the
new context and that legacy page-backed guards are not weakened.

V3 integration tests must exercise genuine source-note creation, proposal,
multi-source seed/update, full batch rebuilding, source-role/citation validation,
target CAS, lossless split, witness-backed interrupted commit/replay, effect
recovery, actual search identity and unchanged quotas. Include negative version
confusion, fabricated receipt/witness, simultaneous writers and source expiry
between selection/model/commit. Rerun v1/v2 suites and publication regressions.

Do not activate v3 from unit tests. Final acceptance additionally requires the
bounded real pilot and semantic audit, current collection checks, and unresolved
wiki-generation blockers addressed under their own approved decisions.

## Trade-offs And Rollout Boundary

An immutable derivative layer adds disk usage and a v3 maintenance surface. It
avoids discarding editorial work or trusting mutable notes, and removes repeated
compilation/model expense. Historical artifacts do not imply perpetual source
authorization. Start disabled and public-only; retain old versions and allow a
flag rollback without deleting evidence or pretending a committed topic vanished.

Written-spec approval permits a TDD implementation plan, not implementation or
production activation. The plan must map every versioned consumer and the pilot
state transition before code work begins.

## Design Review Record

Sol reviewed existing context, provider-boundary, journal, transaction, public
eligibility and effect-recovery interfaces. Its versioning, source-receipt input
pinning and error-layer/projection-scope corrections are incorporated. Luna
identified and rereviewed the note-ID cycle, mutable publication binding and
missing-state allowance hazards; its bounded rereview approved the corrections.
Both reviews used code/synthetic fixtures only. No implementation, migration,
production activation or new model-source processing occurred in this design
stage. The user subsequently approved the reviewed implementation plan; operational
authorization is still required as specified above.
