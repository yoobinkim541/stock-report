# Chart Replay And Paper Terminal Implementation Plan

**Goal:** Deliver Packet 2: look-ahead-safe replay, persistent simulated accounts, chart orders, brackets, partial exits, and strategy-result handoff on the Packet 1 chart contract.

**Architecture:** A pure replay broker consumes bars only through a monotonic cursor. Orders, fills, positions, cash, margin, and risk levels are renderer-neutral data. SQLite persistence stores session snapshots and append-only events. Streamlit and Plotly render that state but do not own account math.

## Global Constraints

- A cursor may advance or explicitly rewind into a branched session; no evaluator can read bars after the cursor.
- Same-bar stop/target collisions use a documented conservative policy, never the favorable outcome by default.
- Market orders fill at the next eligible bar open; limit/stop orders use only subsequent OHLC ranges.
- Fees, spread, slippage, leverage, and maintenance margin are explicit account settings.
- Bracket children activate only after the parent fill. One-cancels-other cancellation is atomic.
- Partial exits cannot create a negative position or silently resize siblings beyond remaining quantity.
- Realtime alerts and replay rules remain separate namespaces.

## Task 1: Pure Replay Cursor And Broker

Create `dashboard/chart_replay.py` and `tests/test_chart_replay.py`.

Implement typed data-only constructors and functions for sessions, market/limit/stop/bracket orders, cursor advance, next-bar fills, conservative same-bar collisions, partial exits, fees/slippage, leverage checks, NAV/exposure/drawdown, and append-only events. Test future-bar isolation first.

## Task 2: Persistent Sessions And Branches

Extend `agent_console/storage.py` and server APIs with replay session create/get/save/list/branch/delete operations. Persist a versioned snapshot plus append-only events, use optimistic revision checks, and test restart/branch/idempotency behavior.

## Task 3: Shared Replay Controller

Create `dashboard/chart_replay_controller.py`. Synchronize one cursor across requested workspace panels, load only data up to the cursor, expose play/pause/step/speed/jump-live commands, and preserve independent realtime alert state.

## Task 4: Chart Orders And Risk Editing

Extend the renderer/embed contract with pending order, fill, position, stop, and target overlays. Browser drag produces a typed price patch preview; server validation applies it. Do not infer an order from a drawing.

## Task 5: Bottom Terminal UI

Create `dashboard/chart_replay_ui.py` and integrate ticker/fullscreen/workspace. Add compact tabs for replay, orders, positions, strategy results, event log, and diagnostics. Support market/limit/stop/bracket entry, quantity/notional sizing, partial exits, commission/slippage/leverage settings, and persistent sessions.

## Task 6: Strategy And Condition Handoff

Allow a validated Strategy Studio run or shared condition tree to seed replay rules. Evaluation uses the same cursor-filtered context and records the exact condition trace with each simulated decision.

## Task 7: Verification

Run pure account-math tests, storage/API tests, chart integration tests, AppTest, and desktop/mobile browser verification. Record look-ahead, same-bar collision, partial-exit, restart, and synchronized-panel evidence in a Packet 2 report. Commit and push only with no failed functional capability.

