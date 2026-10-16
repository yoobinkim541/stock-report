# Wiki Knowledge Preservation Review

## Scope

This continuation follows the original LLM wiki objective, not merely the recent
StockBot repair. The preceding goal turn made concrete progress by repairing
forwarding and verifying synthetic contracts. The broad goal remains active.

No production source bodies, credentials, private cache text or external model
calls were used. A bounded gpt-6.1-sol read-only review used synthetic records.

## Reproduced Gaps And Repairs

1. Existing parent/child limitations and contradiction relations were omitted
   from the model's integration prompt. The prompt now includes only the known
   knowledge fields, retaining its exclusion of private conversation metadata.
2. Repeated readability splitting treated the generated parent overview as the
   complete source, archiving the real child body. It now reconstructs the body
   for generated overviews; a deliberately replaced parent body retains the old
   archival behavior. No stored raw source is modified.
3. Replacing children during integration lost child-only caveats and relations.
   Semantic snapshots now collect those fields across active children, and batch
   preparation preserves the aggregate. Historical children remain archived,
   rather than being deleted.
4. The Markdown mirror omitted compiled claim metadata and limitations. It now
   exports claims, limitations, relations and integration provenance losslessly
   in a structured Source Knowledge section. Unknown metadata is not exported.

The failures were demonstrated with offline synthetic regression fixtures before
their fixes; reviewer reproductions additionally established the child metadata
loss. No tests use fabricated live collection or deployment success.

## Verification

The source compiler/revision/journal/integration/atomic/pipeline/acceptance/recovery,
Markdown, maintenance and qmd suites passed: **177 tests in 32.39 seconds**.
`git diff --check` passed. This is scoped regression evidence, not a new full-suite
or production certification. The reviewer was closed after reporting findings;
the final patches were verified locally, not independently re-reviewed.

## Remaining Gates

- Sources exceeding the approved 20,000-character budget remain pending chunking;
  they are not clipped or counted as compiled. Resumable source chunking requires
  the separate design/approval contemplated by the implementation plan.
- Raw retention/deletion policy, production model activation and private-source
  external-model consent remain separate gates. No policy or activation changed.
- Live authenticated bulk collection, article forwarding, runtime deployment and
  production wiki inspection are unverified in this network-restricted context.
- This turn performed no live cleanup, commit, push or deployment. Existing
  unrelated changes in the dirty worktree were retained.

Therefore the full requested LLM wiki end state is not yet proven complete.
