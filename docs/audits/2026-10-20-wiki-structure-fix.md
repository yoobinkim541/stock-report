# Wiki Structural-Tag Inheritance Fix

The user asked to inspect current changes and continue after the bounded design
was presented. HEAD was `a119ca1`; initial untracked diagnosis/spec files and the
unrelated `_tmp_stale_list.py` were preserved. Work resumed with this narrow code
correction, not an operational rewrite of the eight existing relationship errors.

## RED And Scoped GREEN

New `tests/test_wiki_structure_inheritance.py` uses actual isolated temporary
wiki storage and publication validation. Before production edits: five tests
failed for the expected source-child adoption, inherited ancestry and truncated
reserved-ID behaviors (`/tmp/wiki-structure-red.xml`).

The change in `agent_console/wiki.py` filters structural split tags only from
inherited source tags, retains the target's own relationships, uses the existing
reserved-ID-aware tag normalizer for target/archive records, and assigns manual
split children their direct parent rather than copied ancestry/child ownership.

Scoped verification: 11 passed in 1.14 seconds, combining new regressions,
reserved-ID and merge-overflow suites (`/tmp/wiki-structure-green.xml`). Luna
independently reviewed the diff, found no correctness issue and ran five new
tests successfully. It noted target ancestry lacked an explicit regression
assertion; a separate in-memory check verified an already-parented merge target
keeps both its parent field and `split_from` tag. This latter check is not a
persisted CI regression.

The original disposable reproduction now reports zero relationship errors for
both merge and nested split, compared with its earlier mismatch/conflicting-
parent errors. No real documents, original files or source receipts were used.

## Full-Suite Verification

Terminal exit zero: **4,783 passed, three skipped, 41 warnings, 852.63 seconds**.
JUnit contains all 4,786 collected cases with zero failures/errors. Both frozen
file hashes below match afterward and `git diff --check` passed. Skips are the
explicit opt-in live Karpathy store gate, opt-in real Chromium performance gate,
and a legacy-selector test inapplicable to the current baseline. Plotting/ML/
deprecation warnings remain. Software regression is verified; live-source
compounding, production graph integrity and the whole goal are not certified.

Command: `env AGENT_CONSOLE_TEST_REAL_QMD=1 .venv/bin/python -m pytest -q
--tb=short --junitxml=/tmp/wiki-structure-full-20261020.xml`.
Session 84903, actual PID 3838362 confirmed by expected pytest/report arguments.
The process remained live and CPU-active during a quiet section; it was not
restarted because observation produced no output. Startup environment had no
preconfigured production memory/DB/report overrides, allowing the repository's
temporary-store isolation to apply. Final terminal result is required before
claiming whole-suite success.

Frozen file hashes:
- `agent_console/wiki.py`: `faf03a07ca3107b11a94aa900f1be4f0bc184019dbb3b50d6bfd7630d9186a82`
- `tests/test_wiki_structure_inheritance.py`: `fbcb27290091dc27129c03ed13abfee9e490eaf2325163e726550ecd6e5cf583`

## Architecture Plan

Sol drafted `docs/superpowers/plans/2026-10-20-immutable-public-source-notes.md`.
Parent self-review added hash-covered frozen note assets and full-suite gates;
Luna's review led to explicit pinned-generation reads, strict authorization/state
schemas and attempt-history/locking contracts. Luna approved the corrections and
retracted a filename finding after verifying the cited test file exists.
The user approved the nine-task plan and Luna/Sol execution after review. Its
implementation starts after this full run; operational actions remain gated.

No Git commit/push, deployment, service restart, live data migration, human
security confirmation, real source-model call or quota reset was performed.
Original whole-goal completion remains unproven.
