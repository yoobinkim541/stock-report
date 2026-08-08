# Chart Analysis Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Packet 1 of the TradingView-grade chart program: a renderer-neutral chart document, professional chart modes, session and provenance controls, a reusable series/study manager, an always-visible analysis rail, shared multi-condition alerts, and exports on both ticker and fullscreen chart surfaces.

**Architecture:** Introduce a versioned `ChartDocument` as the source of truth, feed it through deterministic data policies and chart transforms, and render it through a Plotly adapter that preserves the existing drawing runtime. A shared condition DSL replaces alert-only condition parsing so the same typed tree can later power scans, backtests, and paper rules. Streamlit UI code consumes pure snapshots and document patches rather than owning market logic.

**Tech Stack:** Python 3.11, pandas/numpy, Plotly, Streamlit, SQLite through `agent_console.storage`, pytest, Streamlit AppTest, browser verification through the existing chart embed runtime.

## Global Constraints

- Preserve the visible-start `0%` comparison contract and corporate-action policy across every secondary price series.
- A requested timeframe must never silently render another timeframe.
- Compress weekends and exchange holidays on time-based charts.
- Distinguish realtime, delayed, reconstructed, adjusted, and synthetic data in the visible status line.
- Never infer bid/ask or aggressor-side volume from OHLCV bars.
- Price-based chart timestamps identify the source observation that completed a synthetic element; they are not exchange tick timestamps.
- Existing trade markers, event markers, drawing persistence, pan/zoom, live last-price patching, and logarithmic scale must continue to work on compatible time-based chart modes.
- AI changes produce typed `ChartDocument` or condition-tree patches and require a visible diff before apply.
- No arbitrary user code runs in the Streamlit or browser process.
- Packet 1 is not completion of the full program; replay trading, sixteen-panel desktop behavior, and tick-aware charts remain Packet 2 through Packet 4.

## File Map

- Create `dashboard/chart_document.py`: schema, migration, normalization, validation, typed patching, and workspace-panel adapters.
- Create `dashboard/chart_transforms.py`: deterministic chart-mode registry and synthetic OHLC/line transforms.
- Create `dashboard/chart_data_policy.py`: session filtering, source/freshness metadata, and export-safe bar normalization.
- Create `dashboard/chart_renderer.py`: Plotly renderer adapter and renderer-neutral render result.
- Create `dashboard/chart_series.py`: secondary series, fundamental series, normalization, and export tables.
- Create `dashboard/chart_studies.py`: searchable study definitions, parameters, and safe custom-study output contract.
- Create `dashboard/chart_conditions.py`: condition schema, migration, validation, explanation, and pure evaluation.
- Create `dashboard/chart_workbench.py`: pure analysis snapshot assembled from existing chart analysis helpers.
- Create `dashboard/chart_workbench_ui.py`: shared Streamlit toolbar, series manager, analysis rail, alert builder, and export UI.
- Modify `dashboard/charts.py`: add renderer styles without taking ownership of chart state.
- Modify `dashboard/pages/ticker.py`: replace ad hoc chart-kind/session/series assembly with the document and workbench UI.
- Modify `dashboard/pages/chart_full.py`: retain the same document and controls in fullscreen.
- Modify `dashboard/chart_workspace.py`: persist the document fields and migrate existing panels.
- Modify `dashboard/chart_workspace_ui.py`: render panels through the same adapter and reuse the workbench rail/alert builder.
- Modify `agent_console/chart_alerts.py`, `agent_console/chart_alert_runner.py`, and `agent_console/chart_alert_worker.py`: delegate condition semantics to the shared evaluator and load all required symbol/timeframe contexts.
- Add focused tests under `tests/test_chart_document.py`, `tests/test_chart_transforms.py`, `tests/test_chart_data_policy.py`, `tests/test_chart_series.py`, `tests/test_chart_studies.py`, `tests/test_chart_conditions.py`, and `tests/test_chart_workbench.py`.
- Extend `tests/test_dashboard_charts.py`, `tests/test_dashboard_pages.py`, `tests/test_chart_workspace.py`, `tests/test_chart_alerts.py`, `tests/test_chart_alert_runner.py`, and `tests/test_plotly_embed_runtime.py`.

---

### Task 1: Versioned ChartDocument And Workspace Migration

**Files:**
- Create: `dashboard/chart_document.py`
- Modify: `dashboard/chart_workspace.py:20-198`
- Test: `tests/test_chart_document.py`
- Test: `tests/test_chart_workspace.py`

