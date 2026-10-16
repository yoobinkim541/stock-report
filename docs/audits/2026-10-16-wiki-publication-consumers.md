# Wiki Publication Consumer Audit

Read-only code audit; architecture decision remains pending. No implementation,
runtime migration, model invocation or operational writes. Requested Luna
subagent audited consumers; parent inspected publication and recovery ordering.

## Current Boundaries

| Boundary | Current behavior | Required design consideration |
| --- | --- | --- |
| `wiki._publish_artifacts` | Snapshot then four sequential `write_text` calls | Publish documents and index from one frozen snapshot; failure must retain old complete version |
| `wiki.sync_qmd` | Acquires publication lock but takes a separate snapshot | Sharing a lock is not sharing a snapshot/generation |
| Single-source and topic effects | Artifact rebuild then separate search sync | Recovery receipts must identify which version each effect completed |
| `qmd_search.export_pages` | Stable page-ID filenames, atomic per-file replace | Per-file atomicity does not make the directory atomic |
| `qmd_search.sync_pages` | Prunes obsolete Markdown then runs QMD update | Preserve prior complete export/index on failure; no unsafe pruning of retained versions |
| `wiki.list_pages` | QMD ranks IDs, current canonical records supply content | Explicitly define stale-hit/current-content semantics; a pointer alone cannot prove answer consistency |
| Dashboard wiki browser | Canonical record APIs, not artifact Markdown reads | Preserve API, health and child-navigation contracts |
| QMD health | Flat filenames/mtime against current records | Understand active generation without misreporting stale/healthy state |
| Append-only log and revisions | Separate durable history | Keep append-only audit history independent of replaceable snapshot; no lost entries on rollback |

## Compatibility Surface

`AGENT_CONSOLE_WIKI_ARTIFACTS_DIR` and `AGENT_CONSOLE_QMD_WIKI_DIR` currently
select different directories. Stable page-ID filenames preserve title-collision
behavior and cross-links. No configured native Obsidian integration was found;
manually opened vaults cannot be ruled out. Do not silently remove stable entry
paths or claim all external consumers have been enumerated.

Relevant regression files: `tests/test_qmd_search.py`,
`tests/test_wiki_markdown_integrity.py`, `tests/test_agent_console.py`,
`tests/test_wiki_maintenance.py`, source/topic transaction recovery tests.

## Acceptance Requirements For A Future Plan

1. Freeze one canonical semantic snapshot; generate all notes/index/schema from it.
2. Crash before activation leaves the old generation complete and readable.
3. Pin a generation for a multi-file read rather than resolving a mutable pointer
   independently for each file. Test a switch between two reads.
4. Define QMD index activation and stale-result handling explicitly; filesystem
   activation must not imply a completed external search update.
5. Bind recovery effects to generation identity and preserve append-only history.
6. Retain stable entry paths, source attribution, lossless children and the
   existing 1,600-character chatbot excerpt contract.
7. Rollback is non-destructive; no original data or history is pruned as cleanup.

These requirements are design inputs, not verified implemented guarantees.
