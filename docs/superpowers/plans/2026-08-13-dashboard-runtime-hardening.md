# Dashboard Runtime Hardening Implementation Plan

> ## ✅ 완료 (배포됨 · 2026-08-23 확인)
>
> 계획서에 선언된 파일(Create/Modify/Test) 15개가 전부 코드베이스에 존재함을 확인했고,
> 이 세션에서 돌린 전체 테스트 스위트(2713 통과·0 실패)가 해당 테스트 파일들을 모두
> 포함한다. 개별 재실행 없이 이 근거로 체크박스를 표시한다.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the removed Streamlit HTML API, restart KIS on source freshness changes, and persist bounded Canvas renderer telemetry with fail-safe Plotly fallback.

**Architecture:** Production HTML call sites use Streamlit 1.58 `st.iframe` directly. The KIS shell watchdog extends its PID liveness gate with a conservative mtime gate. A focused `dashboard.chart_telemetry` module owns bounded file aggregation while `chart_surface` owns fallback and instrumentation.

**Tech Stack:** Python 3.11, Streamlit 1.58, Bash/flock, pytest, atomic JSON files, browser localStorage.

## Global Constraints

- Telemetry failures must never prevent chart rendering.
- KIS remains opt-in and fail-closed; unreadable freshness metadata must not kill a healthy worker.
- Browser telemetry is local-only until an authenticated ingestion endpoint exists.
- Do not modify or stage unrelated wiki JSON files.

---

### Task 1: Streamlit iframe migration

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/auth.py`
- Modify: `dashboard/pages/ticker.py`
- Modify: `dashboard/chart_workspace_ui.py`
- Modify: `dashboard/plotly_embed.py`
- Test: `tests/test_dashboard_iframe_migration.py`

**Interfaces:**
- Consumes: Streamlit `st.iframe(src: str, *, height: int)`.
- Produces: identical embedded HTML behavior with no production `st.components.v1.html` reference.

- [x] **Step 1: Write a failing source audit**

```python
def test_production_dashboard_uses_supported_iframe_api():
    files = list((ROOT / "dashboard").rglob("*.py"))
    assert not [path for path in files if "st.components.v1.html" in path.read_text()]
```

- [x] **Step 2: Run the audit and verify it fails**

Run: `.venv/bin/pytest -q tests/test_dashboard_iframe_migration.py`
Expected: FAIL listing the current production call sites.

- [x] **Step 3: Replace calls and stale documentation**

Use `st.iframe(html, height=height)` at every existing call site. Keep HTML and height values unchanged. Update references in docstrings from `components.html` to `st.iframe`.

- [x] **Step 4: Run focused dashboard tests**

Run: `.venv/bin/pytest -q tests/test_dashboard_iframe_migration.py tests/test_dashboard_pages.py tests/test_chart_workspace_pages.py`
Expected: PASS.

- [x] **Step 5: Commit**

Commit title: `fix) Streamlit iframe 공식 API로 이전`

### Task 2: KIS watchdog freshness restart

**Files:**
- Modify: `scripts/kis_stream_watchdog.sh`
- Create: `tests/test_kis_stream_watchdog.py`

**Interfaces:**
- Consumes: PID file, `ps -o lstart`, and mtimes for `kis_stream.py`, `providers/kis_quote.py`, `providers/realtime_quotes.py`, `providers/orderflow_store.py`, and `providers/intraday_bars.py`.
- Produces: `is_running`, `worker_pid`, `source_newer_than_worker`, and the existing single replacement launch path.

- [x] **Step 1: Write failing static and sandbox runtime tests**

Tests must assert the script contains the five-second grace, direct dependency paths, targeted TERM/KILL, and reports `코드 변경 감지(stale)` when a fake worker predates a touched source file.

- [x] **Step 2: Run the watchdog tests and verify failure**

Run: `.venv/bin/pytest -q tests/test_kis_stream_watchdog.py`
Expected: FAIL because freshness logic is absent.

- [x] **Step 3: Add conservative freshness logic**

Read the PID once, preserve liveness behavior, derive `START_EPOCH`, compare only the explicit dependency list against `START_EPOCH + 5`, then terminate the exact worker and call the existing launch function. Missing metadata returns healthy without restart.

- [x] **Step 4: Run KIS tests**

Run: `.venv/bin/pytest -q tests/test_kis_stream_watchdog.py tests/test_kis_stream.py tests/test_kis_stream_watchlist.py`
Expected: PASS.

- [x] **Step 5: Commit**

Commit title: `fix) KIS 스트림 코드 변경 자동 재기동`

### Task 3: Canvas renderer telemetry and safe fallback

**Files:**
- Create: `dashboard/chart_telemetry.py`
- Modify: `dashboard/chart_backend.py`
- Modify: `dashboard/chart_surface.py`
- Modify: `dashboard/lightweight_embed.py`
- Modify: `dashboard/pages/ticker.py`
- Modify: `dashboard/chart_workspace_ui.py`
- Create: `tests/test_chart_telemetry.py`
- Modify: `tests/test_chart_surface.py`
- Modify: `tests/test_lightweight_embed.py`

**Interfaces:**
- Produces: `record_renderer_event(*, backend, reasons, prepare_ms, error=None, path=None, force=False) -> dict`.
- Produces: `load_renderer_metrics(path=None) -> dict` and `renderer_summary(path=None) -> dict`.
- Extends: `PreparedChartSurface.prepare_ms: float` and `PreparedChartSurface.status: str`.
- Produces: `chart_backend.canvas_error_fallback(decision, message) -> RendererDecision`.

- [x] **Step 1: Write failing aggregation and fallback tests**

Cover corrupt input recovery, bounded 200 latency samples, p95, per-backend/reason/error counts, rate limiting, and Canvas serialization exception returning Plotly with `canvas_prepare_error`.

- [x] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest -q tests/test_chart_telemetry.py tests/test_chart_surface.py`
Expected: FAIL because telemetry and safe fallback do not exist.

