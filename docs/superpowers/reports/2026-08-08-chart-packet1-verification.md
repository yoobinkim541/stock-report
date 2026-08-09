# Chart Packet 1 Verification

검증 시각: 2026-08-09 UTC

브랜치: `codex/chart-analysis-workbench`

대상: `ChartDocument` v1, Plotly adapter, condition DSL v1

## Automated Tests

```bash
../../.venv/bin/pytest -q \
  tests/test_chart_document.py tests/test_chart_transforms.py \
  tests/test_chart_data_policy.py tests/test_chart_series.py \
  tests/test_chart_studies.py tests/test_chart_conditions.py \
  tests/test_chart_workbench.py
```

결과: **95 passed in 2.08s**

```bash
../../.venv/bin/pytest -q \
  tests/test_dashboard_charts.py tests/test_dashboard_pages.py \
  tests/test_chart_workspace.py tests/test_chart_workspace_pages.py \
  tests/test_chart_alerts.py tests/test_chart_alert_runner.py \
  tests/test_chart_alert_worker.py tests/test_plotly_embed.py \
  tests/test_plotly_embed_runtime.py
```

결과: **277 passed in 25.46s**, Plotly Timestamp 변환 경고 1건. 실패와 skip 없음.

합계: **372 passed**.

## Browser Verification

로컬 서버: `http://127.0.0.1:8512`

브라우저: Playwright Chromium 1228, headless

뷰포트: desktop `1440x1000`, mobile `390x844`

| State | Result | Evidence |
| --- | --- | --- |
| Line/fullscreen | nonblank, drawing toolbar and panes visible | `assets/chart-packet1-full-desktop.png` |
| Renko | parameter visible, ordered synthetic chart, no console error through normal navigation | `assets/chart-packet1-renko-desktop.png` |
| Weekly/mobile | bars visible, `scrollWidth == clientWidth == 390` | `assets/chart-packet1-weekly-mobile.png` |
| Monthly | bars visible, analysis loaded | `assets/chart-packet1-monthly-desktop.png` |
| Compare | common visible anchor is `0%`, MSFT/QQQ lines visible | `assets/chart-packet1-compare-desktop.png` |
| Analysis rail | eight tabs visible without an expander | `assets/chart-packet1-analysis-desktop.png` |

Observed initial fullscreen completion with live providers: about **15.3s**. Renko switch: about **8.1s**. These include Streamlit rerun, provider/cache work, chart serialization, and analysis loaders.

Normal sidebar navigation produced no console errors. Directly opening `/chart` caused Streamlit to request route-relative `/chart/_stcore/health` and `/chart/_stcore/host-config`, both 404, while the page still rendered. This is recorded as a Streamlit deep-link diagnostic rather than a chart-render failure.

## Performance Guardrails

- Browser-only drawing, live patch, crosshair, pinch, and relayout-loop contracts pass in `tests/test_plotly_embed_runtime.py`; they do not call Streamlit.
- Single-symbol time charts now use a category trading-bar axis. This removes weekend/holiday spaces, avoids the prior candle overlap path, and reduced the 5,000-bar benchmark materially.
- 5,000 bars with nine visible Plotly traces measured **p95 258.4ms** after category-axis optimization, down from about **889.8ms** with the datetime/rangebreak path.
- The target p95 below 50ms is not met by the retained Plotly adapter. Status is `intentionally different`: Packet 1 keeps Plotly for compatibility with the existing drawing runtime; this measurement is the evidence-based trigger for a Canvas price renderer. Default period windows remain much smaller than 5,000 bars.

## Capability Audit

| Capability | Status | Notes |
| --- | --- | --- |
| 12 chart types | implemented | Line, area, baseline, candle, hollow, HA, OHLC/high-low, Renko, Kagi, Line Break, Range |
| Synthetic precision | implemented | SourceTimestamp anchor and OHLCV-path warning |
| Session and provenance | implemented | Regular/extended/all; timezone, source, freshness, strict timeframe match |
| Market-closure compression | implemented | Single-symbol category trading-bar axis; compare retains datetime for mixed calendars |
| Series manager | implemented | Price, peer/benchmark, portfolio, fundamental, analyst contracts; ticker/workspace controls |
| Study registry | implemented | Safe registered studies, bounded parameters, data-only strategy preview |
| Analysis rail | implemented | Trend, patterns, MTFA, seasonality, RS, fundamentals, alerts, quality |
| Condition DSL | implemented | Nested boolean tree and price/indicator/fundamental/relative/drawing/event/portfolio operands |
| Multi-symbol alert runtime | implemented | Deduplicated contexts, three-valued result, persisted trace and missing contexts |
| Export | implemented | Chart document, analysis snapshot, source bars |
| Fullscreen continuity | implemented | Fullscreen reuses ticker chart state and renderer |
| Workspace continuity | implemented | Persisted document migration and shared renderer/rail |
| 5,000-bar p95 below 50ms | intentionally different | Plotly p95 258.4ms; Canvas migration trigger |
| Footprint/TPO/bid-ask imbalance | data-blocked | No authoritative tick plus bid/ask/aggressor data in the generic OHLCV path |

No Packet 1 functional capability is marked `failed`. Packet 2 starts from `ChartDocument` v1, the shared condition DSL, strict data bundle, and Plotly renderer adapter documented here.

## Known Trade-Offs

- Optional fundamentals and alerts fail independently; price analysis remains visible, but each provider call adds initial load cost.
- Price-based transforms are deterministic research views, not exchange-native tick construction.
- Mixed-calendar comparison remains on a datetime axis so one market's valid session is not silently discarded.
- `st.components.v1.html` emits an upstream deprecation notice and must be migrated without losing the custom drawing runtime.
