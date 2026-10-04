# Recommendation engine v2 deployment

Deployment date: 2026-10-05 KST. Recommendation feature baseline: `11cf1df`.

## Deployed behavior

- Canonical outcome horizons collapse `20`/`20d` and `60`/`60d` duplicates when reading training data. Invalid price and return metrics are excluded; original records remain intact.
- Incremental SQLite indexing preserves raw source files and records their locations. Historical features respect publication, observation, collection, and labeling availability.
- Common recommendation snapshots record selected and unselected candidates, sample uncertainty, source coverage, evidence references, and quality gaps across entry signals, investment reports, and US/KR mock policies.
- Ranker promotion checks freshness and independent validation evidence. Current operating mode is `shadow`.
- Weekly ranker reports include recommendation index, source, duplicate, and invalid outcome health.
- Refresh runs at minute 07 and 37 of every hour. Existing installed cron entries were preserved.
- Deployment verification uncovered unfinished Kiwoom mock token locking. Token renewal now takes the shared file lock and rechecks disk before requesting a token.
- SQLite WAL allows evidence reads during index updates.

## Verification

| Check | Observed result |
| --- | --- |
| Focused regression suite on operating checkout | 365 passed in 12.41 seconds |
| Initial raw-cache indexing | 58 files, 442,087 ticker-event records, 0 parse errors, 0 pending bytes |
| Live warm metadata lookup | 100 indexed tickers, 20 runs, p95 4.35 ms |
| SQLite journal mode | WAL |
| Dashboard local and public health endpoints | `ok` |
| Telegram bot | Restarted and running; startup analysis completed for 117 stocks |
| Live snapshot smoke test | ORCL and NVDA evidence coverage loaded; ranking mode `shadow` |

Public dashboard: https://plumbing-thought-course-portable.trycloudflare.com/

## Current limitations and findings

- Source health reports SaveTicker and the economic calendar unavailable. Indexing existing cache does not restore these upstream feeds.
- The existing ranker's training end is 2025-04-14. Its OOS IC is approximately -0.0103 and top-decile excess return approximately -0.99%; promotion is rejected.
- Legacy outcome health reports 6,387 canonical duplicate rows and 1,485 invalid metric rows. Read-time normalization and training exclusions handle these records without rewriting the audit history.
- Calibration fitting and diagnostics exist, but per-surface accepted calibration artifact production, rejection against the raw-score OOS baseline, and production application remain incomplete. Generic ranker artifacts lack the scoped surface/horizon/version metadata required for active report reordering.
- News contribution remains zero until independently validated. This deployment does not establish improved investment returns.
- Existing daily entry snapshots are immutable and deduplicated. Previously recorded decisions are not overwritten with post-decision evidence; new decision IDs receive v2 metadata.
- Existing unrelated uncommitted operating changes were preserved. This deployment updated the local Python operating checkout; GitHub remote integration and a Vercel frontend release were not performed.

## Recovery material

- Pre-deployment tracked-change backup: `/tmp/stock-report-predeploy-20261005.patch`.
- Previous installed scheduler: `/tmp/stock-report-crontab-before-20261005.txt`.
- Applied scheduler: `/tmp/stock-report-crontab-after-20261005.txt`.
