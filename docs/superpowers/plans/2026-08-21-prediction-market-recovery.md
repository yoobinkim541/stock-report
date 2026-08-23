# Prediction Market Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore read-only Polymarket collection through an allowed-region relay and add Kalshi as an independent prediction-market source.

**Architecture:** A fixed-upstream Flask relay returns official Gamma event payloads from a configured region. The local collector keeps normalization and tries direct Polymarket first, then the configured relay only for HTTP 451; Kalshi is normalized independently under the shared prediction-market event contract.

**Tech Stack:** Python 3.11, Flask, `requests`, Vercel Python Functions, pytest.

**Spec:** `docs/superpowers/specs/2026-08-21-resilient-source-pipeline-design.md`

## Global Constraints

- Read-only public market data only; no wallet, position, order, or trading code.
- No arbitrary upstream URL, generic proxy behavior, CAPTCHA bypass, or geo-spoofing.
- Relay authentication uses `POLYMARKET_RELAY_TOKEN`; secrets are not committed.
- Polymarket and Kalshi probabilities remain separate evidence rows.
- A failed relay must produce explicit blocked/error health, never stale cached context.

---

### Task 1: Fixed-Upstream Relay Contract

**Files:**
- Create: `deploy/polymarket_relay/api/index.py`
- Create: `deploy/polymarket_relay/requirements.txt`
- Create: `deploy/polymarket_relay/vercel.json`
- Create: `tests/test_polymarket_relay.py`

**Interfaces:**
- Consumes: `Authorization: Bearer <POLYMARKET_RELAY_TOKEN>` and allowlisted query values `limit`, `order`, `ascending`
- Produces: Flask `app`; `GET /api/events -> {ok, source_url, retrieved_at, transport, events}`

- [ ] **Step 1: Write the failing relay tests**

Test unauthorized requests, clamped limits, fixed Gamma URL, successful payload envelopes, and upstream 451 envelopes by injecting a fake HTTP getter into `fetch_events(limit=200, get=fake_get)`.

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest tests/test_polymarket_relay.py -q`

Expected: FAIL because `deploy.polymarket_relay.api.index` does not exist.

- [ ] **Step 3: Implement the minimal relay**

Create `fetch_events(*, limit: int, get=requests.get) -> tuple[dict, int]` with the fixed URL `https://gamma-api.polymarket.com/events`, a 15-second timeout, payload-size validation, and explicit 451 handling. Add the authenticated Flask route and a public `/health` route that does not call upstream.

- [ ] **Step 4: Add standalone Vercel configuration**

Configure only `api/index.py`, Python runtime requirements, `dub1`, and a rewrite from `/api/events` to the function. Do not modify the main application's region.

- [ ] **Step 5: Run the tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_polymarket_relay.py -q`

Expected: PASS.

### Task 2: Relay-Aware Polymarket Fetcher

**Files:**
- Modify: `reports/source_collector.py`
- Modify: `.env.example`
- Modify: `tests/test_source_collector.py`

**Interfaces:**
- Consumes: `POLYMARKET_RELAY_URL`, `POLYMARKET_RELAY_TOKEN`, official Gamma response or relay envelope
- Produces: `fetch_polymarket_payload(*, request_limit: int, get=requests.get) -> tuple[list[dict], dict]`; preserves `fetch_polymarket_events(limit: int | None = None, *, min_volume: float | None = None, keywords: list[str] | None = None) -> list[dict]`

- [ ] **Step 1: Write failing fallback tests**

Cover direct success without relay, direct 451 followed by relay success, relay 451 preserving `availability=blocked`, invalid relay payload rejection, and non-451 direct failure without fallback retry.

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest tests/test_source_collector.py -q -k 'polymarket and relay'`

Expected: FAIL because the relay transport contract does not exist.

- [ ] **Step 3: Implement transport selection**

Extract payload retrieval from normalization. Record `transport`, `retrieved_at`, and upstream URL on every normalized row. Keep `_SOURCE_AVAILABILITY` and `_LAST_ERRORS` accurate across direct and relay paths.

- [ ] **Step 4: Document environment variables**

Add empty, non-secret examples for the relay URL/token and explain that the relay is fixed-upstream and read-only.

