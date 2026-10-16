# SaveTicker Fixed Probe Diagnosis

Read-only runtime diagnosis at approximately 2026-10-16 02:22 UTC.
No article collection, credential reads in output, login attempts or model calls.

## Evidence

- Recovery state changed from earlier human-waiting state to `phase=error`.
- Collector state: `parse_failed`, latest recorded collection attempt
  `2026-10-16T02:08:35.902280+00:00`. This is not an authentication failure code.
- Authoritative systemd ExecStart still uses `--limit 1 --url
  https://saveticker.com/news/news_235492`. Service exits zero even when the
  state reports an error; process exit alone does not prove collection health.
- Read-only browser structural probes on the existing collector task reported
  SaveTicker hostname and an article route, zero articles/headings/body
  paragraphs, one modal, body text length 95 and a not-found marker. No password
  input, exact login button, loading marker or challenge element was detected.
  These probes returned flags/counts only, not article text, cookies or tokens.
- Browser-tool process shutdown printed an atexit cleanup warning after the
  successful structural response; it is separate from the collector parse code.

## Interpretation And Boundary

The fixed historical probe currently sees an unavailable-article screen rather
than an extractable original. HTTP status was not measured, so this is not a
verified HTTP 404 claim. Absence of visible login/challenge markers does not
certify authentication or successful latest-list access.

Do not weaken extraction validation to accept the error screen as an article.
Requested replacing the stale fixed probe with a bounded latest-list one-article
check, retaining private local-only storage and no external LLM transmission.
That deployment change remains pending user confirmation; no service restart,
configuration change or human-confirmation flag mutation was performed.

## Approved Recovery Applied

User subsequently approved latest-list one-article probing. Added deployment
regression: initially one failure for the historical URL; removed only `--url`
and its value from the unit, preserving `--limit 1`. Recovery/collector/deployment
regressions: 77 passed in 6.68 seconds; scoped diff whitespace check passed.

Installed the approved unit, reloaded systemd and verified authoritative
ExecStart ends with `--limit 1` and no fixed URL. Initial service invocation
honored the ordinary interval and did not collect. A single manual `tick` with
interval zero then used the same locking, human-confirmation and private runtime
boundaries: localhost webhook returned HTTP 200, tick returned
`phase=healthy, collection_status=delivered`. Collector state records
`last_success=2026-10-16T02:26:57.489229+00:00` (11:26:57 KST), delivered and
zero errors; recovery state has no remaining reason. No external model calls,
public-source queue activation, credentials output or synthetic confirmation.

The browser tool emitted its separate atexit cleanup warning after successful
delivery; that warning remains uncorrected. This proves one current article's
collection/delivery, not sustained uptime, all categories, StockBot news
forwarding or whole-goal completion. Later human-login challenges still require
the existing explicit confirmation flow.

## Post-Delivery Storage Verification

One event exists in the private 2026-10-16 cache. Its original HTML, extracted
text and manifest all exist inside the private root. Original-byte SHA-256
matches both event and manifest; derived text is nonempty. Event coverage is
`full`, extraction scope `authorized_article`. This verifies retained original
identity, not independent factual accuracy or a semantic full-coverage audit.

Additional synthetic runtime/bot-recovery/forwarding/webhook tests: 52 passed in
2.16 seconds with approved local HTTP listener permission. No real Telegram
article forwarding was performed. A whole-history public-cache leakage scan was
interrupted after discovering the cache is 16GB; no full-history isolation claim
is made from that unfinished scan. A bounded current-day streaming check is
recorded separately after its completion.

Completed current-day streaming scan: 4,811 public cache events checked; zero
matches to the private article event ID or raw path. This is limited to the
2026-10-16 cache snapshot, not proof that every downstream store is isolated.
