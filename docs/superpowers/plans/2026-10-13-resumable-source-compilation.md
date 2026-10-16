# Resumable Source Compilation

Approved by the user: compile sources above 20,000 characters without clipping,
persist exact offsets and per-part processing, consolidate knowledge, and validate
with synthetic data. Keep raw storage and model activation/privacy policies intact.

## Contracts

- `build_revision` / `discover_revisions` keep their old default size gate. An
  explicit `allow_chunking=True` permits a verified complete oversized revision.
- New `reports/wiki_source_chunks.py` uses exact contiguous character ranges at
  most 20,000 characters; their concatenation reproduces the immutable body.
- Chunk identity derives from protocol, parent revision, span and slice hash.
  Existing Journal receipts provide hash-checked compilation reuse in a separate
  child journal. Parent source locks continue to serialize pipeline runs.
- At most four new chunk model calls per source pass. Budget exhaustion means
  pending, not compile failure or completion. Failed chunks retry; accepted chunks
  are reused. No partial source integration into the wiki.
- Aggregation retains every accepted claim and limitation, rebases exact anchors
  to original source offsets, assigns stable parent-scoped claim IDs and remaps
  intra-chunk competing references. It does not invent cross-chunk relations.
- Synthesized text remains source-derived drafts; per-span sections are assembled
  into one compilation. No extra whole-source model call or unsupported claim of
  exhaustive semantic coverage. Skip reasons remain in chunk receipts and output.
- The worker uses this path only for verified oversized sources. Activation stays
  default off. Existing 80,000-character integration budget still applies; oversized
  integration stays pending rather than being clipped.

## TDD Tasks

1. Add failing discovery opt-in and chunk identity/coverage tests, implement the
   explicit discovery gate and deterministic spans.
2. Add failing crash/retry/budget/cache-corruption and global anchor/claim tests,
   implement chunk journaling and deterministic aggregation.
3. Add failing worker pending/resume tests, wire the worker and independent counts.
4. Run synthetic compiler/revision/journal/pipeline/acceptance regressions, then
   bounded Sol review. Preserve unrelated dirty edits; no network/private inputs,
   deployment or git writes in the current restricted execution context.
