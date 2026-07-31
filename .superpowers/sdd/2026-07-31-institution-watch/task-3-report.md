# Task 3 Report: Institution Watch Wiki Persistence

## Summary
- Added `reports.institution_watch.run(...)` as the generic multi-institution persistence runner.
- The runner reuses `HISTORY_PATH` JSONL records to avoid reprocessing unchanged snapshots, writes per-institution snapshot digests, and writes a cross-institution common-pattern digest for multi-institution runs.
- Converted the common-pattern digest to a reviewed `source_digest` with `wiki:institution-watch-*` source refs so the existing distiller can select it without changing `reports/wiki_distillation.py`.
- Kept `reports/notable_investors_wiki.py` compatibility symbols available; its `run(...)` now delegates to `reports.institution_watch.run(...)` while preserving the old single-filer cron behavior of skipping unchanged filings and watchlist auto-add for new tickers.

## Files Changed
- `reports/institution_watch.py`
- `reports/notable_investors_wiki.py`
- `tests/test_institution_watch.py`
- `tests/test_notable_investors_wiki.py`
- `tests/test_wiki_distillation.py`

## Verification
- Initial red run: `uv run pytest tests/test_notable_investors_wiki.py tests/test_wiki_distillation.py -q`
  - Failed because `reports.institution_watch.HISTORY_PATH`/`run` did not exist yet.
- Final focused run: `uv run pytest tests/test_notable_investors_wiki.py tests/test_wiki_distillation.py tests/test_institution_watch.py -q`
  - Result: `29 passed in 14.11s`

## Trade-offs
- The legacy single-filer compatibility wrapper does not write the cross-institution common-pattern digest; list-based `institution_watch.run([...])` does. This preserves the existing cron contract while enabling the new multi-institution path.
- The common-pattern page is a `source_digest` derived from persisted institution snapshot wiki refs and capped at confidence `0.6`; it remains subject to the existing distillation/curation flow and does not promote itself into a judgment card.

## Concerns
- `uv` needs write access to its cache under `/home/ubuntu/.cache/uv`; tests required escalation in this sandbox.
