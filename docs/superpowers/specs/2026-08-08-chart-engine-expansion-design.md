# TradingView-Grade Chart Platform Design

## Goal

Upgrade the stock-report chart experience from a large Plotly figure into a professional analysis and trading platform. The target is the practical capability of a paid TradingView workflow, while adopting stronger ideas from TrendSpider, Koyfin, FINVIZ Elite, and thinkorswim.

The program covers four connected products:

1. an analysis workbench;
2. a replay and paper-trading terminal;
3. a synchronized multi-chart desktop;
4. tick-aware order-flow charts when the underlying data supports them.

The work is intentionally split into implementation packets. Each packet must be usable and verified on its own, but all packets share one chart state model so later work does not require another rewrite.

## Approved Direction

Use a hybrid chart core.

- Keep the current Plotly renderer while it remains useful.
- Move chart state and calculations out of Streamlit widgets and Plotly figures into a renderer-neutral `ChartDocument`.
- Add a renderer adapter boundary so a higher-performance Canvas renderer can replace the main price surface without replacing data loading, indicators, drawings, alerts, replay, or workspace storage.
- Do not embed a third-party chart that prevents use of our own data, paper-trading state, AI changes, or saved drawings.

This direction preserves the substantial chart work already in the repository while leaving a credible path past Plotly's interaction and realtime performance ceiling.

## Benchmark Findings

### TradingView

Paid TradingView combines breadth and polish:

- 20-plus chart representations, including price-based charts;
- hundreds of indicators and strategies;
- drawing tools, layouts, templates, and synchronization;
- bar replay with multiple speeds and synchronized charts;
- price, drawing, indicator, strategy, and watchlist alerts;
- seasonality, fundamental graphs, automatic patterns, screening, and data export;
- chart-based order entry and paper trading.

Sources:

- https://www.tradingview.com/features/
- https://www.tradingview.com/support/solutions/43000703407-chart-types-available-on-tradingview/
- https://www.tradingview.com/support/solutions/43000703396-drawing-tools-available-on-tradingview/
- https://www.tradingview.com/support/solutions/43000474024-how-do-i-turn-bar-replay-on/
- https://www.tradingview.com/support/solutions/43000692404-layouts-charts-drawings-indicators-and-their-interaction/
- https://www.tradingview.com/support/solutions/43000520149-introduction-to-tradingview-alerts/

### TrendSpider

The transferable advantage is a shared condition model. The same visual condition can be used in a scanner, backtest, alert, or strategy. Multi-timeframe and multi-symbol conditions are first-class, and AI can create a condition tree without hiding the resulting logic.

Sources:

- https://help.trendspider.com/kb/multi-factor-alerts/multi-factor-alerts-overview/
- https://help.trendspider.com/kb/scripts/script-manager
- https://help.trendspider.com/kb/charting/multi-timeframe-analysis
- https://help.trendspider.com/kb/automated-technical-analysis/trendline-customization-and-filtering

### Koyfin

Koyfin's strongest contribution is series composition: price, benchmark, portfolio, valuation, and fundamental metrics belong in one graph workflow. Graph templates, reusable custom views, and clean company snapshots make dense research faster.

Source:

- https://www.koyfin.com/features/

### FINVIZ Elite

FINVIZ contributes fast transitions between chart, screener, portfolio, correlated symbols, and fundamentals. Premarket/after-hours visibility, export, and alert coverage are treated as routine workflow features.

Source:

- https://finviz.com/elite

### thinkorswim

thinkorswim contributes a dense but action-oriented terminal: studies and drawings stay near the chart, quote context remains visible, and complex orders can be created and managed without leaving the price surface.

Source:

- https://www.schwab.com/trading/thinkorswim

## Current Baseline

Already present:

- candlestick, line, and Heikin-Ashi charts;
- visible-start normalized comparison;
- common technical indicators and multiple lower panes;
- drawing, magnet, measurement, Fibonacci, anchored VWAP, and volume profile tools;
- automatic trendline and channel detection;
- replay curtain support;
- price and chart-rule alert storage;
- saved workspaces, templates, and up to six chart panels;
- seasonality, relative-strength, pattern, and multi-timeframe analysis helpers;
- paper-trade events rendered as chart markers;
- AI-generated workspace patch previews.

Main gaps:

