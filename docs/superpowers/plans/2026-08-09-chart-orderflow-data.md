# Chart Order-Flow Data Packet 4 Plan

**Goal:** Preserve KIS realtime trade and order-book events with an explicit provenance contract, then expose only evidence-backed order-flow views in the chart workbench.

**Architecture:** `kis_stream` remains the only writer. A bounded recorder preserves provider trade size (with cumulative-volume delta fallback), samples book updates, and appends versioned date/symbol-partitioned JSONL. A pure chart adapter computes coverage, depth imbalance, spread, and volume-at-price. The analysis rail renders these values and visibly blocks footprint/delta when aggressor-side data is absent.

## Constraints

- Never infer bid/ask or aggressor-side volume from OHLCV.
- Receive time and exchange time are distinct; missing exchange time stays null.
- KR depth is up to ten levels; US free realtime depth is one level.
- Capture is opt-in and bounded by a daily byte budget.
- A cumulative-volume reset creates an anomaly/baseline event, never negative volume.
- Existing realtime quote and intraday bar paths must remain failure-isolated.

## Tasks

- [x] Add failing tests for event normalization, cumulative-volume deltas, book sampling, byte limits, and coverage.
- [x] Implement `providers/orderflow_store.py` and wire its recorder into `kis_stream.py`.
- [x] Add failing tests for depth/volume-profile calculations and unavailable aggressor-side capabilities.
- [x] Implement `dashboard/chart_orderflow.py` and add an always-visible order-flow tab to the analysis rail.
- [x] Wire ticker and workspace analysis snapshots to the local order-flow reader.
- [ ] Run focused and full chart regression tests, document evidence, commit, and push.

## Trade-Offs

- JSONL is transparent and append-friendly but less efficient than Parquet for long historical scans. Packet 4 keeps bounded local capture and leaves a columnar compaction seam.
- Book sampling controls storage growth but cannot reproduce every queue transition.
- Volume-at-price is valid from trade-price volume deltas; footprint bid/ask delta remains unavailable without an authoritative side field.
