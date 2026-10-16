# Wiki Generation Publication Design

Status: user approved on 2026-10-16; implementation plan awaits review.

## Intent

The requested wiki accumulates attributed knowledge from immutable originals.
Readers must not see an index from one snapshot and notes from another while
collection continues. Preserve all existing knowledge, child pages, stable page
IDs, original references, append-only history and the 1,600-character chat limit.
This addresses publication consistency, not a substitute for real model synthesis.

## Current Evidence

The live store changed a source digest's body/evidence during read-only auditing.
Artifact rebuilding and QMD export each capture a different snapshot. File-level
replacement is not a multi-file transaction. Chat and the dashboard read canonical
records; QMD supplies ranking, then hits map back to canonical records.
See `docs/audits/2026-10-16-wiki-publication-consumers.md` for consumers and tests.

## Alternatives

1. Atomic per-file replacement only: cheaper, but cannot prevent mixed versions.
2. One global lock across collection, export and every read: intrusive; external
   Markdown readers cannot reliably participate and slow indexing stalls writes.
3. Immutable generations plus pinned reads: recommended. More disk and explicit
   search activation bookkeeping, but collection need not stop during rendering.

## Snapshot And Filesystem Publication

Under the existing store lock, read and detach one complete canonical snapshot.
Release the lock before rendering. Hash the normalized semantic snapshot using
the same fields used for rendering; exclude usage counters only if they are not
rendered. Persist generation identity and the exact snapshot together.

Build a hidden staging directory under the publication filesystem. Generate the
notes, index, questions, lint and schema from that snapshot only; lint generation
must not append a live log as a rendering side effect. Preserve stable ID-based
note names and lossless split children. Fail on collisions or missing targets.
Write a manifest of relative filenames and byte hashes plus snapshot/schema
identity. Validate every file, internal link and split-child relationship before
activation. Reject unsafe paths and symlinks escaping the generation root.

Flush files and staging-directory metadata, rename into immutable `generations/`
on the same filesystem, then atomically replace the active manifest/pointer.
Never fall back to copy-and-delete activation across filesystems. A crash before
pointer replacement leaves the old version active; after replacement, all files
for the new version already exist. Initial migration retains all legacy files.

## Reads And Compatibility

An internal multi-file reader resolves the active generation once and pins that
directory for the operation. Re-resolving a mutable symlink on each file read is
not a coherent read. Missing/corrupt manifests fail explicitly, without assembling
a mixture of legacy and generated files.

Keep documented stable entry paths through compatibility aliases, but label
uncoordinated external multi-file reads as weaker than pinned API reads. Do not
claim arbitrary Obsidian/filesystem readers become transaction-aware. Page IDs
and relative wiki links remain stable inside each generation.

Canonical dashboard/chat APIs remain canonical initially. Each returned content
snapshot identifies its revision; generation search hits must either match that
revision or be explicitly treated as stale ranking hints, never cited as proof
that current content came from the indexed snapshot.

## QMD Boundary

Filesystem activation and external search-index activation are distinct states.
Do not mark search done solely because Markdown publication succeeded. The
implementation plan must first verify installed QMD collection/index isolation
and result-reference behavior using a synthetic temporary collection, not assume
unverified CLI flags or use a live global `qmd update` as a transaction.

Use an isolated generation-scoped QMD collection/index if that capability is
verified. Switch a search descriptor only after indexing and smoke-query checks;
pin that descriptor per query. If isolation cannot be established, leave the new
search activation disabled and report `search_pending`; do not claim overall
generation coherence or production readiness. Canonical search fallback must
remain available and must not alter drafts or promote evidence.

## Recovery And History

Bind source/topic effect receipts to generation identity; on restart verify the
manifest and activation state before marking an effect done. Repair publication
and indexing without rerunning source compilation or consuming another model
reservation. Preserve the existing commit witnesses and target fingerprint checks.

Keep the append-only operation log and immutable revision history outside the
replaceable generation. Append an idempotent publication event after activation;
repair a missing event on recovery. Rolling back selects an intact prior
generation and appends a rollback event; it never rewrites history or originals.
Automatic generation pruning is excluded from this change.

## Verification And Rollout

TDD must cover crashes at write/flush/rename/pointer/history/index boundaries;
concurrent writer during render; reader pinned across activation; corruption,
unsafe paths and collisions; rollback; source/topic effect recovery; unchanged
raw hashes; long-child completeness; stale QMD hits and unchanged citation limits.
Use synthetic data first, then a read-only comparison against real canonical
pages. Production activation is a separate step after tests and review, with
old artifacts retained. No private article model processing is authorized here.

The real multi-source pilot and ongoing collection/semantic quality gates remain
separate. A successful publication migration does not complete the original goal.
