# Structured Knowledge In Chat Context

## Defect

Persistent source claims, limitations and relations survived storage/export but were omitted from build_context_section. Three failing tests reproduced missing counterevidence, structured-only page omission and the absent bounded excerpt helper.

## Repair

Prose and structured evidence share the existing 1,600-character excerpt budget. Fixed per-field allocations prevent limitations/relations from crowding out all claim metadata. Known string fields only are projected: claim identity/text/date/scope/state/source revision, relation identity endpoints/date/scope, limitations and integration role. Unknown nested values and empty projections are excluded. Stored data is not mutated; no model call or raw-source access occurred.

The excerpt labels selected versus available items and explicitly states it is not full review. Field text may be clipped, preserving the user's prior query-time truncation policy. Document reads and Markdown remain separate full-content paths. Missing relation endpoint bodies are not considered reviewed.

## Review And TDD

Sol review found claim starvation, nested-value disclosure and missing claim identities. Three added RED regressions reproduced these findings before the allocation, scalar-validation and identity fixes. Prose-only excerpts retain the original 1,600-character contract. Larger malformed/redaction/nonmutation checks are included.

This repair enables the chat input to see stored knowledge; it is not evidence of live answer correctness or whole-goal completion. Operational quota and consent boundaries remain unchanged.

Final excerpt/Markdown/agent regressions: 206 passed. Contract/schema regressions: eight passed. A missing-state RED regression led to explicit unknown defaults for absent claim date/scope/state. One attempted combined command referenced a nonexistent test filename and ran no tests; the corrected commands above supply the actual evidence.
