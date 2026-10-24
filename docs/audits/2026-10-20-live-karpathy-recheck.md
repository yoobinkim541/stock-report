# Live Karpathy Gate Recheck

Read-only metric-only run of `wiki_karpathy_audit.run_audit(live_inputs())`, checked
at UTC 2026-10-20 04:04:57 (KST 13:04:57). No source prose/IDs, private titles or
evidence payloads were printed; no log-baseline write or repair flag was used.

The full software suite passed 4,783 tests but skipped its opt-in live store
test. This explicit live audit is **not passing**, and cannot be hidden behind
that software result.

| Gate | Result | Observed metric |
| --- | --- | --- |
| C1 raw integrity | Pass | 91 cited revisions hash-verified |
| C2 knowledge vs links | Pass | 293 active wiki pages; 0 mechanical / 0 unsourced |
| C3 source summaries | Fail | 94 compiled revisions / 91 with summaries |
| C4 entity/topic pages | Pass | 61 qualifying linked topic pages |
| C5 compounding structure | Pass | 29 multi-source pages / 6 contradiction flags |
| C6 index | Pass | 293 expected / 293 listed / 8 category headings |
| C7 log | Fail | 60,674 entries; 0 malformed headers; 3 unlogged ingests |
| C8 schema | Pass | Export byte-identical; required sections present |
| C9 query/fileback structure | Pass | 31 query entries / 1 cited fileback page |
| C10 lint | Fail | 16 broken-link pages; 0 orphans; recent lint present |
| C11 long pages/children | Fail | 4 pages over audit limit6,000; 8 unlinked children |
| C12 cross refs | Pass | 293 pages; 0 without outbound or inbound links |
| C13 frontmatter | Pass | 0 missing files / 0 malformed frontmatter |
| C14 collection freshness | Fail | Recent ingest; 2 unhealthy among 11 enabled sources |

This auditor's compounding/source-summary/fileback counters prove structure,
not model entailment, receipt-backed public eligibility or a fresh same-topic
seed/update semantic review. Raw checks compare retained raw/text hashes; they
do not independently certify every claim or compiler receipt. The auditor reads
several stores separately, so this operational snapshot is not an atomic global
publication proof. The generation validator's separate graph errors remain
relevant, including archived documents not counted by active-page gates.

Do not lower the 6,000-character audit threshold, invent summaries/log events,
rewrite receipts, delete knowledge or activate publication to make this report
green. Missing summaries/logs need source-operation recovery evidence, and the
live data graph/long-page decisions need provenance-preserving handling.
Approved nine-task immutable-source-note implementation is underway; this report
does not expand its operational permissions or mark the original goal complete.
