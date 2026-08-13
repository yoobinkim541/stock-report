# Canvas Chart Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automatically selected Lightweight Charts Canvas surface for dense compatible charts while retaining complete Plotly fallback behavior.

**Architecture:** Extend `ChartDocument` with a normalized renderer preference, make a pure backend decision before UI rendering, and serialize the existing transformed frame and compatible Plotly overlays into a bounded Canvas payload. Ticker and workspace pages share the same decision and component while all analysis, replay, persistence, and unsupported modes remain on Plotly.

**Tech Stack:** Python 3.11, pandas, Streamlit components, TradingView Lightweight Charts 5.1 standalone, JavaScript Canvas, pytest, Node runtime harness, Playwright browser verification.

## Global Constraints

- `ChartDocument` remains the source of truth; switching renderers must not mutate symbol, timeframe, chart type, studies, events, replay, or drawings.
- Renderer preferences are exactly `auto`, `canvas`, and `plotly`; new documents default to `auto` and existing explicit `plotly` documents remain valid.
- `auto` selects Canvas for compatible frames with at least 1,000 bars or compact multi-chart panels.
- Comparison, sequence charts, lower unmapped panes, and editable replay orders deterministically fall back to Plotly with a visible reason.
- Canvas initial payloads contain at most 20,000 sorted, duplicate-free finite bars.
- Lightweight Charts is pinned to `5.1.0`, attribution remains enabled, and a load failure presents a visible Plotly fallback command.
- Realtime updates use `series.update()` rather than rebuilding the full chart.
- 5,000-bar server serialization and warm Chromium `setData` p95 must each be below 50 ms.

---

### Task 1: Renderer Preference And Backend Decision

**Files:**
- Create: `dashboard/chart_backend.py`
- Modify: `dashboard/chart_document.py`
- Modify: `dashboard/chart_workbench_ui.py`
- Modify: `tests/test_chart_document.py`
- Create: `tests/test_chart_backend.py`

**Interfaces:**
- Produces: `RendererDecision(backend: str, requested: str, automatic: bool, reasons: tuple[str, ...], status: str)`
- Produces: `select_renderer(document, frame, *, compare=False, compact=False, lower_panes=False, editable_orders=False, x_mode="time") -> RendererDecision`

- [ ] **Step 1: Write failing tests for preference normalization and the full selection matrix**

```python
def test_default_document_prefers_auto_renderer():
    assert default_chart_document("AAPL")["renderer"] == {"preferred": "auto"}

def test_auto_selects_canvas_for_dense_compatible_chart():
    decision = select_renderer(document("auto"), frame(1000))
    assert decision.backend == "canvas"

def test_canvas_request_falls_back_for_compare_with_reason():
    decision = select_renderer(document("canvas"), frame(1000), compare=True)
    assert decision.backend == "plotly"
    assert "comparison" in decision.reasons
```

- [ ] **Step 2: Run focused tests and confirm missing contract failures**

Run: `.venv/bin/pytest -q tests/test_chart_document.py tests/test_chart_backend.py`

Expected: failure because the default is `plotly` and `dashboard.chart_backend` does not exist.

- [ ] **Step 3: Implement renderer validation, safe patching, decision reasons, and toolbar segmented control**

