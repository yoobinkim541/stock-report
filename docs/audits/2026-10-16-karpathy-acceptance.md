# Karpathy LLM-Wiki Acceptance Gate

`reports/wiki_karpathy_audit.py` checks the live store against the Karpathy
LLM-wiki pattern (raw / wiki / schema layers; ingest, query, lint; index.md and
log.md). Each condition is a pure function tested synthetically in
`tests/test_karpathy_llm_wiki.py`; the live gate runs with
`WIKI_LIVE_ACCEPTANCE=1`. CLI: `.venv/bin/python -m reports.wiki_karpathy_audit [--json]`.
Passing is structural evidence, not a certificate that every claim is true.

| ID | Condition | Threshold |
| --- | --- | --- |
| C1 | Raw originals cited by wiki claims are unchanged | every cited revision raw/text SHA-256 matches its receipt |
| C2 | Knowledge, not links | 0 catalog/template/repeated/link-dominant wiki pages; every page source-backed |
| C3 | Source summaries | every compiled source has a `compiled_source` summary page |
| C4 | Entity/topic pages | >= 30 claim-bearing topic pages linked to source summaries |
| C5 | Compounding | >= 20 pages with claims from >= 2 source revisions; contradictions dated, cross-source |
| C6 | index.md | every active page once; >= 2 category headings; summary, date, `sources: N` |
| C7 | log.md | append-only; headers `## [YYYY-MM-DD] op \| title`; ingest entry per compiled source |
| C8 | Schema | exported schema byte-identical to `config/wiki-maintainer.md`, covers all operations |
| C9 | Query file-back | >= 1 query log entry; >= 1 cited `query_fileback` page |
| C10 | Lint | 0 broken links (archived targets count as broken), 0 orphans, lint logged in 24 h |
| C11 | Long pages | body <= 6,000 chars; split parents link every child |
| C12 | Cross-references | every page has >= 1 outbound and >= 1 inbound link |
| C13 | Frontmatter | markdown export has `id, kind, status, tags, updated, source_count` |
| C14 | Freshness | ingest in 24 h; every enabled collection source healthy |

Raw-layer catalogs (`kind: source_index` or tag `raw_index`) are outside the wiki
layer: they may exist for evidence lookup but are not wiki articles.

## Baseline (2026-10-16 13:00 UTC, before changes)

3 of 14 pass (C1, C3, C8). 3,145 active wiki pages; 2,561 mechanical
(template markers 2,561, repeated lines 48). Topic pages 8; multi-source pages 0.
index.md lacks category headings/dates/source counts; all 59,990 log headers use
the old `## [ISO timestamp] action` form and no compiled source has an
`ingest |` entry. No query log entry or filed-back page. 315 pages with broken
links, 188 orphans, 214 without outbound links. Seven pages exceed 6,000 chars.
No markdown export carries `source_count`. Collection: `saveticker` and
`economic_calendar` HTTP providers blocked by 403. Full JSON:
`~/reports/karpathy-audit/2026-10-16-baseline.json`.
