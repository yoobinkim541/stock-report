# Chart Workspace Design

## Goal

Upgrade the existing ticker chart into a TradingView-grade chart workspace: saved layouts, multi-chart views, synchronized chart state, reusable templates, richer analysis overlays, and AI-assisted chart configuration.

This does not replace the current `dashboard.charts` and `dashboard.plotly_embed` stack. It wraps and reuses it so the current drawing tools, replay, indicators, comparison mode, alerts, and fullview work continue to function.

## Benchmark Findings

### TradingView

TradingView treats a layout as the durable unit that contains charts, chart settings, drawing tools, and indicators. A layout can contain multiple charts, and higher tiers allow many charts in one workspace. TradingView also separates layout saving, chart templates, indicator templates, drawing templates, autosave, and drawing synchronization.

Sources:
- https://www.tradingview.com/support/solutions/43000692404-layouts-charts-drawings-indicators-and-their-interaction/
- https://www.tradingview.com/support/solutions/43000629990-leveraging-multi-chart-layouts-in-your-analysis/
- https://www.tradingview.com/charting-library-docs/latest/saving_loading/
- https://www.tradingview.com/support/solutions/43000746464-getting-started-with-supercharts/

### Koyfin

Koyfin's strongest transferable idea is the historical graph as a managed series workspace: users can compare securities, benchmarks, and financial/fundamental series as one graph, then save the result as "My Graphs". For this app, that maps to a chart-level series manager that can include price, relative return, benchmark, fundamentals, and portfolio series.

Sources:
- https://www.koyfin.com/help/charts-and-graphs/
- https://www.koyfin.com/features/advanced-graphing/

### TrendSpider

TrendSpider's useful differentiator is automated technical analysis with filters: trendline quality filtering, multi-timeframe analysis, and automated pattern recognition. For this app, the first useful version should add deterministic pattern and multi-timeframe summaries rather than opaque LLM-drawn lines.

Sources:
- https://help.trendspider.com/kb/automated-technical-analysis/trendline-customization-and-filtering
- https://help.trendspider.com/kb/charting/multi-timeframe-analysis
- https://help.trendspider.com/kb/automated-technical-analysis/automated-chart-pattern-recognition

### StockCharts

StockCharts contributes market-breadth and chart-type ideas: ChartLists, seasonality, RRG-style relative rotation, market carpets, and alternative chart types such as Renko, Kagi, Point & Figure, and Heikin-Ashi. This project already has Heikin-Ashi, heatmap-like treemaps, and a watchlist; the immediate gap is connecting those as workspace side panels and adding seasonality/relative strength.

Sources:
- https://stockcharts.com/features/
- https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-types
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays

## Current App Baseline

Already present:
- Single ticker chart in `dashboard/pages/ticker.py`
- Fullscreen chart in `dashboard/pages/chart_full.py`
- Plotly-based builder in `dashboard/charts.py`
- Custom embedded Plotly runtime in `dashboard/plotly_embed.py`
- Drawing tools, magnet snapping, replay curtain, anchored VWAP, volume profile, Fibonacci, channels, patterns, long/short measurement boxes
- Comparison mode normalized to display-window return
- Automatic trendlines and channels in `dashboard/trendlines.py`
- Price alerts connected to bot alert storage
- Intraday rangebreak handling and live last-bar patching

Main gaps:
- No durable named chart workspace model
- No server-backed layout/template/version storage
- No multi-chart grid workspace
- No synchronized symbol/interval/time-range groups
- No object tree for managing drawings/indicators
- No chart template or indicator template abstraction
- No AI patch workflow for chart configuration
- No Koyfin-style series manager for price plus fundamentals plus portfolio/benchmark series
- No TrendSpider-style multi-timeframe/pattern summary surfaced near the chart
- No StockCharts-style seasonality or relative-rotation workspace panel

## Product Design

### Workspace Model

A chart workspace is a durable, named object:

```json
{
  "id": "default",
  "name": "Default Workspace",
  "layout": "2x2",
  "active_panel": "p1",
  "sync": {
    "symbol": false,
    "interval": true,
    "range": true,
    "crosshair": true,
    "drawings": "symbol"
  },
  "panels": [
    {
      "id": "p1",
      "ticker": "NVDA",
      "timeframe": "1d",
      "period": "1y",
      "chart_kind": "candle",
      "top_indicators": ["이동평균선", "자동 추세선·채널"],
      "bottom_indicators": ["거래량", "RSI"],
      "compare": ["QQQ", "SMH"],
      "log_scale": false,
      "style_template_id": null,
      "indicator_template_id": "trend-default"
    }
  ]
}
```

Workspaces must be server-backed. The current localStorage drawing persistence can remain as the fast client cache, but workspace metadata and templates must be stored through Python storage APIs so they survive browser/device changes and can be versioned.

### Storage

Add storage APIs similar to the strategy studio version store:

- `save_chart_workspace(workspace: dict) -> dict`
- `list_chart_workspaces(limit: int = 50) -> list[dict]`
- `get_chart_workspace(workspace_id: str, version: int | None = None) -> dict | None`
- `save_chart_workspace_version(workspace_id: str, workspace: dict, note: str = "") -> dict`
- `list_chart_workspace_versions(workspace_id: str, limit: int = 30) -> list[dict]`
- `save_chart_template(template: dict) -> dict`
- `list_chart_templates(kind: str | None = None) -> list[dict]`
- `apply_chart_workspace_patch(workspace: dict, patch: dict) -> dict`

Tables:
- `chart_workspaces`
- `chart_workspace_versions`
- `chart_templates`

