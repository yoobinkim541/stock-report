# Conflict Integration Disposition

## Evidence And Repair

A conflict-role addition without a claim relationship retained counterevidence but stored no explicit unresolved state. RED reproduced the missing integration metadata. Source batch preparation now records relation_state=unresolved with a non-adjudication note, or relation_state=mapped when a genuine source-to-claim edge is present. Mapped is not resolved and not factual verification.

Sol review found that a matching revision alone could falsely recognize malformed historical edges, and a 140-character context allocation dropped the whole integration for normal 64-character source IDs. RED regressions reproduced both defects. Mapping now requires known claim endpoints, matching endpoint revisions/dates and recognized relationship fields. The context uses a compact role/revision/state projection with sufficient bounded allocation; the complete note remains in storage/Markdown and the read view. Total prose-plus-knowledge excerpt remains at most 1,600 characters.

## Coverage

Tests cover unresolved state, prior prose/draft-claim preservation, conflict evidence IDs, dangling and malformed historical edges, unresolved-to-mapped updates, valid mapping retention on empty-relations retries, context with a 64-character source ID, and read-view state/reason display. No original, production record or model consent policy was changed. Historical records are not silently rewritten.

Read-only production inventory found 23 support-role integration entries and zero conflict entries lacking disposition. No backfill is required for that snapshot. A further RED regression showed older support entries crowded out a later unresolved entry; bounded context now prioritizes unresolved conflicts, then other conflicts, before support entries (newer ties first). Complete integration history remains stored, while the context still explicitly labels partial selection.

The prior full repository run (4,259 passes) predates this change and is not claimed as final evidence for it. Final integration/pipeline/context/view/Markdown/agent regression suite: 278 passed in 33.66 seconds. Diff whitespace validation passed. This closes explicit disposition for source-batch updates, not semantic truth adjudication or claim-level answer support.