**Interfaces:**
- Produces: `CHART_DOCUMENT_VERSION = 1`
- Produces: `CHART_TYPES: frozenset[str]`
- Produces: `default_chart_document(ticker: str = "MSFT") -> dict[str, Any]`
- Produces: `normalize_chart_document(raw: Mapping[str, Any] | None, *, ticker: str = "MSFT") -> dict[str, Any]`
- Produces: `validate_chart_document(document: Mapping[str, Any]) -> tuple[list[str], list[str]]`
- Produces: `apply_chart_document_patch(document: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]`
- Produces: `document_from_panel(panel: Mapping[str, Any], *, workspace_id: str = "") -> dict[str, Any]`
- Produces: `panel_from_document(document: Mapping[str, Any], panel: Mapping[str, Any] | None = None) -> dict[str, Any]`

- [ ] **Step 1: Write failing document and migration tests**

Create `tests/test_chart_document.py`:

```python
from dashboard import chart_document as cd


def test_default_document_is_valid_and_renderer_neutral():
    doc = cd.default_chart_document("AAPL")
    errors, warnings = cd.validate_chart_document(doc)
    assert errors == []
    assert warnings == []
    assert doc["version"] == 1
    assert doc["symbol"] == "AAPL"
    assert doc["chart"]["type"] == "candlestick"
    assert doc["session"]["policy"] == "regular"
    assert doc["renderer"] == {"preferred": "plotly"}


def test_old_workspace_panel_migrates_without_dropping_settings():
    panel = {
        "id": "p1", "ticker": "MSFT", "timeframe": "1d", "period": "1y",
        "chart_kind": "heikin_ashi", "top_indicators": ["이동평균선"],
        "bottom_indicators": ["거래량", "RSI"], "compare": ["QQQ"],
        "log_scale": True,
    }
    doc = cd.document_from_panel(panel, workspace_id="growth")
    assert doc["chart"]["type"] == "heikin_ashi"
    assert doc["series"][1]["symbol"] == "QQQ"
    assert doc["scale"]["type"] == "log"
    restored = cd.panel_from_document(doc, panel)
    assert restored["chart_kind"] == "heikin_ashi"
    assert restored["compare"] == ["QQQ"]


def test_patch_rejects_unknown_paths_and_invalid_chart_parameters():
    doc = cd.default_chart_document("MSFT")
    try:
        cd.apply_chart_document_patch(doc, {"chart.params.box_size": 0})
    except ValueError as exc:
        assert "box_size" in str(exc)
    else:
        raise AssertionError("zero box_size must fail")
    try:
        cd.apply_chart_document_patch(doc, {"python.eval": "open('/etc/passwd')"})
    except ValueError as exc:
        assert "unsupported patch path" in str(exc)
    else:
        raise AssertionError("unknown patch path must fail")
```

Extend `tests/test_chart_workspace.py` to assert legacy `chart_kind` values migrate and all new `CHART_TYPES` survive save/normalize round trips.

- [ ] **Step 2: Run the tests and verify the import failure**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_document.py tests/test_chart_workspace.py
```

Expected: `ImportError` for `dashboard.chart_document`.

- [ ] **Step 3: Implement the schema and safe patch paths**

Create `dashboard/chart_document.py` with these constants and defaults:

```python
CHART_DOCUMENT_VERSION = 1
CHART_TYPES = frozenset({
    "line", "area", "baseline", "candlestick", "hollow_candle",
    "heikin_ashi", "bars", "high_low", "renko", "kagi", "line_break", "range",
})
SESSION_POLICIES = frozenset({"regular", "extended", "all"})
SERIES_KINDS = frozenset({"price", "benchmark", "peer", "portfolio", "fundamental", "analyst"})
_PATCH_PATHS = {
    "symbol", "timeframe", "period", "chart.type", "chart.params",
    "chart.params.box_size", "chart.params.reversal", "chart.params.lines",
    "chart.params.range_size", "session.policy", "scale.type", "series",
    "studies", "events", "analysis.visible", "analysis.sections",
}


def default_chart_document(ticker="MSFT"):
    symbol = ticker_names.normalize_input(ticker) or "MSFT"
    return {
        "version": CHART_DOCUMENT_VERSION,
        "symbol": symbol,
        "market": "kr" if symbol.endswith((".KS", ".KQ")) else "us",
        "timezone": "Asia/Seoul" if symbol.endswith((".KS", ".KQ")) else "America/New_York",
        "timeframe": "1d",
        "period": "6mo",
        "chart": {"type": "candlestick", "params": {}},
        "session": {"policy": "regular"},
        "source": {"name": "", "as_of": None, "freshness": "unknown", "quality": "unknown"},
        "series": [{"id": "primary", "kind": "price", "symbol": symbol, "axis": "primary", "normalization": "raw", "visible": True}],
        "studies": [], "drawings": [], "events": [], "alerts": [],
        "analysis": {"visible": True, "sections": ["trend", "patterns", "mtfa", "seasonality", "relative_strength", "fundamentals", "alerts", "data_quality"]},
        "replay": {"active": False, "cursor": None},
        "scale": {"type": "linear"},
        "view": {"start": None, "end": None},
        "renderer": {"preferred": "plotly"},
    }
