# Dashboard runtime hardening design

## Goal

Remove the deprecated Streamlit HTML component path, make the KIS stream restart when its imported code changes, and make Canvas renderer selection and failures observable without weakening the Plotly fallback.

## Scope

### Streamlit iframe migration

- Replace every production `st.components.v1.html(raw_html, height=...)` call with Streamlit 1.58 `st.iframe(raw_html, height=...)`.
- Preserve the existing HTML strings, heights, same-origin watchdog behavior, realtime feeder, and cookie scripts.
- Add a source audit test so the removed API cannot return unnoticed.

### KIS freshness restart

- Keep PID-file liveness as the first watchdog gate.
- Compare the live worker start time with `kis_stream.py` and its direct provider dependencies.
- When a dependency is newer than the worker plus a five-second grace period, terminate the worker and launch one replacement through the existing watchdog path.
- Preserve opt-in and fail-closed behavior. A missing or unreadable start time must not kill a healthy stream.

### Chart telemetry

- Record bounded server-side renderer events in `~/reports/telemetry/chart-renderer.json` or `STOCK_REPORT_REPORTS_DIR/telemetry`.
- Aggregate Canvas/Plotly choices, capability fallback reasons, Canvas preparation errors, and a bounded recent preparation latency window used for p95.
- Rate-limit identical render events per process to avoid Streamlit rerun write amplification.
- If Canvas payload or HTML preparation raises, record the error and return a Plotly decision instead of crashing the page.
- Record browser-only CDN load failures, Canvas initialization failures, successful initializations, and manual Plotly fallbacks in bounded `localStorage`. Browser telemetry remains local because iframe content has no authenticated server write endpoint.

## Data flow

1. `chart_surface.prepare_chart_surface` selects a backend.
2. The server records the decision and preparation latency through `chart_telemetry`.
3. Canvas preparation failure is converted to a typed Plotly fallback and recorded.
4. The Canvas iframe records runtime outcomes locally in the browser.
5. Tests validate persisted aggregation, p95 bounding, fallback behavior, iframe migration, and watchdog freshness.

## Error handling

- Telemetry is best-effort and can never prevent chart rendering.
- Corrupt telemetry files are replaced by a valid empty state on the next write.
- KIS freshness failures degrade to liveness-only behavior.
- Browser telemetry storage failures are ignored; the chart remains usable.

## Trade-offs

- Direct `st.iframe` calls avoid an unnecessary compatibility wrapper but couple call sites to Streamlit 1.58 or newer.
- Server metrics describe render decisions and preparation, while CDN/runtime failures remain browser-local until a secure ingestion endpoint is introduced.
- File aggregation is sufficient for the current single-host deployment; a multi-instance deployment would require a shared metrics backend.

## Verification

- Focused unit tests for all three changes.
- Existing chart, dashboard, KIS, and smoke suites.
- Static audit finds no production `st.components.v1.html` use.
- Restart both dashboard and KIS services and verify health, current PID, telemetry state, and external HTTP 200.