- chart state is spread across Streamlit widget state, Plotly figure construction, and browser JavaScript;
- only three chart types are selectable;
- session policy and data provenance are not prominent controls;
- analysis helpers are not consistently visible beside the chart;
- price, benchmark, portfolio, and fundamental series do not share a visible series manager;
- alerts, scanner conditions, strategy conditions, and AI patches do not share one condition model;
- replay is not a full simulator with synchronized charts and paper orders;
- workspace synchronization and autosave are incomplete;
- tick and price-level volume data are not broadly available, so order-flow charts cannot yet be represented honestly.

## Capability Matrix

| Capability | Current state | Target | Packet |
| --- | --- | --- | --- |
| Time-based price charts | line, candle, Heikin-Ashi | line, area, baseline, bars, hollow candle, high-low | 1 |
| Price-based charts | absent | Renko, Kagi, Line Break, Range with visible parameters | 1 |
| Session control | implicit | regular/extended policy with provenance | 1 |
| Technical studies | broad fixed set | searchable registry, parameters, pane/axis placement, templates | 1 |
| Custom studies | strategy code exists elsewhere | versioned study API and Strategy Studio output on charts | 1 |
| Series comparison | price comparison | price, peer, benchmark, NAV, fundamental, analyst series manager | 1 |
| Pattern analysis | helper functions | visible, evidence-backed pattern and multi-timeframe rail | 1 |
| Seasonality | helper function | interactive seasonal profile and summary | 1 |
| Fundamental graphing | fixed EPS/fundamental panes | selectable metrics, periods, normalization, peers | 1 |
| Events | earnings/news helpers | earnings, dividends, macro, news, trades, and alerts with filters | 1 |
| Alerts | price and chart rules | shared multi-symbol, multi-timeframe condition tree | 1 |
| Data export | limited | visible/full series and chart document export | 1 |
| Replay | curtain/cutoff support | synchronized media controls and persistent sessions | 2 |
| Chart trading | trade markers | simulated orders, brackets, partial exits, drag editing | 2 |
| Strategy report | separate Strategy Studio | chart-linked runs, metrics, trades, and condition handoff | 2 |
| Multi-chart | up to six panels | one to sixteen progressive panels | 3 |
| Synchronization | partial settings | symbol, interval, range, crosshair, replay, drawing sync | 3 |
| Persistence | workspace/template storage | autosave, versions, restore, duplicate, share snapshot | 3 |
| Keyboard workflow | browser defaults | shortcuts and command palette | 3 |
| Footprint/TPO | bar-derived volume profile only | authoritative tick/price-level order-flow views | 4 |

## Architecture

### 1. ChartDocument

Create a renderer-neutral document with a versioned schema. It contains:

- symbol, market, timezone, timeframe, period, and adjustment policy;
- session policy: regular, extended, or all available sessions;
- source, freshness, retention, and quality metadata;
- primary and secondary series;
- chart type and chart-type parameters;
- indicators and pane placement;
- drawings and synchronization scope;
- market, earnings, dividend, news, alert, and trade events;
- replay cursor and simulator state;
- visual range, scale mode, and active panel.

Streamlit controls update the document. Renderers consume it. Storage saves it. AI proposes typed document patches. No consumer should need to parse a Plotly figure to understand chart state.

### 2. Data Pipeline

```text
Providers and local stores
        |
        v
Normalized bars and events
  timestamp, session, source, freshness, quality
        |
        v
Chart transforms and studies
        |
        v
ChartDocument
        |
        +--> Plotly renderer
        +--> future Canvas renderer
        +--> replay engine
        +--> alert/scanner/backtest evaluator
```

Every series must disclose its source and latest timestamp. Missing data must produce an explicit unavailable state. A requested timeframe must never silently render another timeframe.

### 3. Renderer Boundary

The initial adapter wraps the existing `dashboard.charts.price_chart()` and `dashboard.plotly_embed` runtime. It accepts a `ChartDocument` and emits an embeddable chart.

The future Canvas renderer may replace only the main price and volume surfaces. Analysis panels can remain Plotly until replacement produces a measurable interaction or realtime benefit. Drawings use normalized time/price coordinates so they survive renderer changes.

### 4. Shared Condition DSL

Define a typed condition tree with:

- `all`, `any`, and `none` groups;
- price, indicator, drawing, event, fundamental, relative-performance, and portfolio operands;
- symbol and timeframe on every operand;
- crossing, comparison, range, change, and happened-within operators;
- regular/extended session policy;
- confirmation timing and expiration;
- a human-readable explanation generated from the same tree.