```

Implement deep-copy normalization, version migration, allowlisted dotted patch assignment, numeric parameter checks, series ID uniqueness, and adapters between existing panel fields and the new document. Map old `candle` to `candlestick`; preserve `line` and `heikin_ashi`.

- [ ] **Step 4: Wire workspace normalization to the document contract**

In `dashboard/chart_workspace.py`, replace the local three-value `CHART_KINDS` with `chart_document.CHART_TYPES`, retain `chart_kind` as the persisted compatibility field, and add a `document` object to each normalized panel:

```python
p["document"] = chart_document.normalize_chart_document(
    src.get("document") or chart_document.document_from_panel(p, workspace_id=str(out.get("id") or "")),
    ticker=p["ticker"],
)
p.update(chart_document.panel_from_document(p["document"], p))
```

Validation must report document errors with a `panel[index].document` prefix.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_document.py tests/test_chart_workspace.py
```

Expected: all pass.

Commit:

```bash
git add dashboard/chart_document.py dashboard/chart_workspace.py tests/test_chart_document.py tests/test_chart_workspace.py
git commit -m "add) 차트 문서 스키마와 워크스페이스 마이그레이션 추가" -m "차트 상태를 버전형 문서로 분리하고 기존 패널 저장 형식을 호환 마이그레이션했습니다. 렌더러 교체가 가능해지는 대신 초기 스키마 검증 비용이 추가됩니다."
```

---

### Task 2: Deterministic Chart Transform Registry

**Files:**
- Create: `dashboard/chart_transforms.py`
- Test: `tests/test_chart_transforms.py`

**Interfaces:**
- Produces: `ChartTransformResult(frame: pd.DataFrame, render_kind: str, x_mode: str, synthetic: bool, metadata: dict[str, Any])`
- Produces: `available_chart_types() -> tuple[str, ...]`
- Produces: `transform_chart(hist: pd.DataFrame, chart_type: str, params: Mapping[str, Any] | None = None) -> ChartTransformResult`

- [ ] **Step 1: Write failing transform tests**

Create tests using a deterministic OHLC fixture and assert:

```python
def test_renko_emits_fixed_size_bricks_with_source_timestamps(ohlc):
    out = transforms.transform_chart(ohlc, "renko", {"box_size": 2.0})
    assert out.synthetic is True
    assert out.render_kind == "candlestick"
    assert set(out.frame.columns) >= {"Open", "High", "Low", "Close", "SourceTimestamp"}
    assert set((out.frame["Close"] - out.frame["Open"]).abs().round(8)) == {2.0}
    assert out.frame.index.is_monotonic_increasing


def test_kagi_reverses_only_after_configured_amount(ohlc):
    out = transforms.transform_chart(ohlc, "kagi", {"reversal": 3.0})
    assert out.render_kind == "line"
    assert out.synthetic is True
    assert out.metadata["reversal"] == 3.0
    assert len(out.frame) < len(ohlc)


def test_line_break_uses_previous_three_lines(ohlc):
    out = transforms.transform_chart(ohlc, "line_break", {"lines": 3})
    assert out.metadata["lines"] == 3
    assert out.render_kind == "candlestick"
    assert not out.frame.index.duplicated().any()


def test_range_bars_have_exact_range_and_no_time_claim(ohlc):
    out = transforms.transform_chart(ohlc, "range", {"range_size": 2.5})
    assert out.x_mode == "sequence"
    assert (out.frame["High"] - out.frame["Low"] >= 2.5 - 1e-9).all()
    assert out.metadata["source_precision"] == "ohlcv_close_path"
```

Also cover line, area, baseline, candlestick, hollow candle, Heikin-Ashi, bars, high-low, empty frames, unsorted/duplicate input, ATR-derived defaults, and nonpositive parameters.

- [ ] **Step 2: Run tests and verify failure**

Run `./.venv/bin/pytest -q tests/test_chart_transforms.py`.

Expected: import failure.

- [ ] **Step 3: Implement transform result and registry**

Create the frozen result dataclass and dispatch table:

```python
@dataclass(frozen=True)
class ChartTransformResult:
    frame: pd.DataFrame
    render_kind: str
    x_mode: str
    synthetic: bool
    metadata: dict[str, Any]


_TRANSFORMS = {
    "line": _identity_line,
    "area": _identity_line,
    "baseline": _identity_line,
    "candlestick": _identity_candle,
    "hollow_candle": _identity_candle,
    "heikin_ashi": _heikin_ashi,
    "bars": _identity_candle,
    "high_low": _identity_candle,
    "renko": _renko,
    "kagi": _kagi,
    "line_break": _line_break,
    "range": _range_bars,
}
```

