# Topic accumulation recovery — 2026-10-16

## Implementation
- Source-context selection and target routing share Korean/English financial alias matching. Equity aliases reuse `ticker_names` (offline); macro equivalents are candidate-discovery hints, never evidence of entailment.
- New `topic_synthesis` identities depend on normalized title, surface and kind, not the triggering source revision. Existing topics remain preferred routing candidates; distinct supported entity/mechanism topics may share an evidence set.
- Topic integration retains historical prose, dated attributed claims, limitations and relations. Cross-revision `compete` relations derive symmetric `competing_claim_ids`; subsequent revisions preserve this enrichment without confusing it with immutable source claim identity.
- Topic transactions link to contributing compiled-source summaries. Reverse navigation uses the existing derived `summary.backlinks`, preserving read-only summary records and their receipt fingerprints.
- Topic context uses the shared `source_allowed` policy. Default public/private authorization is owned by the shared helper, not duplicated here.
- Topic ingest logging supplies meaningful topic titles. Existing single-source ingest title handling is unchanged.

## Verification boundary
Focused regression tests are included for bilingual routing/context, revision-independent identities, summary links/backlinks, contradiction preservation and later appends. No live-store mutation or bulk backfill was performed by this slice. C4/C5 live thresholds require the parent's authorized compilation/backfill and final audit; this code does not manufacture pages to meet counts.

## Exercised focused checks
- `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_wiki_source_integration.py tests/test_wiki_topic_context.py tests/test_wiki_topic_integration.py tests/test_wiki_topic_transaction.py tests/test_wiki_topic_acceptance.py tests/test_wiki_topic_pipeline.py tests/test_wiki_topic_public_targets.py tests/test_wiki_topic_readback.py tests/test_wiki_topic_prompt_boundary.py` — 183 passed, 1 failed in 12.39s. The failure was the old exact ingest-metadata key assertion excluding the now-required title.
- Updated that assertion to require the actual topic title. `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_wiki_topic_transaction.py::test_ingest_log_lists_contributors_not_unused_context` — 1 passed in 0.89s.
- The broad focused selection was not repeated; the main agent owns the final combined audit.
