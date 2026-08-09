# Chart Multi-View Desktop Implementation Plan

**Goal:** Deliver Packet 3: persisted layouts through 4x4, real symbol/interval/date-range synchronization, selected-chart link groups, and an adaptive multi-chart desktop that remains usable with sixteen panels.

**Architecture:** Keep `chart_workspace` as the renderer-neutral source of truth. Every panel edit becomes a typed panel mutation that can propagate through a pure synchronization policy before normalization. Browser-only viewport synchronization uses a separate workspace channel and never writes back to Streamlit. Dense panels reuse the same `ChartDocument` and data contract but render a bounded study set; the active or maximized panel retains the full workbench.

## Global Constraints

- Existing saved layouts and panel IDs migrate without losing documents, drawings, indicators, templates, or replay state.
- Symbol and interval synchronization may target all panels or a selected link group; unlinked panels remain independent.
- Date-range synchronization includes persisted period selection and browser viewport changes.
- Drawings synchronize only across charts with the same symbol and compatible timeframe/scale contract.
- Synthetic sequence charts must not claim timestamp viewport synchronization.
- A 4x4 layout must not silently fetch compare series, fundamentals, or bottom studies for every background panel.
- Replay has one active account/session; background panels follow its cursor but cannot submit orders for another symbol.

## Task 1: Layout Schema And Migration

Extend `dashboard/chart_workspace.py` with layouts `3x3`, `4x3`, and `4x4`, stable panel IDs through expansion/contraction, per-panel `link_group`, and a persisted maximized-panel setting. Write model tests first for 16-panel normalization, shrink-expand preservation, validation, and legacy round trips.

## Task 2: Typed Panel Mutation And Synchronization

Add `mutate_panel(workspace, panel_id, changes)` as the only panel-edit path. Propagate `ticker`, `timeframe`, and `period` according to workspace booleans and optional link groups. Rebuild only the affected legacy/document fields and return a mutation trace for UI and tests. Cover linked, unlinked, global, invalid, and replay-compatible changes.

## Task 3: Browser Viewport Synchronization

Extend `dashboard/plotly_embed.py` with a separate `range_sync_key`. Broadcast user-originated x-range changes through localStorage with nonce and timestamp guards, map incoming timestamps to compatible axes, and suppress feedback loops. Ignore sequence/synthetic axes. Add Node runtime tests for peer update, no self-echo, no infinite relayout, disabled mode, and incompatible axis rejection.

## Task 4: Adaptive Sixteen-Panel UI

Replace the fixed modulo column loop in `dashboard/chart_workspace_ui.py` with a layout specification. Add 3x3/4x3/4x4 controls, compact panel headers, active/maximize commands, and stable panel heights. Active/maximized charts keep drawings, replay orders, comparisons, and full studies. Dense background panels use a bounded price/volume view and no compare/fundamental loaders. Remove the obsolete caption that says crosshair is settings-only.

## Task 5: UI Mutation Wiring

Route toolbar output and panel controls through `mutate_panel`. Add group selection and explicit sync state to each panel header. Ensure symbol/interval/period changes propagate before data loading and persist in workspace versions. Keep AI patch preview typed and visible before apply.

## Task 6: Verification

Run workspace/document/embed/runtime/AppTest regressions, a deterministic 16-panel loader-budget test, and desktop/mobile Playwright verification. Evidence must show 4x4 layout, group-specific propagation, crosshair/range channels, active-panel maximize/restore, no horizontal overflow, and no application console exception. Document Plotly limits and commit/push only after all checks pass.