The same condition document is evaluated by chart alerts, watchlist scans, strategy backtests, and paper-trading rules. AI may create or edit it, but the user always sees the resulting tree and diff before applying it.

## Product Packets

### Packet 1: Analysis Workbench

#### Chart types

Support these modes through deterministic transforms:

- line, area, baseline, candlestick, hollow candle, and Heikin-Ashi;
- Renko, Kagi, Line Break, and Range;
- bar and high-low views where the renderer can express them cleanly.

Price-based charts must state their box, reversal, or range parameters. Their synthetic timestamps are anchors to source observations, not claims of exchange tick precision.

#### Session-aware charts

Expose regular and extended-session controls for equities and ETFs. Compress weekends and holidays for time-based charts. Show the active timezone, latest bar time, source, and whether the current bar is delayed, realtime, or reconstructed.

#### Series manager

The visible series manager supports:

- one primary price series;
- multiple benchmark or peer series normalized at the visible start;
- portfolio or paper-account NAV where available;
- revenue, EPS, net income, margins, valuation, and analyst metrics where available;
- independent axis, normalization, and visibility settings.

#### Analysis rail

Keep compact always-visible sections beside the chart:

- trend and channel;
- deterministic pattern candidates;
- multi-timeframe trend agreement;
- seasonality;
- relative strength;
- fundamental and analyst snapshot;
- active alerts and data-quality status.

The rail explains computed evidence. It must not present an unverified pattern as a fact.

#### Alert builder

Create price, drawing, indicator, fundamental, and multi-condition alerts from the chart. Conditions can span symbols and timeframes. The UI shows the condition tree, schedule, session policy, last evaluation, and reason for the latest pass or failure.

#### Custom study and strategy bridge

Expose a versioned study interface that accepts normalized bars and returns named plot series, pane metadata, events, and optional condition operands. Strategy Studio and AI-generated strategies can publish through this interface after validation. Arbitrary code does not run in the browser, and applying a study always shows its source, parameters, and version.

### Packet 2: Replay And Paper-Trading Terminal

- replay start by bar or date;
- play, pause, step, speed, and jump-to-live controls;
- selectable replay update interval;
- synchronized replay across chart panels;
- simulated market, limit, stop, and bracket orders;
- draggable pending orders and stop/target levels;
- partial exits, commission, slippage, leverage, and account settings;
- persistent replay sessions with equity, exposure, drawdown, and trade log;
- protection against using future events or indicator values.

Realtime alerts continue to use realtime data and remain visually separate from replay rules.

### Packet 3: Multi-Chart Desktop

- one to sixteen panels with practical presets;
- independent or synchronized symbol, timeframe, visible range, crosshair, replay, and drawings;
- layout, style, indicator, series, and condition templates;
- autosave, named versions, restore, duplicate, export, and shareable snapshots;
- keyboard shortcuts and command palette;
- a compact watchlist and quote context near the charts;
- graceful mobile layouts that prioritize one active chart while preserving panel state.

Synchronization must be document-based, not simulated by copying widget values after rerender.

### Packet 4: Tick-Aware Pro Charts

Add only when authoritative tick or price-level volume data exists for the selected market:

- volume footprint;
- bid/ask imbalance;
- session and anchored volume profiles backed by price-level volume;
- TPO or market profile;
- tick replay and order-flow alerts.

When only OHLCV bars exist, these controls remain unavailable with a clear data requirement. Bar volume must not be fabricated into bid/ask volume.

## User Interface

### Top toolbar

- symbol and source status;
- chart type;
- timeframe and period;
- session policy;
- indicators and series;
- compare, replay, alert, and layout actions;
- undo, redo, save, export, and fullscreen icons.

### Left tool rail

Group cursor, line, channel, Fibonacci, measurement, annotation, magnet, lock, hide, and clear actions. Use icons with tooltips and keep dimensions stable.

### Chart body

The primary chart remains visually dominant. The chart body owns event marks, order marks, current position, active risk levels, and data status. It does not contain large explanatory cards.

### Right analysis rail

Show the series manager, analysis summaries, alerts, and data quality in compact tabs or sections. On narrow screens it becomes a drawer.

### Bottom terminal

Use tabs for replay, orders, positions, strategy results, event log, and data diagnostics. The terminal remains collapsed until relevant but retains state when hidden.

