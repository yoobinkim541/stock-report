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
- `dashboard/pages/watchlist.py`
- `tests/test_dashboard_pages.py`

## Verification
- Initial red run: `uv run pytest tests/test_notable_investors_wiki.py tests/test_wiki_distillation.py -q`
  - Failed because `reports.institution_watch.HISTORY_PATH`/`run` did not exist yet.
- Final focused run: `uv run pytest tests/test_notable_investors_wiki.py tests/test_wiki_distillation.py tests/test_institution_watch.py -q`
  - Result: `29 passed in 14.11s`
- Regression run: `.venv/bin/pytest tests/test_dashboard_pages.py -q`
  - Result: `70 passed in 17.02s`

## Trade-offs
- The legacy single-filer compatibility wrapper does not write the cross-institution common-pattern digest; list-based `institution_watch.run([...])` does. This preserves the existing cron contract while enabling the new multi-institution path.
- The common-pattern page is a `source_digest` derived from persisted institution snapshot wiki refs and capped at confidence `0.6`; it remains subject to the existing distillation/curation flow and does not promote itself into a judgment card.

## Concerns
- `uv` needs write access to its cache under `/home/ubuntu/.cache/uv`; tests required escalation in this sandbox.

fix round 1:
  addressed:
    - "혼합 출처 기관 공통 패턴 페이지는 source_digest/reviewed 로 올리지 않고, 모든 입력이 source-backed 인 경우에만 source_digest 로 올리도록 조정했다."
    - "Berkshire 단일 cron 경로는 legacy page id/notable-investor-berkshire 를 유지하도록 reports/notable_investors_wiki.run() 을 복구했다."
  tests:
    - ".venv/bin/pytest tests/test_notable_investors_wiki.py tests/test_wiki_distillation.py tests/test_institution_watch.py -q -> 30 passed in 14.06s"
  commit:
    - "3e344ebf2dcc66fd2d278a450f72bab45b5d6810"

fix round 2:
  addressed:
    - "notable_investors_wiki.py 를 thin compatibility wrapper 로 되돌리고, legacy investor page 생성/히스토리 처리는 institution_watch 쪽 helper 로 이동시켰다."
    - "Berkshire cron 과 mixed provenance common-pattern 경계에 대한 regression test 를 유지하면서, old page id/notable-investor-berkshire 계약과 draft/note trust boundary 를 동시에 고정했다."
  tests:
    - ".venv/bin/pytest tests/test_notable_investors_wiki.py tests/test_wiki_distillation.py tests/test_institution_watch.py -q -> 30 passed in 13.93s"
  commit:
    - "619c7fe3fbd4968d7e428d67568290b1702137ff"

fix round 3:
  addressed:
    - "watchlist 페이지의 행 선택 처리에 '이미 선택된 티커면 다시 rerun 하지 않음' 가드를 추가해, 빈 기관 허브 조합에서 AppTest 가 루프에 빠지는 회귀를 막았다."
    - "home/research 페이지와 같은 선택 상태 방어 패턴으로 맞춰서, 실제 클릭 이동 동작은 유지하되 중복 rerun 만 제거했다."
  tests:
    - ".venv/bin/pytest tests/test_dashboard_pages.py -q -> 70 passed in 17.02s"
  commit:
    - "7e5c30f230f1d8def8de95da7d1c0b8ab1f54940"
