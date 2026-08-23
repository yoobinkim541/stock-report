# Resilient Source Pipeline And Knowledge Utilization Design

## Goal

Restore lawful, read-only prediction-market collection after the local Polymarket HTTP 451, remove silent data-loss and serial bottlenecks from the source pipeline, add an independent prediction-market source, and prove that collected data reaches the wiki and AI console as fresh, attributable evidence.

## Confirmed Findings

The design starts from observed production behavior on 2026-08-21:

- Direct Polymarket Gamma, Data, CLOB, and geoblock endpoints return HTTP 451 from the current server.
- Polymarket documents market data as public and unauthenticated, but geographic restrictions still apply. The system must not disguise user traffic, enable trading, or bypass a blocked jurisdiction.
- Kalshi's public REST market-data endpoint is reachable from the current server without authentication.
- `reports/source_collector.py` runs seven providers serially. A recent live run took about 47 seconds, including roughly 18 seconds for World Government Bonds and 10 seconds each for Yahoo and FRED.
- `event_id()` hashes only the URL. Mutable Yahoo, FRED, government-bond, and prediction-market snapshots therefore retain only the first sample per URL/day instead of a point-in-time series.
- Yahoo fetches one year of history separately for every configured ticker every 30 minutes.
- Source health counts fetched rows, not persisted rows, so it reports success even when URL deduplication writes zero observations.
- The repository crontab specifies source-wiki `--limit 0`, but the installed server crontab still uses `--limit 8`.
- Shared memory contains 54 wiki records; 37 have never been used. The curator computes about 90 pages but the installed cron saves only eight.
- QMD markdown files have not changed since 2026-08-05, while shared-memory wiki pages changed on 2026-08-21. QMD health checks installation and file count, not index freshness or query success.
- The hourly news-label cron repeatedly logs an LLM failure and silently writes heuristic labels. Stored rows do not expose which path generated each label.

## Architecture

### 1. Prediction-market recovery

Use two independent providers without blending them into a synthetic probability:

1. A dedicated read-only Polymarket relay deployed in an allowed infrastructure region. The first target is a standalone Vercel Python function in `dub1`; if its live probe still returns 451, deploy the same WSGI contract to AWS Lambda `eu-west-1`.
2. A direct Kalshi public REST collector on the current server.

The relay:

- calls only official public market-data endpoints,
- has no wallet, order, position, or trading code,
- requires a dedicated bearer token to prevent public abuse,
- returns source URL, retrieval time, status, and unmodified event payloads,
- never accepts an arbitrary destination URL,
- returns an explicit `blocked` response when the upstream returns 451.

The local Polymarket provider tries the official endpoint first. It uses the configured relay only after an HTTP 451 or when direct collection is explicitly disabled. It records `transport=direct|relay`, upstream URL, and observation time. A relay is not treated as a generic proxy.

Kalshi rows use `source=kalshi`, `type=prediction_market`, stable market ticker, bid/ask/last price, volume, open interest, close time, and an explicit caveat that prices are market-implied probabilities. Only finance, economics, policy, technology, energy, and geopolitical topics enter the stock-research cache; sports and entertainment rows are excluded.

### 2. Provider registry and independent schedules

Move orchestration out of the 1,600-line collector into a small provider registry. Each provider declares:

- canonical source names,
- group (`news`, `market`, `macro`, `prediction`),
- timeout and retry count,
- freshness threshold,
- whether rows are immutable content or mutable observations.

The CLI accepts `--group` and `--source`. Installed cron runs groups independently with `flock`:

- news every 30 minutes,
- market snapshots every 30 minutes on a separate minute,
- prediction markets every 30 minutes on a separate minute,
- macro data every six hours,
- economic calendar every hour.

Providers inside a group execute concurrently with bounded worker count. One source failure updates only that source and cannot delay or mark unrelated providers as failed.

### 3. Content identity versus observation identity

The cache keeps its JSONL compatibility, but identity becomes type-aware:

- immutable content: `content_id = hash(source + canonical URL or source item id)`, reused as event id;
- mutable observation: `entity_id = hash(source + instrument/series/market id)` and `event_id = hash(entity_id + observation bucket)`;
- every row stores `observed_at`, `entity_id`, and `record_kind=content|observation`;
- a 30-minute collection produces at most one observation per entity per 30-minute bucket;
- `load_recent_events()` preserves distinct observation buckets and still deduplicates immutable content.

