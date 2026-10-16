# Approved Topic Pilot Readiness

User approved the limited actual-model pilot, twice with the same answer. This
is one authorization, not four attempts: maximum two reserved attempts total.
No private sources, trust promotion or standing topic automation is approved.

## Read-Only Preflight

Checked 2026-10-16 11:04 KST. The shared public budget for the KST date is 20/20.
No quota file was reset and no reservation or model call was made. The earliest
next KST date begins 2026-10-17 00:00 KST (2026-10-16 15:00 UTC). Availability
must be rechecked then; the existing public runner can also consume those slots.
This document does not schedule a job or promise a reserved slot.

Three public Telegram originals were archive/journal/fingerprint verified:

| Role | Source revision | Claims | Original expiry |
| --- | --- | --- | --- |
| Read-only seed context: US Riyadh airport security advisory | 387f54cb405eb1f475a5a1f486450b29efbc38e1d4755614bd416771b40f63d6 | 5 | 2026-10-25 09:05 KST |
| Seed current source: Canadian airport safety advisory report | 5fdda9590f68d65704ca12f8f0a5e3e343370bb61b600e725c3ec10606d9563e | 5 | 2026-10-25 01:05 KST |
| Follow-up current source: attributed Houthi airport attack claim | eabbe0e3a9384920dd147f1e87e4496c6d56b3b4744b39dafe4c5dd05916c3a9 | 3 | 2026-10-26 06:35 KST |

Each passed `_note` and `_verified_source`: current public committed page
fingerprint, retained raw/text bytes, compilation hash/revision, source scope and
expiry. These are community-carried reports, not independently verified official
advisories or attack facts. The pilot must retain that attribution and caveat.
The legacy Canadian source-note title still has a processing suffix; the pilot
must not rename or rewrite that source-specific note to make the result prettier.

## Exact Pilot Boundary

First reserved attempt: current Canadian report plus only the US source note as
read-only context; ask for a separate draft topic. Second reserved attempt:
current attributed attack report, only the two advisory source notes as context,
and the committed pilot topic as the sole editable target. Do not silently admit
other public notes just because general retrieval would rank them.

Revalidate all evidence before both model exposure and commit. Keep snapshots
of original hashes/source-note fingerprints and compare after each operation.
Use the same shared budget and journal; no separate budget to evade the cap.
Persist attempt IDs and outcomes before external side effects, including failures.
A refused/invalid first response is not a successful seed and must not be hidden
by unlimited retries. Any continuation beyond the two authorized reservations
requires another explicit approval.

Actual acceptance requires retained claims from both sources, dated limitations,
resolvable source citations, and then a same-topic update retaining all three
revisions without erasing old prose/counterevidence. Inspect the prose against
the originals; counters and syntactic validators alone cannot establish it.

At this snapshot the implementation and synthetic tests are verified, but this
actual-model pilot has not started. Whole-goal completion remains unproven.

## Bounded execution harness

`scripts/run_public_topic_pilot.py` defaults to read-only preflight. Apply uses
the existing shared lock and budget, persists at most two attempt intents, and
halts on refusal/failure/interruption. Replays verify reservation, committed
receipt, source revision/role, atomic witness and latest topic fingerprint.
Two committed stages report `awaiting_semantic_review`, never semantic success.
Initial state/completion regressions were observed failing before the fix.
Scoped synthetic suite: 195 passed; latest read-only operational preflight:
2026-10-16 KST, quota 20/20, pilot attempts 0. No live model calls or standing
activation. A next-day slot is not reserved or scheduled.