- [ ] **Step 5: Run the tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_source_collector.py -q -k polymarket`

Expected: PASS.

### Task 3: Independent Kalshi Provider

**Files:**
- Create: `reports/prediction_markets.py`
- Modify: `reports/source_collector.py`
- Modify: `tests/test_source_collector.py`
- Create: `tests/test_prediction_markets.py`

**Interfaces:**
- Produces: `fetch_kalshi_events(limit: int = 80, *, get=requests.get, keywords: list[str] | None = None) -> list[dict]`
- Produces: `prediction_entity_id(event: dict) -> str`
- Consumes: `https://external-api.kalshi.com/trade-api/v2/markets?status=open&mve_filter=exclude`

- [ ] **Step 1: Write failing Kalshi normalization tests**

Use representative API rows to assert bid/ask midpoint probability, last-price fallback, dollar-field parsing, market ticker identity, finance-topic inclusion, sports-only exclusion, and pagination capped by configured page count.

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest tests/test_prediction_markets.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement Kalshi normalization**

Emit `source=kalshi`, `type=prediction_market`, `record_kind=observation`, stable `entity_id`, `observed_at`, metrics, source URL, and the prediction-market caveat. Never map a Kalshi market to a Polymarket market id.

- [ ] **Step 4: Register Kalshi health and collection**

Add `kalshi` to expected sources, stale thresholds, and the prediction provider group while preserving existing Polymarket behavior.

- [ ] **Step 5: Run the tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_prediction_markets.py tests/test_source_collector.py -q -k 'kalshi or polymarket'`

Expected: PASS.

### Task 4: Context Separation And Live Relay Probe

**Files:**
- Modify: `agent_console/context.py`
- Modify: `tests/test_agent_console.py`
- Create: `scripts/probe_prediction_sources.py`

**Interfaces:**
- Produces: `prediction_market_state(events: list[dict] | None = None, limit: int = 8)["providers"]` keyed by `polymarket` and `kalshi`
- Produces: probe JSON with per-provider `ok`, `count`, `freshest_observed_at`, `availability`, and `error`

- [ ] **Step 1: Write failing context tests**

Assert that provider rows remain separate, stale/blocked Polymarket rows are excluded, fresh Kalshi remains available, and no cross-provider average is computed.

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv/bin/pytest tests/test_agent_console.py -q -k prediction_market`

Expected: FAIL on the new provider contract.

- [ ] **Step 3: Implement provider-aware context and probe script**

The probe calls the same production fetchers and exits nonzero only when every prediction provider is unavailable. Its JSON must be suitable for health-check ingestion.

- [ ] **Step 4: Run tests and local live probe**

Run: `.venv/bin/pytest tests/test_agent_console.py -q -k prediction_market`

Run: `.venv/bin/python scripts/probe_prediction_sources.py`

Expected: tests PASS; Kalshi count is positive; Polymarket is either direct/relay healthy or explicitly blocked.

### Task 5: Deploy And Verify The Relay

**Files:**
- Modify: `docs/data-collection-pipeline.md`

**Interfaces:**
- Produces: deployed relay URL and environment configuration outside git
- Consumes: Vercel CLI project scoped to `deploy/polymarket_relay`

- [ ] **Step 1: Deploy the standalone relay to Vercel `dub1`**

Set `POLYMARKET_RELAY_TOKEN` in that project, deploy production, and record only the non-secret URL locally.

- [ ] **Step 2: Configure the local collector secret and URL**

Update the server environment without committing secrets and restart only the affected scheduled job if needed.

- [ ] **Step 3: Verify the live path**

Run the production probe twice at least one observation bucket apart. Confirm relay transport, positive row count, and distinct stored observations.

- [ ] **Step 4: Apply the fallback rule if `dub1` returns 451**

Deploy the identical fixed-upstream contract to AWS Lambda `eu-west-1`. If that also returns 451, leave Polymarket blocked and keep Kalshi healthy; do not add an unapproved proxy.

- [ ] **Step 5: Document the verified transport**

Record provider, region, probe time, and result in `docs/data-collection-pipeline.md`, excluding tokens.
