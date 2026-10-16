# SaveTicker To StockBot Contract Audit

Checked: 2026-10-13 13:58 KST.

## Scope And Runtime Limit

The requested flow is manual authenticated source collection followed by prompt
StockBot delivery of breaking-news titles and complete bodies. This session's
execution sandbox denies sockets, including loopback Chromium CDP on port 9225
(`PermissionError: Operation not permitted`); escalation is unavailable. No fresh
site collection or live Telegram source-body delivery was performed. This is an
offline contract audit and a read-only inspection of local cache metadata.

No protected source text, login credentials or cookie values were printed or
sent to an external model. All executable audit fixtures are synthetic.

## Reproduced Failures

Explicit audit command:

```bash
.venv/bin/python -m pytest docs/audits/saveticker_forwarding_contract.py -q --tb=short
```

Initial result: **six failed, two passed**. These failures were reproduced before
the approved repair. The contracts are now included in the regular test suite.

1. `crons/news_spike_detector.py:_format_alert` omits the complete `body_raw`.
2. The same formatter omits the original article URL.
3. It slices timestamps and labels them KST without timezone conversion. A UTC
   04:00 publication is rendered 04:00 KST rather than 13:00 KST.
4. `main` records `sent_ids` before Telegram send success. A failed first send is
   never retried on the next run because it already appears sent.
5. `fetch_breaking_news` fetches the public provider first and has no authenticated
   cache fallback. When that fetch fails, even an available full-body cached
   breaking event is not read. It also targets `~/reports/source-cache`, not the
   recovery worker's `~/reports/private-sources/cache`.
6. `extract_article` does not preserve a known breaking category from the source
   snapshot/header, so the authenticated ingress cannot reliably retain that flag.

Controls: successful-send deduplication works; ordinary breaking items are
excluded by the existing importance policy. The latter is a product-policy
mismatch with an all-breaking immediate-forward requirement, not an accidental
failure: threshold seven, maximum five sends per run, two-hour publication window.

## Existing Coverage And Local Metadata

The adjacent offline suite passed **115 tests in 6.51 seconds**, including current
importance, realtime-tag, notifier, collector, reauthentication and private remote
access tests. Those passes do not cover the six end-to-end contracts above.

The latest private authenticated cache contains one SaveTicker full-body row,
zero breaking-tag rows and one unknown publication timestamp. The latest legacy
alert-cache file contains zero SaveTicker rows. These are cache counts, not proof
that the current site has no news or that the alert cron is absent.

The deployment template schedules the legacy detector every minute. Reading the
live crontab was denied, so live cron installation/execution is unverified here.
The recovery timer uses a fixed, already authorized article as a login/source
probe; its healthy state does not establish bulk-news or forwarding readiness.

## Repair Direction

Feed the authenticated raw-source queue to a dedicated StockBot forwarder without
external-model egress. Preserve observed breaking status and source/publication
timestamps separately; never invent a publication time from collection time.
Send title, complete body and original URL, with durable per-part delivery receipts
for long Telegram messages. Mark an article sent only after acknowledged delivery;
retry transient failures and distinguish article revisions from duplicate sends.
Keep the current importance filter as a separate explicit mode if retained.

After repairs, rerun the RED contracts as GREEN, then verify a small actual batch
through authorized Chromium, immutable archive and the owner's StockBot. A
network-enabled execution context is required for that live gate.

## Approved Repair And Verification

The alert formatter now includes the complete available source body and original
URL, converts publication timestamps to KST, and explicitly labels unknown times.
The collector preserves supplied categories and exact breaking labels from article
headers. Cached authenticated SaveTicker articles remain eligible even if the
public fetch fails; the public failure is still logged. Unknown publication times
are not invented and remain ineligible for the two-hour breaking window.

Article delivery is recorded only after all parts succeed. Exact frozen message
parts and per-part progress are saved after every success; a retry resumes at the
failed part. Splitting preserves blank lines and respects the limit even for emoji.
There is still an unavoidable duplicate-delivery window if Telegram accepts a
message but the process crashes before recording the acknowledgment. This is not
an exactly-once delivery guarantee. Retries currently require the source to remain
inside the breaking-news eligibility window.

Private Hermes-source events use rules only, without external LLM scoring.
Existing important-only filtering (score >= 7) and the five-article run limit remain
unchanged. This repair does not enable an all-breaking feed or expand the recovery
worker's fixed-article probe into bulk collection.

Verification: **128 tests passed in 6.60 seconds** across forwarding, importance,
realtime, notifier, collector, recovery, authentication and remote-access suites.
`git diff --check` passed. Fixtures contain synthetic content only. No fresh live
collection, Telegram article delivery, deployment, commit or push was performed;
the current execution context does not permit the required network operations.