## Error Handling And Accuracy

- Validate timeframe density before rendering cached data.
- Preserve source timestamps and exchange timezones.
- Distinguish realtime, delayed, reconstructed, adjusted, and synthetic data.
- Reject nonpositive inputs for logarithmic scales and price-based chart parameters.
- Keep compare mode anchored at the first common visible observation.
- Keep corporate-action adjustment policy consistent across compared series.
- Avoid look-ahead in replay, automatic patterns, alerts, and strategy evaluation.
- Continue rendering available series when one optional comparison or fundamental metric fails.
- Show a concise reason and recovery action for unavailable data.

## Test Strategy

### Pure unit tests

- `ChartDocument` normalization, migration, validation, and patching;
- chart transforms for Renko, Kagi, Line Break, Range, baseline, and hollow candles;
- session filtering and timezone boundaries;
- series normalization and corporate-action consistency;
- condition parsing, explanation, and evaluation across symbols and timeframes;
- replay cursor isolation and look-ahead prevention;
- partial fills, bracket orders, commissions, and account math.

### Integration tests

- existing OHLC loaders populate the same document contract;
- saved workspaces round-trip without dropping drawings or conditions;
- scanner, alert, backtest, and paper-trading evaluators return consistent results for the same condition;
- missing optional series degrades without losing the primary chart;
- renderer adapters preserve event, trade, alert, and drawing coordinates.

### Browser verification

- desktop and mobile screenshots for ticker and fullscreen charts;
- candle density, labels, panes, rail, terminal, and controls do not overlap;
- pan, zoom, crosshair, drawing, replay, compare, and resizing work;
- nonblank canvas/Plotly pixel checks;
- live updates preserve visible range and drawings;
- synchronized panels remain aligned after symbol and timeframe changes.

### Performance budgets

- no full Streamlit rerun for a browser-only pan, zoom, crosshair, or drawing move;
- last-price updates patch the active chart without rebuilding unrelated panels;
- pan, zoom, and crosshair updates target a p95 browser response below 50 ms on a 5,000-bar single chart with eight visible studies;
- a sixteen-panel workspace progressively renders the active and visible panels first and does not initialize all hidden analysis rails;
- `ChartDocument` autosave payloads remain below 500 KB per workspace excluding market bars, which stay in the data cache;
- browser verification records render time and interaction measurements so a renderer migration is triggered by evidence, not preference.

## Delivery Order

1. Extract and test `ChartDocument`, transform registry, renderer adapter, and shared condition schema.
2. Deliver Packet 1 chart modes, session controls, series manager, analysis rail, and multi-condition alerts.
3. Deliver Packet 2 replay and chart-based paper trading.
4. Deliver Packet 3 synchronized multi-chart desktop and persistence.
5. Audit data availability and deliver Packet 4 only for markets with sufficient inputs.
6. Run a final requirement-by-requirement comparison against the official benchmark matrix.

Each packet receives its own implementation plan, tests, browser verification, commit, and deployment check. Completing Packet 1 is progress toward this program, not completion of the full goal.

## Completion Criteria

The program is complete only when:

- Packets 1 through 3 are implemented and verified;
- Packet 4 is implemented for each market whose provider exposes timestamped trades plus bid/ask or aggressor-side information, and is explicitly data-blocked elsewhere;
- the benchmark matrix identifies each compared feature as implemented, intentionally different, or data-blocked;
- chart state survives save, reload, and renderer changes through the versioned document;
- alerts, scans, backtests, and paper rules share the same tested condition semantics;
- replay and paper trading pass look-ahead and account-math tests;
- desktop and mobile runtime checks pass for primary and multi-chart pages;
- all related test suites pass and the final changes are committed and pushed.

## Trade-Offs

- A hybrid renderer boundary adds schema and adapter work now, but prevents the current Plotly implementation from becoming the permanent domain model.
- Deterministic price-based transforms provide reproducible research but cannot match exchange-native tick construction without tick data.
- A shared condition language constrains ad hoc feature logic, but pays back by keeping alerts, scans, tests, and AI edits consistent.
- Supporting sixteen charts requires strict performance budgets and progressive rendering; rendering every analysis panel at once is not a valid implementation.
- Broad TradingView feature parity is a multi-packet program. Shipping and verifying each packet is safer than introducing many unverified controls in one release.