Implement constants `RENDERER_PREFERENCES`, `CANVAS_CHART_TYPES`, `AUTO_CANVAS_BAR_THRESHOLD`, and immutable `RendererDecision`. Return reasons from the ordered set `comparison`, `sequence_chart`, `lower_panes`, `editable_orders`, and `unsupported_chart_type`. Explicit `plotly` always returns Plotly without capability reasons.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/test_chart_document.py tests/test_chart_backend.py tests/test_chart_workspace.py tests/test_chart_workspace_pages.py`

Expected: all pass.

- [ ] **Step 5: Commit the backend contract**

```bash
git add dashboard/chart_backend.py dashboard/chart_document.py dashboard/chart_workbench_ui.py tests/test_chart_document.py tests/test_chart_backend.py
git commit -m "add) 차트 렌더러 자동 선택 계약 추가"
```

### Task 2: Bounded Canvas Payload And Browser Runtime

**Files:**
- Create: `dashboard/lightweight_embed.py`
- Create: `tests/test_lightweight_embed.py`
- Create: `tests/test_lightweight_embed_runtime.py`

**Interfaces:**
- Consumes: `chart_renderer.RenderedChart`
- Produces: `build_payload(rendered, *, compact=False, max_bars=20_000) -> dict[str, Any]`
- Produces: `lightweight_chart_html(payload, *, height, store_key=None, range_sync_key=None, live=False, light=False) -> str`

- [ ] **Step 1: Write failing payload tests**

Assert candle, line, volume, line overlays, markers, horizontal price lines, log scale, sorted duplicate removal, non-finite filtering, 20,000-bar bounding, and empty-frame errors. Assert the payload never contains `NaN` or `Infinity` after `json.dumps(..., allow_nan=False)`.

- [ ] **Step 2: Run payload tests and confirm import failure**

Run: `.venv/bin/pytest -q tests/test_lightweight_embed.py`

Expected: import failure for `dashboard.lightweight_embed`.

- [ ] **Step 3: Implement the payload serializer and standalone HTML component**

Use pinned `https://unpkg.com/lightweight-charts@5.1.0/dist/lightweight-charts.standalone.production.js`. Create the appropriate v5 series with `chart.addSeries(...)`, add volume and overlay series, markers through `createSeriesMarkers`, horizontal lines through `createPriceLine`, and a resize observer. Add crosshair OHLC readout, localStorage visible-range synchronization, realtime feed validation, and a 5-second library timeout containing a `Plotly로 다시 열기` button that writes `tnrenderer:<store_key>=plotly`.

- [ ] **Step 4: Add a Node runtime harness**

Stub the v5 API and DOM, execute the generated component script, and assert: a nonempty series receives all bars, `series.update()` handles one realtime event, range synchronization does not echo, resize updates dimensions, and the missing-library timeout renders the fallback button.

- [ ] **Step 5: Run payload and runtime tests**

Run: `.venv/bin/pytest -q tests/test_lightweight_embed.py tests/test_lightweight_embed_runtime.py`

Expected: all pass.

- [ ] **Step 6: Commit the Canvas component**

```bash
git add dashboard/lightweight_embed.py tests/test_lightweight_embed.py tests/test_lightweight_embed_runtime.py
git commit -m "add) 고성능 Canvas 차트 컴포넌트 추가"
```

### Task 3: Ticker And Workspace Integration

**Files:**
- Modify: `dashboard/pages/ticker.py`
- Modify: `dashboard/chart_workspace_ui.py`
- Modify: `dashboard/chart_workbench_ui.py`
- Modify: `tests/test_dashboard_pages.py`
- Modify: `tests/test_chart_workspace_pages.py`
- Modify: `tests/test_dashboard_charts.py`

**Interfaces:**
- Consumes: `chart_backend.select_renderer(...)`
- Consumes: `lightweight_embed.build_payload(...)`
- Consumes: `lightweight_embed.lightweight_chart_html(...)`

- [ ] **Step 1: Write failing integration contract tests**

Assert both chart surfaces reference the shared selector and Canvas component, renderer status is visible, the ticker preference persists in session state, workspace documents persist the preference, compact panels pass `compact=True`, and Plotly remains the branch for every fallback condition.

- [ ] **Step 2: Run integration tests and verify they fail because Canvas is not wired**

Run: `.venv/bin/pytest -q tests/test_dashboard_pages.py tests/test_chart_workspace_pages.py tests/test_dashboard_charts.py`

- [ ] **Step 3: Wire the ticker chart surface**