Normalize input with `normalize_ohlc_frame`. For Renko and Range, iterate the close path and emit as many elements as required when one source bar crosses multiple box boundaries. Assign a unique integer sequence index and keep the completing source time in `SourceTimestamp`. Kagi tracks current direction and extreme; a reversal is emitted only after `reversal` price units. Three-line break compares close against the high/low of the previous `lines` synthetic bodies. ATR defaults use the latest finite 14-period ATR and fall back to one percent of the latest close.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_transforms.py tests/test_dashboard_charts.py -k "transform or heikin or candle"
```

Commit:

```bash
git add dashboard/chart_transforms.py tests/test_chart_transforms.py
git commit -m "add) 가격 기반 차트 변환 레지스트리 추가" -m "Renko, Kagi, Line Break, Range 등 결정론적 변환을 추가하고 합성 시점과 데이터 정밀도를 명시했습니다. OHLCV 경로 기반 모드는 틱 차트와 동일하지 않다는 제한이 있습니다."
```

---

### Task 3: Session Policy And Data Provenance

**Files:**
- Create: `dashboard/chart_data_policy.py`
- Modify: `dashboard/views.py:1520-1685`
- Modify: `dashboard/cached.py:179-299`
- Test: `tests/test_chart_data_policy.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Produces: `SessionResult(frame: pd.DataFrame, metadata: dict[str, Any])`
- Produces: `apply_session_policy(hist, *, market: str, timeframe: str, policy: str, timezone: str | None = None) -> SessionResult`
- Produces: `chart_data_status(hist, *, requested_timeframe: str, actual_timeframe: str, source: str, now: datetime | None = None) -> dict[str, Any]`
- Produces: `exportable_bars(hist, metadata: Mapping[str, Any]) -> pd.DataFrame`

- [ ] **Step 1: Write timezone and session boundary tests**

Use timezone-aware fixtures around 09:30 and 16:00 New York, 09:00 and 15:30 Seoul, DST transitions, daily/weekly bars, and naive provider timestamps. Assert regular filtering includes boundaries, extended preserves bars, daily bars are not time-filtered, and metadata records the decision.

```python
def test_us_regular_session_filters_intraday_in_exchange_timezone(us_intraday):
    out = policy.apply_session_policy(
        us_intraday, market="us", timeframe="5m", policy="regular", timezone="America/New_York"
    )
    local = out.frame.index.tz_convert("America/New_York")
    assert local[0].strftime("%H:%M") == "09:30"
    assert local[-1].strftime("%H:%M") == "16:00"
    assert out.metadata["excluded_bars"] > 0
```

- [ ] **Step 2: Run tests and verify failure**

Run `./.venv/bin/pytest -q tests/test_chart_data_policy.py`.

- [ ] **Step 3: Implement policy and explicit status metadata**

Use `zoneinfo.ZoneInfo`, never fixed UTC offsets. Intraday regular windows are `[09:30, 16:00]` for `us` and `[09:00, 15:30]` for `kr`. `extended` and `all` retain available input while recording that provider coverage may be incomplete. For naive indices, localize using provider metadata when supplied; otherwise mark `timezone_assumption=True` instead of silently claiming certainty.

`chart_data_status` must reject `requested_timeframe != actual_timeframe`, classify source timestamps without mutating bars, and return `freshness` from `realtime`, `delayed`, `stale`, or `unknown`. For intraday bars, let `bar_seconds` be the requested interval: an explicitly realtime source is `realtime` when age is at most `max(90, 2 * bar_seconds)` seconds; any source is `delayed` when age is at most `max(1_200, 2 * bar_seconds)` seconds; older data is `stale` while the exchange session is open. Outside the session, preserve the source's explicit class and add `market_closed=True`. Daily-or-higher data is `stale` only after four calendar days unless the source explicitly reports another class. Missing or naive timestamps without provider timezone metadata are `unknown` and set `timezone_assumption=True`.

- [ ] **Step 4: Thread status through loaders without changing their DataFrame return contract**

Keep `cached.ohlc()` and `cached.ohlc_tf()` backwards compatible. Add `views.chart_data_bundle(ticker, timeframe, session_policy="regular") -> dict` and cached wrapper returning:

```python
{
    "frame": frame,
    "requested_timeframe": timeframe,
    "actual_timeframe": timeframe,
    "session": session_meta,
    "source": source_meta,
}
```

