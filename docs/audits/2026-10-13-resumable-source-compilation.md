# Resumable Source Compilation Verification

The user approved resumable compilation of sources exceeding 20,000 characters,
using synthetic data while preserving existing raw/model activation policies.

## Implemented

- Exact contiguous source spans, each <= 20,000 characters, with deterministic
  parent/span/hash identity and immutable original source references.
- Per-chunk source receipts, accepted-output capsules and hash-checked compiled
  artifacts. A capsule is persisted before final journal bookkeeping; interruption
  during subsequent bookkeeping reuses that exact output without re-inference.
- At most four new chunk calls per source pass. Cache hits do not consume calls;
  exhaustion leaves the parent pending and never integrates partial knowledge.
- Global anchor offsets, parent-scoped claim identities, remapped competing IDs,
  retained limitations/terms, explicit skipped-span reasons and source-span prose.
- Source receipts are resumed after events leave the recent collection window,
  including incomplete role routing. Archived raw/text/manifest are reverified
  before resume. Missing/expired/changed original data is unresolved, not silently
  reconstructed or sent to the model.
- Existing direct discovery defaults retain the size gate. The worker explicitly
  enables the approved chunk path. Model activation remains default off.

## Evidence

RED fixtures reproduced missing chunk APIs, missing discovery opt-in, premature
whole-source compilation, invalid parent identity, recent-window abandonment and
the result-persistence/bookkeeping crash window before fixes.

A bounded gpt-6.1-sol read-only reviewer independently reproduced the last two
issues; both were repaired and tested locally. The reviewer was closed. No claim
of an independent final-patch review is made.

Fresh scoped regression: **204 passed in 33.66 seconds**, covering source chunks,
compiler, revision, journal, integration, atomic updates, pipeline, acceptance,
recovery, Markdown, maintenance, qmd and StockBot forwarding. `git diff --check`
passed. The archive-to-compiled-child fixture proves original raw/text byte
preservation, global anchors, final-source-tail retention and redelivery reuse.

All inputs/model outputs were synthetic or prerecorded public synthetic fixtures.
No live private source text or credentials were accessed or exported. No actual
provider calls, deployment, git writes or production wiki edits occurred.

## Remaining Limits

- Per-span assembly is source-derived draft knowledge, not exhaustive semantic
  certification. No cross-span relationship is fabricated. The later integration
  model can synthesize the accepted knowledge within its existing budget.
- The existing 80,000-character integration budget is unchanged. A compilation
  exceeding integration capacity remains routing-pending; it is never clipped.
- A model request interrupted before any durable result exists can be retried;
  no impossible exactly-once remote-inference guarantee is claimed.
- This is scoped software verification, not the full repository suite or a live
  production certification. Production collection/model gates, retention policy,
  cleanup and the overall LLM wiki completion audit remain open.
