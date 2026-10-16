# Wiki Index Integrity

## Scope

Index links now target exported document IDs instead of titles. Display labels normalize reserved wikilink delimiters and whitespace without modifying stored titles or bodies. Export and index use the same ID normalization, including the existing 100-character limit.

Markdown export rejects filename collisions before writing the batch. Existing files must contain matching YAML frontmatter identity before replacement. A same-ID title/body update remains allowed; different IDs cannot silently overwrite each other in sequential exports.

## Evidence

Regression tests reproduced ambiguous title links, truncated-ID mismatch, malformed display labels, batch collisions, and sequential overwrite. A Sol reviewer identified the sequential overwrite gap after the initial repair; it was reproduced with a failing test and fixed. Test directory overrides now use the actual exporter setting.

Initial related suite: 220 passed; final index/export suite: 222 passed. No live source body, stored title, or production artifact was changed by this scoped test run.

## Split Document Follow-Up

Parent overview bodies still generated title-only child links. Two failing regressions reproduced ambiguous titles, malformed labels, and invented unidentified targets. Parent overviews and index entries now share the same ID-based link renderer. Related regression suite: 218 passed; final split roundtrip, acceptance, and markdown tests: 19 passed. Real synthetic split snapshots verify every child ID appears in the parent body while preserving claims and provenance. Production data migration is not included.

Sol compatibility review found the explicit-reference parser treated aliases incorrectly. A failing regression showed spaced labels were ignored and single-word labels became part of the ID. The parser now extracts only the target while retaining existing deduplication and bounds.

Luna implemented Streamlit body display and navigation with failing regression evidence. Known ID references and unique legacy titles display readable labels; verified referenced pages get deduplicated navigation buttons using the existing selection state. Unknown or ambiguous references and inline/fenced code remain unchanged. Stored body text is not changed. Lightweight client conversion and actual rendered browser verification remain pending.

## Operational Recheck

Read-only recheck: public wiki timer active/waiting, last oneshot service success with zero attempts due to daily capacity. Public pending sources: 229; KST 2026-10-14 reservations: 20/20. The 20 automatic attempt records are terminal: compiled/data committed 14, compile failed 6, routing pending/effects pending/stale base 0. Failure records do not identify detailed causes; do not infer them. No quota bypass or new model calls were made.

SaveTicker recovery timer active, service success, state healthy with last success 2026-10-14 11:03:35 KST. This checks a fixed article and does not establish complete latest-news ingestion.

## Live Artifact Publication

Authorized local-only regeneration exported 4,006 Markdown pages and rebuilt index/schema/lint/log. All 4,006 index targets have corresponding files; no missing target was found. External QMD was disabled, no model call or pruning was performed.

The combined operational assertion exited 1: the pre/post full wiki-record digest differed. Therefore content immutability across this live interval is NOT proven. The invoked export/rebuild path does not write source records, but concurrent activity is only a hypothesis, not an established cause. A subsequent read-only two-snapshot check found 4,006 records both times, zero additions/removals/changed records. This does not retroactively prove the failed interval. Synthetic artifact/acceptance recheck: 18 passed. No production-wide completion claim follows from file existence or the later stable snapshot.

## Remaining Work

This is not overall LLM wiki completion. Legacy long titles, live artifact publication and vault-relative link resolution, cross-source semantic synthesis, and broader collection/consent gates require separate verification. The current change does not claim concurrent exporter serialization across uncoordinated callers.
