# Wiki Generation Acceptance Audit

Date: 2026-10-16

## Legacy-Link Compatibility Follow-Up

The user-approved export-only correction is implemented. Unique exact bare
titles resolve after ID precedence to stable export stems, preserving labels.
Labelled references remain ID-only; publication-file syntax retains its earlier
existing-stem support. Code literals, canonical records and snapshot bytes are
preserved. Unknown/ambiguous references and absent files still block publication.

TDD reproduced missing title conversion, fence boundaries and extensionless
file-reference regressions before their respective fixes. Fresh parent
verification with real-QMD opt-in: **225 passed, exit 0, 19.18 seconds**, report
`/tmp/wiki-legacy-compatibility-final-20261016.xml`. Luna independently passed
56 focused cases and approved the final fence/path corrections. `git diff
--check` passed. This is focused risk-matched evidence; the older whole-suite
result below predates this follow-up and was not rerun for these changes.

No originals were edited, no placeholder documents created, and no generation
activated or deployed. The broader knowledge-compounding objective remains open.

## Latest Verified Result

Final full repository: **4,612 passed, two skipped, 41 warnings, exit 0** in
894.82 seconds. Real-QMD tests were enabled; structured report
`/tmp/wiki-generation-full-verified-v2-20261016.xml` contains all 4,614 collected
cases, zero failures and zero errors. All ten critical production/config hashes
matched the frozen baseline afterward; `git diff --check` passed.

The prior failure was a test-double lexical mismatch: `qmdgeneration` plus an
e-prefixed generation hash contains page ID `one` as a substring. The fake
returned the sentinel as a page hit; production correctly rejected it. A
test-only token-aware fake and forced collision/control cases resolve it without
weakening assertions or changing production guards. Fresh Luna review approved;
related regression passed 971 with one legacy-selector skip.

Full-run skips: opt-in Chromium performance and an inapplicable legacy selector.
Scientific/plotting/deprecation warnings remain. This proves the software gate,
not production activation or the whole objective. The real candidate still has
39 unresolved targets/78 diagnostics; actual model-driven compounding is not yet
piloted. No commit, push, live activation or private-source model processing.

## Scope And Result

This is synthetic integration evidence for the approved wiki-generation design, not production activation or completion of the broader wiki-generation goal. The acceptance test uses a real committed synthetic multi-source topic transaction with retained claims, limitations, and long split-child content. It checks the real Task 3 manifest/pinned-reader/publisher and Task 4 generation search APIs with a deterministic injected QMD runner. It also invokes Task 5 `repair_publication` against the real committed receipt.

The committed topic uses a `source-topic-<64-character digest>` ID, longer than the split-tag value limit. The shared-memory/wiki tag normalization fix is now present in the workspace; the acceptance test preserves the full ID and candidate and passes validation without shortening IDs, filtering records, relaxing validation, or forcing publication. All downstream publication, search, pinning, rollback, and recovery assertions now execute.

## Evidence

- `tests/test_wiki_publication_acceptance.py`: real synthetic source/topic commit; claims and limitations retained; long child tail; 1,600-character context excerpt bound; concurrent canonical write while rendering; and executed downstream assertions for immutable publication, pinned reads, injected isolated search, rollback, and Task 5 recovery. The earlier RED stopped at validation; that historical limitation was resolved by the bounded split-ID fix.
- Initial test-harness REDs (fixture discovery and incomplete injected QMD preflight) were corrected. The short-ID harness workaround was removed; the final long-ID test passes with the actual committed identifiers.
- The long-topic test reads retained source files using the journal's actual `raw_ref`, `text_ref`, and `manifest_ref` schema and asserts that the set is nonempty. It creates a valid reservation in `automatic-budget.json`, asserts nonempty bytes, and checks source hashes and budget bytes after recovery.
- The positive recovery test passes: a prepared v2 topic operation uses enabled Task 5 dispatch and injected QMD; an injected crash occurs after pointer activation; real `repair_publication` completes artifact/search/history effects on the same generation. It verifies real sidecar/descriptor hashes and unchanged canonical events, raw inputs, and quota bytes, with the compiler/model path forbidden. The legacy-done assertion separately verifies no fabricated generation proof.
- Verification: `pytest -q tests/test_wiki_publication_acceptance.py` -> 2 passed. Acceptance plus publication recovery, activation, and QMD-generation search -> 97 passed, 1 skipped.
- Existing real-candidate audit evidence reports 78 broken links. That is a rollout blocker: candidates with these errors must remain unpublished until separately reviewed; this acceptance work does not delete or repair knowledge.