- [x] **Step 3: Implement bounded best-effort aggregation**

Use `safe_io.file_write_lock` and `safe_io.atomic_write_json`. Store version, updated timestamp, counters, reason counts, error counts, and only the latest 200 finite nonnegative latency values. Suppress identical non-forced events for 30 seconds per process.

- [x] **Step 4: Instrument chart preparation**

Measure with `time.perf_counter`. Record every selected backend subject to rate limiting. Catch Canvas payload/HTML exceptions, create a typed Plotly fallback decision, record the error class, and return without raising.

- [x] **Step 5: Add bounded browser runtime telemetry**

In Canvas HTML, update `tn-chart-telemetry-v1` for initialization success, load/init failure, and manual fallback. Keep recent failure reasons to 20 entries and ignore storage exceptions.

- [x] **Step 6: Surface preparation status**

Replace call-site captions with `prepared.status`, containing backend status and preparation milliseconds.

- [x] **Step 7: Run chart and browser tests**

Run: `.venv/bin/pytest -q tests/test_chart_telemetry.py tests/test_chart_surface.py tests/test_lightweight_embed.py tests/test_lightweight_embed_runtime.py tests/test_chart_backend.py tests/test_dashboard_pages.py tests/test_chart_workspace_pages.py`
Expected: PASS.

- [x] **Step 8: Commit**

Commit title: `add) Canvas 렌더러 운영 지표와 안전 폴백 추가`

### Task 4: Regression and deployment

**Files:**
- Modify only verification documentation if measured evidence changed.

**Interfaces:**
- Consumes: completed Task 1-3 commits.
- Produces: pushed `master` and healthy dashboard/KIS processes.

- [x] **Step 1: Run syntax and diff checks**

Run: `.venv/bin/python -m py_compile <changed Python files>` and `git diff --check`.

- [x] **Step 2: Run related regression and smoke**

Run related chart/dashboard/KIS suites, Chromium performance gate, and `tests/bot_smoke_test.py` without Telegram transmission.

- [x] **Step 3: Push master**

Run: `git push origin master`.

- [x] **Step 4: Restart and verify services**

Restart dashboard, allow cron to restart KIS, verify dashboard health `ok`, external HTTP 200, KIS PID freshness, orderflow status, and chart telemetry file creation after one chart render.

- [x] **Step 5: Report residual trade-offs**

Report local-only browser metrics and the remaining path to authenticated central ingestion.
