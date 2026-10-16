# Live Wiki Integrity Recheck

Read-only inspection at 2026-10-16 02:16 UTC (11:16 KST). No model calls,
quota reservations, collection requests, wiki edits or artifact regeneration.

## Evidence

- 4,026 wiki records: 2,818 draft, 324 reviewed, 884 archived. Status was
  derived through the actual wiki record projection, not a guessed tag filter.
- 35 compiled source receipts passed archive-aware source identity, retained
  raw/text and compiled-artifact verification; zero verification errors.
- Five source receipts currently report failed compilation. These counts are
  source states, not daily attempt counts or independent factual validation.
- 39 public target IDs match committed legacy operation fingerprints in a
  supplied store snapshot. All 39 project as draft.
- Zero topic-operation receipts: actual multi-source seed/follow-up remains
  unperformed. No claim that model-driven compounding is operationally proved.
- Across all wiki records (including archives), 43 records hold structured
  claims, totaling 210 claim entries; each record names one source revision.
  These are not 210 unique facts and not 43 verified current public targets.
- No stored wiki body exceeded 12,000 characters in the inspected snapshot.

## Consistency Limitation

The full shared-memory store digest differed before and after the multi-second
archive inspection. No cause was established; this is not proof of raw-source
corruption. Snapshot-local counts and checks above do not establish an atomic
whole-store publication. Hidden-generation/atomic-publication design remains
pending approval; no live artifact rewriting was performed.

### Follow-up Drift Diagnosis

A second archive-verification interval reproduced one changed wiki record:
`source-ticker-spy`. Changed fields were `body`, `evidence_ids`, `updatedAt`;
there were no additions/deletions. Its tags identify a Yahoo Finance ticker
source digest. This drift includes content, not merely usage counters. The
public compiled originals still passed verification.

`reports/source_wiki_curator.py` constructs the same deterministic
`source-{group_type}-{slug}` IDs and replaces event-derived digest payloads
through `wiki.upsert_page`. This is a compatible write path, not proof of which
process caused the observed update; no writer PID was established.
`wiki._publish_artifacts` reads a snapshot and writes four artifact files
sequentially with `write_text`; its publication lock does not make all store
writers and readers participate in one generation. This is a consistency
limitation, not evidence of raw-source corruption. A generation-publication
decision was requested; no architecture approval or migration is assumed.

## Next Gate

The approved two-attempt pilot is prepared but today's shared budget is 20/20.
After a genuine KST-day rollover, recheck the authoritative budget and source
validity before attempting it. Do not reset the budget or promise a reserved
slot. Even two committed operations require a separate prose/source semantic
review before pilot success may be claimed. Overall objective remains open.