Do not return daily data from this function when intraday data is unavailable.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_data_policy.py tests/test_dashboard.py -k "ohlc_tf or chart_data"
```

Commit the new policy, loader wrappers, and tests with title `add) 차트 세션 정책과 데이터 출처 상태 추가` and a body noting that strict mismatch rejection exposes provider gaps instead of hiding them.

---

### Task 4: Plotly Renderer Adapter And Professional Chart Styles

**Files:**
- Create: `dashboard/chart_renderer.py`
- Modify: `dashboard/charts.py:993-1430`
- Test: `tests/test_dashboard_charts.py`
- Test: `tests/test_plotly_embed.py`

**Interfaces:**
- Produces: `RenderedChart(figure: Any, frame: pd.DataFrame, document: dict[str, Any], transform: ChartTransformResult, warnings: tuple[str, ...])`
- Produces: `render_plotly_chart(document, hist, *, chart_kwargs: Mapping[str, Any] | None = None) -> RenderedChart`
- Consumes: `chart_transforms.transform_chart(...)`

- [ ] **Step 1: Write renderer contract tests**

Assert every `CHART_TYPES` value returns a nonempty Plotly figure; area has `fill="tozeroy"`; baseline colors points above/below the selected base; hollow candles encode previous-close and open/close direction; bars/high-low use OHLC-compatible traces; synthetic charts expose their precision warning; trade/event markers are snapped to `SourceTimestamp`; compare mode rejects synthetic price-based charts with a clear warning and falls back to normalized line comparison without mutating the document.

- [ ] **Step 2: Run tests and verify failure**

Run `./.venv/bin/pytest -q tests/test_dashboard_charts.py -k "chart_type or renderer"`.

- [ ] **Step 3: Implement the adapter**

`render_plotly_chart` validates the document, applies the transform, maps the result to existing `charts.price_chart`, and applies style-only changes after figure creation. Keep `charts.price_chart` compatible for callers that have not migrated. Use this mapping:

```python
_PLOTLY_KIND = {
    "line": "line", "area": "line", "baseline": "line", "kagi": "line",
    "candlestick": "candle", "hollow_candle": "candle", "heikin_ashi": "candle",
    "bars": "ohlc", "high_low": "ohlc", "renko": "candle",
    "line_break": "candle", "range": "candle",
}
```

Extend `price_chart` only enough to accept `kind="ohlc"`, `line_fill`, `baseline`, and `hollow` style arguments. Preserve category-axis compression for synthetic and time-based candle charts.

- [ ] **Step 4: Verify embed invariants and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_dashboard_charts.py tests/test_plotly_embed.py tests/test_plotly_embed_runtime.py
```

Commit with title `add) 차트 렌더러 어댑터와 전문 차트 스타일 추가` and a body describing compatibility and the extra transform/render boundary.

---

### Task 5: Series Manager And Safe Study Registry

**Files:**
- Create: `dashboard/chart_series.py`
- Create: `dashboard/chart_studies.py`
- Modify: `dashboard/chart_workspace.py:20-166`
- Test: `tests/test_chart_series.py`
- Test: `tests/test_chart_studies.py`

**Interfaces:**
- Produces: `normalize_series_specs(raw, *, primary_symbol: str) -> list[dict[str, Any]]`
- Produces: `load_series(spec, *, price_loader, fundamental_loader, nav_loader) -> pd.Series | None`
- Produces: `normalize_visible_series(primary, secondary, *, view_days: int | None) -> dict[str, pd.Series]`
- Produces: `series_export_frame(primary, secondary) -> pd.DataFrame`
- Produces: `StudyDefinition(id, label, placement, parameters, compute)`
- Produces: `StudyOutput(series: dict[str, pd.Series], placement: str, events: tuple[dict, ...], metadata: dict)`
- Produces: `study_catalog() -> tuple[StudyDefinition, ...]`
- Produces: `run_study(study_id: str, hist, params: Mapping[str, Any] | None = None) -> StudyOutput`
- Produces: `study_output_from_strategy_preview(preview: Mapping[str, Any]) -> StudyOutput`

- [ ] **Step 1: Write failing series and study tests**

Test duplicate series IDs, primary-series enforcement, common-visible-start normalization, sparse quarterly fundamental dates, portfolio NAV, missing optional series, parameter type/range validation, and a safe registered custom study.

```python
def test_visible_normalization_anchors_all_price_series_at_zero(primary, benchmark):
    out = chart_series.normalize_visible_series(primary, {"QQQ": benchmark}, view_days=90)
    common = out["primary"].dropna().index.intersection(out["QQQ"].dropna().index)[0]
    assert out["primary"].loc[common] == 0.0
    assert out["QQQ"].loc[common] == 0.0


def test_study_registry_rejects_unknown_and_invalid_parameters(hist):
    with pytest.raises(ValueError, match="unknown study"):
        chart_studies.run_study("python_eval", hist, {})
    with pytest.raises(ValueError, match="period"):
        chart_studies.run_study("sma", hist, {"period": 0})
```