Add an always-visible `자동 / 고성능 / 분석` segmented control next to existing chart controls. Build the Plotly `RenderedChart` once, select the backend from the normalized document and actual feature flags, render Canvas for compatible decisions, and display `고성능 Canvas` or the exact Plotly fallback reason. Keep legacy mode and sequence charts on Plotly.

- [ ] **Step 4: Wire workspace and compact panels**

Use the document preference from the shared toolbar. Pass compact mode to the selector, retain drawing and replay URLs on Plotly, and use the existing crosshair/range keys for Canvas synchronization. Keep the analysis rail and replay terminal outside the renderer branch.

- [ ] **Step 5: Run page and chart regressions**

Run: `.venv/bin/pytest -q tests/test_dashboard_pages.py tests/test_chart_workspace_pages.py tests/test_dashboard_charts.py tests/test_plotly_embed.py tests/test_plotly_embed_runtime.py`

Expected: all pass with both renderer paths covered.

- [ ] **Step 6: Commit UI integration**

```bash
git add dashboard/pages/ticker.py dashboard/chart_workspace_ui.py dashboard/chart_workbench_ui.py tests/test_dashboard_pages.py tests/test_chart_workspace_pages.py tests/test_dashboard_charts.py
git commit -m "add) 종목과 멀티차트에 Canvas 자동 렌더링 연결"
```

### Task 4: Performance, Browser Verification, And Deployment

**Files:**
- Create: `tests/test_lightweight_embed_performance.py`
- Create: `docs/superpowers/reports/2026-08-13-canvas-chart-verification.md`
- Create: `docs/superpowers/reports/assets/canvas-chart-desktop.png`
- Create: `docs/superpowers/reports/assets/canvas-chart-mobile.png`

**Interfaces:**
- Verifies: `build_payload` 5,000-bar p95 under 50 ms over 20 warm runs
- Verifies: browser `setData` 5,000-bar p95 under 50 ms over 10 warm runs

- [ ] **Step 1: Add deterministic performance gates**

Use `time.perf_counter_ns()` and a generated OHLCV frame. Warm each path before measurement, sort durations, and assert the 95th-percentile sample is below `0.050` seconds. The browser harness records `performance.now()` around the real Lightweight Charts `setData` call.

- [ ] **Step 2: Run focused and full chart regressions**

Run:

```bash
.venv/bin/pytest -q tests/test_lightweight_embed*.py
.venv/bin/pytest -q tests/test_chart_*.py tests/test_dashboard_charts.py tests/test_dashboard_pages.py tests/test_plotly_embed.py tests/test_plotly_embed_runtime.py tests/test_kis_stream.py tests/test_kis_stream_watchlist.py tests/test_orderflow_store.py
```

Expected: all pass; both performance assertions remain under 50 ms.

- [ ] **Step 3: Start the dashboard and verify desktop/mobile browser behavior**

Run the dashboard on an unused local port. In Chromium, verify desktop `1440x1000` and mobile `390x844`: nonblank Canvas pixels, no console/page/request errors, no horizontal overflow, crosshair and range synchronization, realtime update, renderer switching, and visible Plotly fallback. Save the two screenshots.

- [ ] **Step 4: Write the evidence report**

Record exact test counts, p95 values, browser dimensions, Canvas pixel checks, errors, fallback checks, and remaining trade-offs in `docs/superpowers/reports/2026-08-13-canvas-chart-verification.md`.

- [ ] **Step 5: Commit, push, and restart affected services**

```bash
git add tests/test_lightweight_embed_performance.py docs/superpowers/reports/2026-08-13-canvas-chart-verification.md docs/superpowers/reports/assets/canvas-chart-desktop.png docs/superpowers/reports/assets/canvas-chart-mobile.png
git commit -m "test) Canvas 차트 성능과 브라우저 검증 완료"
git push origin master
```

Restart the dashboard service, confirm the public route responds, and confirm `kis_stream` remains alive with `ORDERFLOW_CAPTURE_ENABLED=true`.