## Readiness Boundary

Implementation evidence: synthetic long-ID validation, Task 3 publication/pinned reads/rollback, Task 4 isolated search, and Task 5 crash recovery/replay all pass. The skip in the adjacent focused suites remains reported by pytest; no full suite was run.

Activation evidence: not established. The existing real candidate still has 78 reported broken links, which blocks rollout pending review; this synthetic acceptance does not certify or repair that store. No production QMD activation or live-store modification was performed, and no private model or network call was made. Production activation requires review of those links, real QMD isolation evidence, and separate approval. This audit makes no claim that the whole goal is complete.

## Final Cross-Contract Fixes

Only the exact flag value `1` enables generation readers and source/topic
dispatchers; unset, empty, `true` and `yes` remain disabled. Both readers and
writers resolve generations under `<wiki_artifacts_dir>/publication-generations`.
Explicit activation/rollback retains immutable `activation-intents/<op-hash>.json`
and `activation-history/<op-hash>.json` in that root, outside generations. Retry
with the same operation ID and generation to repair interrupted history; later
activations repair the prior active intent before replacing its pointer. An
already completed or superseded retry does not reactivate the older pointer.
These records do not write to the global wiki log or attest source/topic receipts.
The separate history directory leaves existing `history.jsonl` bytes untouched;
no existing test contract or receipt/schema/budget definition was changed.

Final fix verification: the initial integration run reproduced all three findings
(7 failed, 4 passed, including flag variants). After fixes and crash-boundary
coverage, publication/activation/recovery/search/acceptance tests passed 126 with
one opt-in real-QMD skip. The broader fresh run of publication, QMD-generation,
source/topic, QMD compatibility, public-budget and agent-console tests passed
745 with that same single skip in 65.31 seconds; `git diff --check` passed.
An intervening broader run was interrupted after an added maintenance-doc
paragraph exceeded the existing schema size limit; that paragraph was fully
removed and these operational notes moved here before the fresh GREEN run.
The final full suite result is recorded below; it is not all green.

Trade-offs: durable rollback history adds local files and directory flushes, and
activation scans retained intents. Failure to repair active history blocks a
later pointer replacement rather than losing its activation evidence. Aborted
pre-pointer intents remain retained without being presented as completed events.
Separate operation IDs are required for intentional later reactivation.

## Durable Full-Repository Result

Command: `env AGENT_CONSOLE_TEST_REAL_QMD=1 .venv/bin/python -m pytest -q
--tb=short --junitxml=/tmp/wiki-generation-full-final-20261016.xml`.
Exit 1: 4,610 passed, one failed, two skipped, 41 warnings in 851.55 seconds.
JUnit proves all 4,613 collected tests executed, zero collection errors and one
failure: `test_actual_adapter_ready_and_search_replay` expected search done but
received failed. Ten critical production/config file hashes matched the frozen
baseline after execution. This was a genuine failed run, not an all-green or
pre-existing-failure claim; the later successful run above supersedes it.

The skips are the explicitly opt-in Chromium canvas performance check and a
legacy-selector test inapplicable to the current baseline. Real QMD generation
tests were enabled. A focused run before the full run passed 59 tests; that does
not override that full-run failure. A fresh Sol worker reproduced the exact
collision, fixed the test double and preserved all assertions/activation policy.

Residual P2 ruling: the compatibility alias must recover alongside rollback
history after a crash immediately following `current.json` replacement. History
repair now atomically replaces and directory-flushes the alias only when the
entire current pointer matches the intent, including generation and operation.
It does not reactivate the pointer. Later-publication guards cover both another
generation and the same generation under another operation ID. Trade-off: repair
of a matching active intent adds an alias replacement/fsync and can fail before
history completion; retry remains idempotent. Exact repro: 1 failed, 2 guard
cases passed before the fix; focused integration/activation/recovery/acceptance/
generation-search verification afterward: 129 passed, 1 opt-in real-QMD skip in
13.00 seconds. `git diff --check` passed. Full-suite rerun remains with the parent.
