# Ticker Analysis Always-Visible Indicators Design

Date: 2026-08-01

## Purpose

Redesign the ticker analysis page so that the most important decision aids are visible immediately, without requiring the user to open expanders.

The current page already has strong raw data and several useful charts, but the core indicators are split across nested or collapsed sections. This makes the experience feel slower than it needs to be and hides the very signals the page is supposed to help interpret.

The goal of this change is to keep the same underlying data model and analysis logic, while changing the presentation so the page feels like a live analyst desk:

- the main verdict stays at the top,
- analyst opinion, target price, fair value, and peer context stay visible,
- detail sections remain available, but are no longer required to understand the page,
- charts and indicators become the default reading surface rather than a hidden secondary layer.

## Current State

The ticker page already renders a strong summary card and multiple supporting sections in `dashboard/pages/ticker.py`, but several important indicators are hidden behind expanders:

- `🇰🇷 한국 종목 심화 컨텍스트`
- `📐 자체 역사 PER 밴드 — 지금이 비싼지/싼지`
- `🏭 동종업계 비교`
- `🤖 AI 종목 분석`
- `🤖 AI 연관 종목 추천`

In the valuation area, the page also mixes plain metrics with chart-heavy sections, which makes the layout feel vertically dense and hard to scan. The page has reusable visual primitives in `dashboard/theme.py`, including compact metric bands and gauge-like cards, and chart helpers in `dashboard/charts.py` for analyst ratings, target price fans, and valuation bands.

The main product gap is not missing data. It is discoverability and hierarchy.

## Design Decision

Use an **always-visible analysis rail** directly under the summary verdict card, and convert the most important hidden indicators into compact visual cards and charts.

The new structure should behave like this:

1. The summary verdict still appears first.
2. Immediately below it, a compact rail shows the core indicators:
   - analyst stance,
   - target price / upside,
   - fair value / valuation stance,
   - peer context,
   - self-history PER band.
3. Charts become first-class surface elements instead of expander content.
4. Expanders, if kept at all, only contain extended explanation and raw tables, not the core decision cues.

This is a presentation-only redesign. It should not change the underlying valuation math, consensus logic, or data sources.

## Architecture

### 1. Summary block

Keep the existing `analysis_card_html(...)` verdict card as the topmost module.

This card remains the anchor for the page and answers:

- what the overall verdict is,
- what the positives are,
- what the risks are,
- what the next checks are.

### 2. Always-visible indicator rail

Add a compact rail directly below the verdict card for the indicators users most often need at a glance.

Recommended contents:

- `애널리스트 의견` distribution or rating mix
- `목표가 / 여력`
- `적정가 인디케이터`
- `자체 PER 밴드`
- `동종업계 비교`

The rail should use compact cards, chips, or mini panels rather than large stacked blocks. For Korean equities, the rail can include flow and DART-backed fundamentals when available, but it should not force an expander to reveal the basic context.

### 3. Visual emphasis

Use charts to make the indicators easier to scan:

- analyst ratings as a compact distribution chart,
- target price as a fan chart,
- fair value as a bullet / band indicator,
- PER history as a compact band chart,
- peer comparison as either a compact table plus value bars or a small comparison strip.

The rule is simple: if a metric is important enough to influence the verdict, it should not live only in text.

### 4. Detail sections

Keep a lower “details” area for longer explanation and raw numbers.

This area may still use expanders, but only for:

- long-form explanation,
- verbose tables,
- supporting notes,
- data provenance.

The user should not have to open a section to understand the page’s main opinion.

## Components

### `dashboard/pages/ticker.py`

Refactor the page to separate:

- summary verdict rendering,
- always-visible indicator rail rendering,
- detailed supporting sections,
- LLM analysis blocks.

Likely helper extraction:

- `_analysis_snapshot(...)` should stop hiding KR core context inside an expander.
- `_valuation(...)` should render core valuation cues in visible blocks first.
- `_per_self_band_section(...)` and `_peer_comparables_section(...)` should become visible modules instead of default-collapsed expanders.

### `dashboard/theme.py`

Reuse the existing compact HTML primitives where possible:

- `position_band_html(...)`
- gauge / card styles

If the current primitives are not expressive enough for the new rail, add one small reusable helper rather than building one-off HTML in multiple places.

### `dashboard/charts.py`

Keep the existing chart helpers, but change how they are surfaced:

- charts should render in a compact visible area,
- chart titles should remain descriptive,
- chart heights should stay short enough to preserve scanability.

## Data Flow

```text
valuation / consensus / flows / peer data / PER history
        -> page helpers
        -> compact indicator rail
        -> visible charts
        -> optional details
```

The data flow itself does not change. The visibility and ordering do.

## Error Handling

The redesign should preserve the current “show what is known, hide what is missing” behavior.

Guidelines:

- If analyst consensus is missing, show a neutral placeholder card instead of collapsing the section.
- If target price data is missing, show a short “no consensus target” state.
- If DART-backed KR fundamentals are unavailable, keep the visible rail but downgrade the card to a fallback state.
- If peer data is missing, show the current company card and a “peer data unavailable” note rather than hiding the section entirely.

The page should fail visibly, not silently.

## Testing Strategy

Extend the dashboard smoke tests so they confirm the new visibility rules.

### Page-level tests

- ticker render still succeeds for US and KR tickers,
- KR deep context indicators appear without opening an expander,
- analyst opinion and target price sections render in the visible area,
- PER band and peer comparison are no longer only expander content,
- fallback states still render when data is missing.

### Regression tests

- verify the page still renders when consensus data is empty,
- verify the page still renders when DART-backed metrics are unavailable,
- verify chart helpers still appear in the output when data exists,
- verify no new exceptions are thrown by the revised layout.

## Rollout

This is a safe UI-only change and can ship directly behind the existing page route.

Recommended rollout:

1. land the layout refactor,
2. confirm the page still passes dashboard smoke tests,
3. inspect the ticker page visually in both KR and US cases,
4. keep the current data logic untouched unless a rendering edge case forces a small adjustment.

## Scope Boundaries

### In scope

- ticker analysis layout,
- always-visible indicator placement,
- chart surfacing and visual hierarchy,
- visible fallback states,
- smoke tests for the new layout.

### Out of scope

- new data collection,
- valuation math changes,
- new model logic,
- alerting,
- backend API changes.

## Success Criteria

- The user can understand the page’s core judgment without opening any expander.
- Analyst opinion, target price, fair value, PER band, and peer context are visible on first load.
- Charts support the narrative instead of hiding inside collapsed sections.
- The page still renders cleanly for both KR and US tickers, including fallback states.
- Existing valuation logic and data sources remain intact.
