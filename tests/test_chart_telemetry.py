from __future__ import annotations

import json

from dashboard import chart_telemetry


def test_renderer_metrics_are_bounded_and_summarized(tmp_path):
    path = tmp_path / "chart-renderer.json"
    for latency in range(205):
        chart_telemetry.record_renderer_event(
            backend="canvas",
            reasons=(),
            prepare_ms=float(latency),
            path=path,
            force=True,
        )
    chart_telemetry.record_renderer_event(
        backend="plotly",
        reasons=("lower_panes", "comparison"),
        prepare_ms=2.5,
        error="ValueError",
        path=path,
        force=True,
    )

    metrics = chart_telemetry.load_renderer_metrics(path)
    summary = chart_telemetry.renderer_summary(path)

    assert metrics["version"] == 1
    assert metrics["totals"] == {"canvas": 205, "plotly": 1}
    assert metrics["reasons"] == {"lower_panes": 1, "comparison": 1}
    assert metrics["errors"] == {"ValueError": 1}
    assert len(metrics["prepare_ms_recent"]) == 200
    assert metrics["prepare_ms_recent"][0] == 6.0
    assert summary["total"] == 206
    assert summary["canvas_share"] == 205 / 206
    assert summary["prepare_p95_ms"] == 194.0


def test_corrupt_metrics_file_recovers_on_next_write(tmp_path):
    path = tmp_path / "chart-renderer.json"
    path.write_text("{broken", encoding="utf-8")

    before = chart_telemetry.load_renderer_metrics(path)
    after = chart_telemetry.record_renderer_event(
        backend="plotly", reasons=("advanced_overlays",), prepare_ms=1.0,
        path=path, force=True,
    )

    assert before["totals"] == {"canvas": 0, "plotly": 0}
    assert after["totals"] == {"canvas": 0, "plotly": 1}
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_identical_events_are_rate_limited_per_process(tmp_path, monkeypatch):
    path = tmp_path / "chart-renderer.json"
    now = [100.0]
    chart_telemetry._LAST_RECORDED.clear()
    monkeypatch.setattr(chart_telemetry.time, "monotonic", lambda: now[0])

    for _ in range(2):
        chart_telemetry.record_renderer_event(
            backend="canvas", reasons=(), prepare_ms=3.0, path=path,
        )
    now[0] += 31.0
    chart_telemetry.record_renderer_event(
        backend="canvas", reasons=(), prepare_ms=4.0, path=path,
    )

    assert chart_telemetry.load_renderer_metrics(path)["totals"]["canvas"] == 2