- [ ] **Step 2: Implement series types and registry**

Represent studies with callable objects registered in module code. Parameter definitions use explicit `type`, `min`, `max`, and `default`. Register the existing visible studies with their current defaults. `study_output_from_strategy_preview` accepts only `plots: [{name, dates, values, placement}]`, `events: [{date, kind, price, label}]`, and scalar `metadata`; reject unknown top-level keys, unequal date/value lengths, non-finite values, and source-code strings. Do not import or execute a callable from the preview.

Fundamental series use `cached.chart_fundamentals()` rows and explicit metric keys: `revenue`, `net_income`, `margin`, `eps_actual`, `eps_est`, and valuation/analyst keys present in the source. Missing metrics return `None` plus a warning, not zeros.

- [ ] **Step 3: Persist series and studies in ChartDocument/workspaces**

Update workspace templates so style, study, and series templates serialize document-backed values without losing legacy `top_indicators`, `bottom_indicators`, or `compare` fields.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_series.py tests/test_chart_studies.py tests/test_chart_workspace.py
```

Commit with title `add) 차트 시리즈 관리자와 안전한 지표 레지스트리 추가` and a body noting that registered outputs improve reuse while intentionally excluding arbitrary runtime code.

---

### Task 6: Shared Multi-Condition DSL

**Files:**
- Create: `dashboard/chart_conditions.py`
- Modify: `agent_console/chart_alerts.py`
- Test: `tests/test_chart_conditions.py`
- Test: `tests/test_chart_alerts.py`

**Interfaces:**
- Produces: `normalize_condition(raw: Mapping[str, Any]) -> dict[str, Any]`
- Produces: `validate_condition(condition: Mapping[str, Any]) -> list[str]`
- Produces: `condition_requirements(condition: Mapping[str, Any], *, default_symbol: str, default_timeframe: str) -> set[tuple[str, str]]`
- Produces: `explain_condition(condition: Mapping[str, Any]) -> str`
- Produces: `evaluate_condition(condition, contexts, *, now: str | None = None) -> dict[str, Any]`
- Context key: `(symbol: str, timeframe: str)` with `previous`, `current`, `events`, and `as_of` mappings.

- [ ] **Step 1: Write failing schema, migration, and semantics tests**

Cover legacy leaves and `{"all": [...]}` migration, nested `all`/`any`/`none`, multi-symbol/timeframe requirements, price and indicator crossings, fundamental values, relative performance, drawing lines, range, change, happened-within, missing contexts, and explanation text.

```python
def test_nested_condition_preserves_boolean_semantics(contexts):
    condition = {
        "op": "all",
        "children": [
            {"type": "indicator", "symbol": "AAPL", "timeframe": "1d", "field": "rsi_14", "operator": "greater_than", "value": 55},
            {"op": "any", "children": [
                {"type": "price", "symbol": "QQQ", "timeframe": "1h", "field": "close", "operator": "crossing_up", "value": 500},
                {"type": "fundamental", "symbol": "AAPL", "timeframe": "1d", "field": "forward_pe", "operator": "less_than", "value": 25},
            ]},
        ],
    }
    result = chart_conditions.evaluate_condition(condition, contexts)
    assert result["matched"] is True
    assert len(result["trace"]) >= 3
```

- [ ] **Step 2: Implement normalized tree and evaluation trace**

Canonical groups are `{"op": "all|any|none", "children": [...]}`. Canonical leaves include `type`, `symbol`, `timeframe`, `field`, `operator`, `value`, `session`, and optional `window`, `unit`, or drawing points. Every evaluation returns `matched`, `reason`, and a per-node `trace`; missing data is `unknown`, and `unknown` must not become true under `none`.

- [ ] **Step 3: Delegate legacy chart alerts to the DSL**

Keep `evaluate_chart_alert(...)` public and backwards compatible. Build a single-symbol context from its scalar arguments, evaluate the normalized condition, and translate the trace into the existing event payload. Remove `_condition_leaves` once regression tests prove parity.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_conditions.py tests/test_chart_alerts.py
```

Commit with title `add) 차트 다중 조건 공통 DSL 추가` and a body explaining the shared semantics and stricter unknown-data behavior.

---

### Task 7: Multi-Symbol Alert Runtime And Audit Trace

