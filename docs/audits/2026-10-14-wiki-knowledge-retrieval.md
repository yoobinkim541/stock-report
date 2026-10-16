# Knowledge Retrieval Coverage

## Evidence

Fallback ranking considered title, summary, tags and conversation fields, but neither the stored body nor source_knowledge. Four RED regressions demonstrated that prose-only and structured-only evidence lost to an unrelated first record for a unique content query.

## Repair

Fallback search candidates now include the complete document view body and known scalar fields from claims (text/scope/date), limitations and relation scope explanations. Secret masking remains applied to known knowledge text. Arbitrary metadata and nested private objects are excluded rather than stringified. Document-content scoring is enabled only for retrieval, not automatic mutation/link scoring. Stored objects, raw sources, score bonus weights and QMD behavior are unchanged.

## Verification

Initial combined retrieval/context/agent regression suite: 206 passed. Sol review identified shared automatic-curation scoring and preexisting filter leakage. Three RED tests reproduced automatic score change, status fallback leakage and missing surface exclusion. The final retrieval/context/agent/performance suite passes 212 tests; focused final search cases pass eight. Explicit unmatched status/surface filters now return no unrelated pages. Diff whitespace validation passed. This establishes deterministic local retrieval coverage, not semantic ranking quality or live answer correctness. Existing query-time excerpt budget remains unchanged and missing factual verification is not promoted by a lexical hit.