Templates should support three kinds:
- `style`: colors, candle type, log/linear, theme preferences
- `indicators`: top/bottom indicator selections and parameter values
- `series`: compare sets, benchmark sets, and Koyfin-style fundamental series presets

### Dashboard UX

`/charts/fullview` becomes the primary workspace screen.

Top bar:
- workspace selector
- save button
- layout selector: `1`, `2 vertical`, `2 horizontal`, `2x2`, `3+1`, `2x3`
- sync toggles: symbol, interval, range, crosshair, drawings
- template menu
- AI assistant popover

Workspace body:
- grid of chart panels
- each panel uses existing `ticker._price_chart` behavior where possible
- active panel has a subtle border and controls apply to that panel
- panel maximize restores the existing fullscreen feel

Right rail:
- object tree: indicators, comparisons, drawings status, alerts
- series manager: price, benchmarks, portfolio NAV, EPS/revenue/net income where available
- analysis summary: automatic trendlines, MTFA, pattern candidates, seasonality, relative strength

The UI should remain dense and operational, not a landing page. It should avoid nested card walls. Reuse Streamlit controls and the existing dark terminal style.

### Synchronization

Version 1 synchronization is server/session-state driven:
- symbol sync: changing active symbol pushes to all panels
- interval sync: changing timeframe pushes to all panels
- range sync: panels use the same `period`/`view_days`
- drawing sync: metadata state supports modes `off`, `layout_symbol`, `global_symbol`; full cross-device drawing sync can be phased because current drawings live inside iframe localStorage
- crosshair sync: first implementation stores the option and leaves the browser hook as an explicitly unverified runtime task; do not claim complete crosshair sync until browser runtime proves it

### AI Chart Agent

The AI chart agent does not directly mutate Streamlit widgets. It returns a structured patch:

```json
{
  "summary": "Switch active panel to 5m intraday trend view with QQQ comparison removed.",
  "patch": {
    "panels[0].timeframe": "5m",
    "panels[0].top_indicators": ["이동평균선", "VWAP(세션)", "매물대"],
    "panels[0].bottom_indicators": ["거래량", "MACD"]
  },
  "warnings": ["5m data is limited by provider retention."]
}
```

The UI must show a diff preview before applying. Applying the patch saves a workspace version.

Patch safety rules:
- reject unknown panel ids
- reject unknown indicator names
- cap compare symbols at the current UI limit unless the renderer is expanded
- never execute arbitrary code
- preserve existing fields not mentioned in the patch

### Analysis Enhancements

TrendSpider-inspired:
- Add a compact MTFA summary for active ticker: 5m/1h/1d/1wk trend state, RSI zone, distance from VWAP/MA, latest auto trendline proximity
- Add deterministic pattern candidates: double top/bottom, ascending/descending triangle approximation, channel breakout, Bollinger squeeze expansion
- Each pattern includes confidence, window, invalidation price, and evidence fields

Koyfin-inspired:
- Add a series manager that can add benchmark price series, relative return, portfolio NAV, EPS, revenue, net income, and analyst target series
- Series manager chooses chart mode: price, relative %, fundamentals panel

StockCharts-inspired:
- Add active ticker seasonality summary by calendar month when enough history exists
- Add relative strength panel vs SPY/QQQ or a user-selected benchmark
- Add RRG-like summary table: relative strength and momentum quadrant for watchlist symbols

## Non-Goals For First Implementation

- Full Pine Script compatibility
- Broker trading from chart
- Exact TradingView UI clone
- 16-chart realtime workspace
- Cross-device drawing synchronization inside the iframe runtime
- Paid market data replacement

These are explicitly out of scope for the first implementation but the model should not block later expansion.

## Data And Accuracy Constraints

- Intraday availability must be shown honestly because provider retention and missing bars have already been a real issue.
- US and KR symbols must keep existing market-specific behavior.
- Comparison mode must continue to normalize at the visible starting point.
- Weekend/holiday gaps must stay compressed.
- Existing trade markers and price alerts must remain available.
- No LLM-generated price levels should be treated as facts unless computed from data.

## Testing Plan

Unit tests:
- workspace default creation and validation
- workspace storage CRUD and version restore
- template save/list/apply
- patch application preserves unrelated fields and rejects bad indicators
- MTFA/pattern/seasonality functions handle short or missing data

Dashboard smoke tests:
- fullview renders workspace selector and at least one chart
- multi-panel config renders without exception
- AI patch preview text appears and apply changes session workspace

Regression tests:
- `tests/test_dashboard_charts.py`
- `tests/test_plotly_embed.py`
- `tests/test_plotly_embed_runtime.py`
- targeted `tests/test_dashboard_pages.py` fullview/ticker smoke

## Rollout Plan

1. Add pure workspace model, templates, patching, and tests.
2. Add storage APIs and tests.
3. Add dashboard workspace renderer using existing chart component.
4. Add AI patch preview/apply UI.
5. Add MTFA/pattern/seasonality/relative-strength analysis helpers and surface them in the right rail.
6. Run chart regression tests and commit.

## Open Trade-Offs

- Streamlit multi-chart reruns will be heavier than a pure JS charting app. The first implementation should keep panel count modest and reuse windowing already built in `charts.view_window`.
- Drawing persistence is currently excellent inside one browser but not server-backed. Moving drawing payloads server-side requires iframe-to-Streamlit communication that is riskier than the workspace metadata work.
- Crosshair synchronization is a browser runtime feature. Store the setting now, but verify before claiming it works.
- TradingView's paid-tier breadth is too large to copy exactly. The practical target is an integrated workspace with saved layouts, templates, multi-chart analysis, AI patches, and richer deterministic overlays.
