# Canvas Chart Renderer Design

## Goal

Add a production Canvas rendering path for dense time-based stock charts without replacing the renderer-neutral `ChartDocument` or removing the existing Plotly analysis tools. The default experience selects the fastest compatible renderer, exposes the decision, and always offers an explicit Plotly fallback.

## Approved Direction

Use TradingView Lightweight Charts 5.1 for the main price and volume surface. It is an Apache 2.0 HTML5 Canvas financial-chart library designed for thousands of bars and streaming updates. The integration uses our own normalized bars, events, replay state, and saved chart document; it does not use TradingView market data or widgets.

Primary references:

- https://www.tradingview.com/lightweight-charts/
- https://tradingview.github.io/lightweight-charts/docs/5.1/plugins/intro
- https://tradingview.github.io/lightweight-charts/docs/api/functions/createSeriesMarkers

The standalone library URL is pinned to version `5.1.0`. The chart enables Lightweight Charts attribution so the required TradingView link remains visible.

## Alternatives Considered

### Hand-written Canvas 2D engine

This gives complete control over drawing semantics but requires custom scales, hit testing, accessibility, touch gestures, crosshair synchronization, and realtime patching. It duplicates mature financial-chart infrastructure and has the highest regression risk.

### Plotly WebGL or bar decimation

This keeps one runtime but does not provide a true WebGL candlestick path. Decimation also changes the requested OHLC data and can hide extrema or trade markers. It is useful as a fallback optimization, not the primary dense-chart solution.

### Lightweight Charts hybrid

This is the selected approach. It provides the high-performance surface while Plotly remains available for features that do not yet have parity. The trade-off is maintaining two renderer adapters and making fallback reasons explicit.

## Renderer Contract

`ChartDocument.renderer.preferred` accepts:

- `auto`: use Canvas when the request is compatible and dense enough, otherwise Plotly;
- `canvas`: request Canvas, but fail visibly to Plotly when a required capability is unsupported;
- `plotly`: always use the existing renderer.

The default is `auto`. Existing documents with `plotly` preserve that value. The toolbar presents a stable segmented control labeled `자동`, `고성능`, and `분석`.

The renderer decision returns:

- selected backend;
- whether it was an automatic or explicit decision;
- machine-readable fallback reasons;
- a short user-facing status string;
- supported and omitted capabilities.

## Compatibility Policy

Canvas supports the first production slice when all of these are true:

- the transformed chart has a time axis;
- there is one primary price series and no normalized comparison series;
- the chart type is line, area, baseline, candlestick, hollow candle, Heikin-Ashi, bars, or high-low;
- logarithmic and linear price scales are both allowed;
- overlays are limited to values that can be serialized as series, markers, or price lines.

Canvas renders:

- OHLC candle/bar or line/area/baseline price series;
- volume histogram in a lower scale band;
- moving averages and other top-pane line traces already present in the Plotly figure;
- trade, alert, and event markers;
- average cost, current price, replay position, and pending replay order price lines;
- crosshair OHLC readout, pan, zoom, resize, dark/light theme, visible-range synchronization, and realtime last-bar updates.

Plotly is selected when any of these are required:

- normalized comparison mode;
- Renko, Kagi, Line Break, or Range sequence charts;
- editable drawings, Fibonacci, measurement, regression-channel, anchored-VWAP, or volume-profile drawing tools;
- drag editing of replay order lines;
- a lower technical or fundamental pane that has not been mapped to a Canvas pane.

Fallback never silently drops a feature. The UI displays the reason and retains the same `ChartDocument`, so switching renderers does not alter chart state.

## Automatic Selection

`auto` chooses Canvas for compatible charts when either condition is met:

- the transformed frame contains at least 1,000 bars;
- the chart is rendered in compact multi-chart mode.

Below that threshold Plotly remains the default because its mature drawing runtime is more valuable than the small performance difference. An explicit `canvas` preference bypasses the bar threshold but not capability checks.

## Components

### `dashboard/chart_backend.py`

Pure capability and backend selection logic. It has no Streamlit or browser dependency and produces a typed decision object. This is the single source of truth used by ticker and workspace pages.

### `dashboard/lightweight_embed.py`

Serializes a rendered frame and supported overlays into a bounded JSON payload and emits standalone Streamlit component HTML. It owns the pinned library URL, chart lifecycle, Canvas sizing, crosshair readout, live update bridge, and range synchronization.

The payload rejects non-finite numbers, duplicate timestamps, unsupported chart types, and more than the configured bar ceiling. Time values use UTC epoch seconds, while the visible timezone remains part of the status line.

### Existing Plotly adapter

`dashboard/chart_renderer.py` remains responsible for transforms and the complete Plotly figure. The Canvas serializer may consume the same normalized frame and selected Plotly traces for compatible line overlays, avoiding duplicate indicator calculations.

### UI integration

Ticker and workspace chart surfaces call the backend selector after transformation. They render either `lightweight_embed` or `plotly_embed`, show the selected backend and fallback reason, and preserve the existing analysis rail, replay terminal, exports, and source status.

## Data Flow

```text
Provider bars and events
        |
        v
ChartDocument normalization and chart transform
        |
        +--> capability decision
                 |
                 +--> Canvas payload --> Lightweight Charts component
                 |
                 +--> Plotly figure --> existing Plotly component
        |
        v
Shared analysis rail, replay terminal, alerts, exports, persistence
```

## Failure Handling

- A malformed or empty payload renders a compact unavailable state instead of a blank Canvas.
- A library load timeout offers a `Plotly로 다시 열기` command and records a visible error reason.
- Unsupported features cause deterministic server-side fallback before HTML generation.
- Live updates validate symbol, timestamp, and positive finite price before changing the last bar.
- Renderer errors do not mutate the saved chart document or replay session.

## Performance And Resource Bounds

- Maximum initial Canvas payload: 20,000 bars.
- Auto threshold: 1,000 bars.
- The serializer must process 5,000 OHLCV bars in p95 under 50 ms on the deployment host.
- Browser `setData` for 5,000 bars must report under 50 ms p95 across ten warm runs in Chromium.
- Realtime updates use `series.update()` and must not rebuild the full chart.
- Compact multi-chart panels omit nonessential markers and cap initial bars according to the requested visible period.

## Verification

### Unit and contract tests

- renderer preference normalization and validation;
- automatic selection and every fallback reason;
- finite, sorted, duplicate-free OHLCV payloads;
- line, candle, volume, markers, price lines, themes, log scale, and live-update contracts;
- a deliberate missing-library path that produces a visible fallback action;
- Plotly behavior remains unchanged when selected.

### Performance tests

- generate a deterministic 5,000-bar fixture;
- benchmark serializer p95 over at least twenty runs;
- run a Chromium harness that measures `setData` ten times after library warm-up;
- fail the performance gate when p95 is 50 ms or slower.

### Browser verification

Verify ticker and 4-panel workspace views at desktop `1440x1000` and mobile `390x844`:

- at least one nonblank Canvas with changing pixels;
- no console, page, or failed-request errors;
- no horizontal overflow or text overlap;
- pan, zoom, crosshair, visible-range synchronization, and realtime update work;
- switching to analysis mode restores Plotly drawing tools;
- unsupported modes visibly fall back without losing the document selection.

## Trade-Offs

The hybrid path materially improves dense-chart interaction and multi-chart cost while retaining feature completeness through Plotly. It does not claim immediate drawing-tool parity. Users who need drawing or editable replay orders choose analysis mode, and the compatibility decision makes that boundary visible rather than silently removing controls.