Appending is protected by the existing cross-process file lock because source groups can overlap. Health records attempted, fetched, persisted, duration, transport, availability, and last error separately.

### 4. Fetch efficiency and cadence

- Replace per-ticker Yahoo history calls with one batch `yf.download()` request and retain the existing per-ticker fallback only for missing columns.
- Do not fetch one year of data to calculate every 30-minute snapshot when a shorter window supplies 1D/5D/1M metrics. Keep 1Y only in a lower-frequency enrichment job or request the minimum adequate period once in batch.
- Run FRED and government-bond snapshots at macro cadence instead of every 30 minutes.
- Use bounded retries only for transient statuses and timeouts. HTTP 401/403/451 is non-retryable for that run and opens a provider circuit for a configurable cooldown.
- Persist a run manifest per provider so health can distinguish `fetched > 0, persisted = 0` from a true success.

### 5. Additional pipelines

Add two useful paths rather than duplicate existing collectors:

- Kalshi as an independent prediction-market provider.
- The existing economic-calendar provider as normalized `economic_calendar` events with scheduled collection and AI context exposure.

DART disclosures and KR microstructure already have dedicated providers/jobs. This change adds adapters that expose their existing fresh records to the common context/evidence layer; it does not issue duplicate network requests.

### 6. Knowledge conversion and retrieval

- Apply the repository crontab as the operational source of truth and add a drift check comparing installed relevant lines with `deploy/crontab.stock-report`.
- Export QMD markdown immediately after wiki batch upsert and run the configured QMD index update command.
- QMD health must execute a probe query and compare newest shared-memory wiki update time with newest exported markdown time. Installation alone is not healthy.
- Source-wiki curation uses all eligible groups and records candidate, saved, skipped, failed, and duration counts.
- Delta-aware curation refreshes a page when evidence changes instead of rebuilding broad topic pages from the same 48-hour corpus.
- AI context excludes blocked or stale provider rows, includes source and observation time, and exposes provider disagreement rather than averaging it away.
- News labels store `label_method=llm|heuristic`, provider/model, failure reason, and labeled time. Health reports the fallback ratio.

### 7. Utilization metrics

The pipeline health report must answer the following for every stage:

- collection: attempted, fetched, persisted, freshness, latency, blocked/error state;
- knowledge: candidate pages, saved pages, QMD export/index freshness;
- retrieval: query count, hit count, fallback count, pages never used;
- answer use: evidence ids included in context and cited in the final answer where available;
- quality: LLM-versus-heuristic label ratio and wiki helpful/neutral/not-helpful feedback.

A source is not healthy merely because an HTTP call returned rows. The end-to-end status is degraded when fresh data is not persisted, indexed, retrieved, or used.

## Security And Legal Constraints

- Read-only public market data only.
- No geolocation spoofing, residential proxy rotation, CAPTCHA bypass, arbitrary proxy URL, trading endpoint, user position, or wallet handling.
- The relay has one fixed upstream allowlist and a dedicated secret.
- Secrets stay in local/Vercel environment configuration and are never committed.
- If both `dub1` and `eu-west-1` are blocked by the upstream, Polymarket remains explicitly unavailable and Kalshi continues independently.

## Rollout

1. Ship identity, health, and provider contracts behind compatibility-preserving functions.
2. Add Kalshi and relay-aware Polymarket collection; verify live provider output separately.
3. Split cron groups and apply the checked-in crontab.
4. Backfill only observation metadata for new runs; do not fabricate missing historical samples.
5. Repair QMD synchronization and label provenance.
6. Run an end-to-end probe that traces a unique collected item through cache, wiki/QMD, and AI context.

## Success Criteria

- Polymarket returns fresh read-only rows through a verified allowed-region relay, or is explicitly marked blocked after both approved relay targets fail.
- Kalshi independently supplies fresh prediction-market rows from the current server.
- Mutable sources store multiple point-in-time observations for the same entity across collection buckets.
- A slow or failed source no longer prevents unrelated groups from finishing and updating their own health.
- Yahoo collection uses a batch path; macro sources no longer run every 30 minutes.
- Installed source/wiki cron lines match the repository source of truth.
- QMD export/index timestamps follow shared-memory wiki updates and a real probe query succeeds.
- News label rows disclose LLM versus heuristic provenance.
- Pipeline health reports fetched, persisted, indexed, retrieved, and used counts, and detects a deliberately broken stage.
- Focused tests and the existing source, wiki, health, and agent-console regression suites pass.

