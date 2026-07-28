# Polymarket Source Integration Design

## Goal

Add read-only Polymarket data as a prediction-market signal for the source cache, Evidence Wiki, and AI console context.

## Scope

- Collect public, unauthenticated Polymarket market/event data only.
- Store rows in the existing `reports/source_collector.py` JSONL source cache with `source=polymarket`.
- Preserve raw payload fields needed for later EvidenceCard/Wiki use.
- Classify Polymarket separately from news and community posts as `prediction_market`.
- Include Polymarket in source health so silent collection gaps are visible.
- Expose a compact prediction-market summary in the AI console context.

## Non-Goals

- No trading, order placement, wallet auth, or private user data.
- No hard dependency on the CLOB SDK.
- No claim that implied probabilities are facts; they are crowd-priced risk signals.

## Data Shape

Each collected row should include:

- `source`, `source_url`, `title`, `url`, `published_at`
- `body_raw`, `body`, `body_excerpt`
- `tags`, `markets`, `tickers`
- `metrics` with probability, volume, liquidity, open interest, and close time
- `raw_payload`
- existing `classification` fields from `_classify_event`

## Data Flow

`Polymarket Gamma API -> fetch_polymarket_events -> append_events -> source-cache -> context_pack -> prediction_markets -> AI prompt/Evidence Wiki`

The first implementation uses Gamma API event/market payloads because it gives discovery, titles, tags, prices, liquidity, and event URLs without auth. CLOB price history can be added later behind the same normalized row contract.

## Error Handling

Polymarket collection is isolated like other sources. A failure records `_LAST_ERRORS["polymarket"]`, updates health as zero rows, and does not stop other source fetchers.

## Tests

- Parse Gamma events into normalized rows with implied probabilities.
- Filter out closed or low-signal markets.
- Classify Polymarket as prediction-market evidence.
- Include Polymarket in `collect_once`, expected sources, stale health, digest, and AI console context.
