# Wiki Body Read Integrity

## Evidence

Read-only live inventory: 4,006 wiki records, body limit 12,000 characters, zero over-limit stored bodies and maximum body length 11,999. This does not prove historical losslessness; it establishes the current inventory only.

A synthetic legacy unsplit record exceeding that limit lost its trailing counterevidence in `_record_to_page`, before Markdown export. RED reproduced the truncation. Document reads now retain the entire stored body, with existing secret redaction intact. Storage auto-splitting limits remain unchanged, as does the approved 1,600-character chatbot context excerpt.

Sol review identified redaction expansion: `token=x` becomes longer after masking, so an original-length cap still clipped the tail. A second RED regression reproduced that case. `_clean` now accepts an explicit unlimited mode for document reading only; existing callers retain their default/explicit bounds and redaction behavior.

## Boundaries

No live records, originals, or model prompts were modified by this repair. It cannot restore text already lost before persistence. Current live inventory has no over-limit body requiring this repair to be applied through a migration. Separate operational digest drift from the prior publication remains unexplained and is not resolved by this fix.

## Verification

Initial related suite: 208 passed. Excerpt/redaction/roundtrip follow-up: 17 passed. Final expanded suite including short-secret expansion: 211 passed. Whole repository and actual browser rendering are not claimed.
