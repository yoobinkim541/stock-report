# Live Wiki Summary/Log Gap Diagnosis

## Scope

Read-only diagnosis of the six revisions reported missing by the 2026-10-22
`wiki_karpathy_audit` run (132 compiled, 126 summaries). Receipt inspection was
limited to metadata and routing counts. Page inspection compared revision IDs in
claim/source-reference fields and statuses; no titles, bodies, prompts, compiled
text, or claim text were emitted. No external model, network, live data write,
test, or implementation change was performed.

## Findings

All six IDs have source receipts under
`~/reports/public-wiki-pilot-20261013/sources/`. Every receipt reports
`compile_state=compiled`, an empty `error_code`, one observed evidence ID, and
the `support` role. Compilation success is not evidence of a completed summary
publication: the audit counts a source summary only when an active non-raw page
tagged `compiled_source` has a non-empty claim citing that revision
(`reports/wiki_karpathy_audit.py:158-172`).

| Revision | Support routing metadata | Matching operation receipt | Current page claim/source-ref match |
| --- | --- | --- | --- |
| `240da5a0787b5400f2411013509f67d7bad073e0c17e8335ef6ac51e94c54502` | `integrated=false`, cursor 0, 0 examined, 0 selected, 3 new-page specs | None | None |
| `66166b21bfc60db945bdc35e1103c48bc5c4920782026a3f4795edacabe7e033` | `integrated=false`, cursor 0, 0 examined, 0 selected, 1 new-page spec | None | None |
| `794bcbd894e439aff03b6685969fc19b01689e90ba9782f8243cdbab649075dc` | `integrated=false`, cursor 0, 0 examined, 0 selected, 2 new-page specs | None | None |
| `aa54cdb5f0d05c10582008156fa9ac845014335c459d695551ee888e0fdd2e0d` | No support routing entry | None | None |
| `ac040ce7cef418676d9e56f7476fb9a4518fdffaef590d245a70f13d02f260bf` | `integrated=false`, cursor 0, 0 examined, 0 selected, 1 new-page spec | None | None |
| `eb7bca07f8b3c2aeaa89875b082e3e8e37a34560b480e72b0346db6a88b6c539` | `integrated=false`, cursor 0, 0 examined, 0 selected, 1 new-page spec | None | None |

Only one journal root with source receipts was found. Its `operations`,
`topic-operations`, and `topic-operations-v3` records contain no operation
matching these revisions. Across current pages, including archived pages, there
are no claim records or source references matching the six IDs. Thus there is
no positive evidence of a prepared/committed transaction, a log-effect failure,
or a subsequently archived/removed page with retained claims. This absence
cannot prove that historical data was never removed outside the retained stores.

## Diagnosis

The best-supported classification is **publication did not reach the durable
operation boundary**, rather than compilation failure, transactional effect
failure, or a demonstrated archive/lost-claims event. Five receipts preserve
unintegrated routing state with proposed new-page specs but no selected/examined
targets; the sixth preserves no routing state. The code persists routing before
building and preparing the operation (`reports/wiki_source_pipeline.py:400-407`;
`reports/wiki_source_journal.py:173-199`). The batch builder is also where the
compiled-source summary target is added (`reports/wiki_source_pipeline.py:282-324`).
Since no operation exists, the operation recovery/effect path cannot account
for these revisions or their missing log entries (`reports/wiki_source_pipeline.py:185-228`).

This narrows the gap to **not routed / pre-prepare integration incomplete** but
does not identify the exact interrupted call or exception. In particular, the
five persisted `new_page_specs` show routing did some work; they do not show
that `_build_batch` succeeded. The worker catches integration exceptions and
retains only an aggregate `integration_failed` counter (`reports/wiki_source_pipeline.py:400-412`),
so the receipts do not preserve that historical cause. The missing log entries
are consistent with no prepared operation, not proof that the log writer failed.

## Next Safe Repair

Before any live replay, add a **synthetic, temporary-journal TDD reproduction**
at the worker boundary:

1. Seed a synthetic compiled source and a routing result with zero selected
   existing targets plus one new-page spec. Use a fake model and temporary
   `Journal`; assert the resulting batch includes the compiled-source summary
   and its revision claim.
2. Inject a one-shot failure after `save_routing` but before `prepare`. Assert
   the first run creates no operation and makes no page/log mutation, while the
   saved source remains pending for integration.
3. Rerun with the same synthetic input and fake model. Assert exactly one
   operation reaches committed state, summary/page artifacts and the ingest log
   effect complete, and a further repeat creates no duplicate operation or
   model call.
4. Add a second failure injection after operation preparation (for example,
   during an effect) to prove recovery completes the existing operation rather
   than creating another one.

This tests both sides of the prepare boundary and idempotent recovery without
using real source text or touching live receipts/pages. If it exposes a
diagnostic or retry defect, make that narrowly scoped code change and rerun the
synthetic test before proposing any separately authorized live recovery. Do not
infer a compiler failure, rewrite receipts, fabricate summaries/log events, or
replay the six live revisions as part of this diagnosis.