**Files:**
- Modify: `agent_console/chart_alert_runner.py`
- Modify: `agent_console/chart_alert_worker.py`
- Modify: `agent_console/storage.py:669-780`
- Modify: `dashboard/views.py:2105-2140`
- Test: `tests/test_chart_alert_runner.py`
- Test: `tests/test_chart_alert_worker.py`
- Test: `tests/test_agent_console_storage.py`

**Interfaces:**
- Consumes: `chart_conditions.condition_requirements(...)`
- Produces: `build_condition_contexts(rule, bars_by_key, fundamentals_by_symbol=None, events_by_symbol=None) -> dict`
- Changes: `evaluate_alert_rules(rules, bars_by_key, *, as_of=None) -> list[dict]`, where `bars_by_key` accepts `(symbol, timeframe)` keys and legacy symbol-only keys.

- [ ] **Step 1: Write failing cross-symbol/timeframe runtime tests**

Assert a rule requiring `AAPL:1d` and `QQQ:1h` loads both; a missing required series records `missing_contexts`; one rule failure does not stop others; last evaluation stores the complete node trace and data timestamps; once-only alerts remain idempotent.

- [ ] **Step 2: Update runner and worker loading**

Group enabled rules by every requirement from the canonical tree, call `load_chart_alert_bars(symbol, timeframe)` once per unique key, compute existing indicator values for each frame, and pass contexts to the DSL. Preserve the old worker callback shape by accepting both one-argument and two-argument loaders through signature inspection in the compatibility path only.

- [ ] **Step 3: Persist evaluation diagnostics**

Store `last_checked_at`, `matched`, `reason`, `trace`, `missing_contexts`, and source timestamps under `last_state`. Keep event notification payloads compact; full trace belongs in state/run records.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_alert_runner.py tests/test_chart_alert_worker.py tests/test_agent_console_storage.py tests/test_chart_alert_dispatcher.py
```

Commit with title `add) 다중 종목 차트 알림 실행기와 평가 추적 추가` and a body noting additional provider calls are deduplicated by symbol/timeframe.

---

### Task 8: Shared Workbench Snapshot And UI

**Files:**
- Create: `dashboard/chart_workbench.py`
- Create: `dashboard/chart_workbench_ui.py`
- Modify: `dashboard/pages/ticker.py:741-1007`
- Modify: `dashboard/pages/chart_full.py:20-70`
- Modify: `dashboard/chart_workspace_ui.py:94-191,191-295,404-617,739-920`
- Test: `tests/test_chart_workbench.py`
- Test: `tests/test_dashboard_pages.py`
- Test: `tests/test_chart_workspace_ui.py`

**Interfaces:**
- Produces: `build_analysis_snapshot(document, hist, *, ohlc_loader, fundamental_loader, alert_loader) -> dict[str, Any]`
- Produces: `render_chart_toolbar(document, *, key_prefix: str) -> dict[str, Any]`
- Produces: `render_series_manager(document, *, key_prefix: str) -> dict[str, Any]`
- Produces: `render_analysis_rail(snapshot, *, key_prefix: str) -> None`
- Produces: `render_condition_builder(document, *, workspace_id: str, key_prefix: str) -> None`
- Produces: `render_exports(document, render_result, *, key_prefix: str) -> None`

- [ ] **Step 1: Write failing pure snapshot tests**

Test US/KR benchmark selection, trendline summary, pattern evidence, MTFA partial data, seasonality sample counts, relative strength, fundamentals, active alerts, and source-quality messages. The snapshot must continue when one optional loader raises.

```python
def test_analysis_snapshot_keeps_available_sections_when_fundamentals_fail(hist):
    def boom(_symbol):
        raise RuntimeError("provider down")
    snapshot = chart_workbench.build_analysis_snapshot(
        chart_document.default_chart_document("MSFT"), hist,
        ohlc_loader=lambda symbol, tf: hist,
        fundamental_loader=boom,
        alert_loader=lambda symbol: [],
    )
    assert snapshot["trend"]["ok"] is True
    assert snapshot["patterns"] is not None
    assert snapshot["fundamentals"]["ok"] is False
    assert snapshot["fundamentals"]["reason"] == "provider down"
