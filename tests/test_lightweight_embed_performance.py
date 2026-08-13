from __future__ import annotations

import json
import math
import os
import time
from types import SimpleNamespace

import pandas as pd
import plotly.graph_objects as go
import pytest

from dashboard import chart_document, lightweight_embed


BAR_COUNT = 5_000
P95_LIMIT_SECONDS = 0.050


def _rendered():
    index = pd.date_range("2025-01-01", periods=BAR_COUNT, freq="min", tz="UTC")
    values = [100.0 + index * 0.001 for index in range(BAR_COUNT)]
    frame = pd.DataFrame({
        "Open": values,
        "High": [value + 0.2 for value in values],
        "Low": [value - 0.2 for value in values],
        "Close": [value + 0.05 for value in values],
        "Volume": [10_000.0 + index for index in range(BAR_COUNT)],
    }, index=index)
    return SimpleNamespace(
        frame=frame,
        document=chart_document.default_chart_document("AAPL"),
        figure=go.Figure(),
        transform=SimpleNamespace(x_mode="time"),
    )


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def test_serialize_5000_bars_p95_under_50ms():
    rendered = _rendered()
    lightweight_embed.build_payload(rendered)
    samples = []
    # Forty samples keep p95 representative when CPython's periodic GC lands on
    # one or two otherwise fast payload builds.
    for _ in range(40):
        started = time.perf_counter()
        payload = lightweight_embed.build_payload(rendered)
        samples.append(time.perf_counter() - started)

    assert len(payload["bars"]["time"]) == BAR_COUNT
    assert _p95(samples) < P95_LIMIT_SECONDS, samples


@pytest.mark.skipif(
    os.getenv("RUN_CANVAS_BROWSER_PERF") != "1",
    reason="set RUN_CANVAS_BROWSER_PERF=1 for real Chromium performance gate",
)
def test_lightweight_charts_setdata_5000_bars_p95_under_50ms():
    playwright = pytest.importorskip("playwright.sync_api")
    payload = lightweight_embed.build_payload(_rendered())
    bars = [
        {"time": time_value, "open": open_value, "high": high_value, "low": low_value, "close": close_value}
        for time_value, open_value, high_value, low_value, close_value in zip(
            payload["bars"]["time"], payload["bars"]["open"], payload["bars"]["high"],
            payload["bars"]["low"], payload["bars"]["close"],
        )
    ]
    data = json.dumps(bars, separators=(",", ":"))
    html = (
        "<div id='chart' style='width:1200px;height:700px'></div>"
        f"<script src='{lightweight_embed.LIGHTWEIGHT_CHARTS_URL}'></script>"
    )
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 700})
        page.set_content(html, wait_until="networkidle")
        page.wait_for_function(
            "() => Boolean(window.LightweightCharts && window.LightweightCharts.createChart)",
        )
        samples = page.evaluate(
            """bars => {
              const chart = LightweightCharts.createChart(document.getElementById('chart'), {
                width:1200, height:700, attributionLogo:true
              });
              const series = chart.addSeries(LightweightCharts.CandlestickSeries);
              series.setData(bars);
              const runs=[];
              for (let i=0;i<10;i++) {
                const start=performance.now();
                series.setData(bars);
                runs.push((performance.now()-start)/1000);
              }
              return runs;
            }""",
            json.loads(data),
        )
        browser.close()

    assert _p95(samples) < P95_LIMIT_SECONDS, samples