```

- [ ] **Step 2: Implement pure snapshot assembly**

Reuse `chart_analysis.relative_strength_summary`, `pattern_candidates`, `seasonality_summary`, and `multi_timeframe_summary`. Summarize `cached.trendlines_for` results without recomputing them in UI code. Include source/freshness metadata and label every pattern as a candidate with confidence, evidence, and invalidation.

- [ ] **Step 3: Implement the professional toolbar and series manager**

Replace the three-value chart segmented control with a compact chart-type menu grouped as time, smoothed, and price-based. Show chart-specific numeric controls only when required. Add session segmented control, source/freshness chip, searchable study popover, series manager rows, and JSON/CSV export commands. Preserve per-timeframe state by namespacing widget keys with document symbol/timeframe.

- [ ] **Step 4: Implement the always-visible analysis rail and condition builder**

Use a `[chart | rail]` column layout on desktop and a drawer/popover on narrow mode. The rail has compact tabs for `개요`, `시리즈`, `패턴`, `계절성`, `알림`, and `데이터`. The condition builder renders nested group rows from the canonical DSL, supports price/indicator/fundamental/relative-performance leaves in Packet 1, previews the Korean explanation and requirements, and saves only after validation.

- [ ] **Step 5: Route ticker, fullscreen, and workspace through one document**

In `ticker._price_chart`, construct/load one document, apply toolbar patches, request `chart_data_bundle`, load series/studies, call `chart_renderer.render_plotly_chart`, and pass the result to the existing embed. Fullscreen reuses the same session-state document. Workspace panels use their persisted document and the same renderer/UI helpers. Remove duplicate workspace-only analysis/alert formatting after callers migrate.

- [ ] **Step 6: Add AppTest smoke coverage**

Assert chart type and session controls render, selected Renko parameters persist across reruns, analysis tabs are visible without an expander, missing optional fundamentals do not crash, fullscreen uses the same selected type, and legacy workspace records still render.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_workbench.py tests/test_dashboard_pages.py tests/test_chart_workspace_ui.py tests/test_dashboard_charts.py tests/test_plotly_embed.py
```

Commit with title `add) 종목 차트 분석 워크벤치 UI 통합` and a body describing the shared ticker/fullscreen/workspace flow and the trade-off of a denser operational layout.

---

### Task 9: Runtime Verification, Performance Guardrails, And Packet 1 Audit

**Files:**
- Modify: `tests/test_plotly_embed_runtime.py`
- Modify: `tests/test_dashboard_pages.py`
- Create: `docs/superpowers/reports/2026-08-08-chart-packet1-verification.md`

**Interfaces:**
- Verifies all interfaces produced by Tasks 1 through 8.

- [ ] **Step 1: Add runtime assertions**

Extend browser/embed tests to assert pan/zoom/crosshair/drawing actions do not cause a Streamlit rerun, live-price patching preserves document/store keys, synthetic sequence axes remain ordered, and event/trade markers map to source timestamps.

- [ ] **Step 2: Run focused and full chart suites**

Run:

```bash
./.venv/bin/pytest -q tests/test_chart_document.py tests/test_chart_transforms.py tests/test_chart_data_policy.py tests/test_chart_series.py tests/test_chart_studies.py tests/test_chart_conditions.py tests/test_chart_workbench.py
./.venv/bin/pytest -q tests/test_dashboard_charts.py tests/test_dashboard_pages.py tests/test_chart_workspace.py tests/test_chart_workspace_ui.py tests/test_chart_alerts.py tests/test_chart_alert_runner.py tests/test_chart_alert_worker.py tests/test_plotly_embed.py tests/test_plotly_embed_runtime.py
```

Expected: all pass with no skipped Packet 1 contract tests.

- [ ] **Step 3: Start the dashboard and verify real pages**

Start the existing Streamlit app on an unused local port. Verify `/charts/fullview` and the ticker page at desktop `1440x1000` and mobile `390x844` sizes. Capture screenshots for line, candle, Renko, weekly/monthly, compare, analysis rail, and alert builder states. Check nonblank chart pixels, no overlap, stable controls, drawing persistence, fullscreen continuity, and explicit missing-data messages.

- [ ] **Step 4: Record measured results and matrix status**

Create the report with exact test commands/results, screenshot paths, render/interaction timings, known provider limitations, and each Packet 1 capability marked `implemented`, `intentionally different`, `data-blocked`, or `failed`. A `failed` item prevents the Packet 1 completion commit.

- [ ] **Step 5: Run final hygiene checks and commit**

Run:

```bash
git diff --check
git status --short
```

Commit only Packet 1 files and the verification report:

```bash
git commit -m "add) 트레이딩뷰급 분석 워크벤치 Packet 1 완성" -m "차트 문서, 전문 차트 유형, 세션·출처, 시리즈·지표, 분석 레일, 다중 조건 알림을 종목·풀뷰·워크스페이스에 통합했습니다. 관련 단위·통합·브라우저 검증 결과를 함께 기록했습니다. 기능 폭과 상태 검증이 늘어 초기 로딩 비용이 있으므로 렌더러 성능 지표를 후속 Packet에서도 추적합니다."
```

- [ ] **Step 6: Push and update the program audit**

Push `master` only after the final test evidence is fresh. Update the active program plan so Packet 2 begins with the exact `ChartDocument`, condition DSL, and renderer adapter versions delivered here.
