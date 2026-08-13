from __future__ import annotations

import re
import shutil
import subprocess

import pandas as pd
import pytest

from dashboard import chart_document, chart_renderer, lightweight_embed


NODE = shutil.which("node")


def _inline_script() -> str:
    index = pd.date_range("2025-01-01", periods=30, freq="h", tz="UTC")
    frame = pd.DataFrame({
        "Open": [100.0 + i for i in range(30)],
        "High": [101.0 + i for i in range(30)],
        "Low": [99.0 + i for i in range(30)],
        "Close": [100.5 + i for i in range(30)],
        "Volume": [1_000.0 + i for i in range(30)],
    }, index=index)
    rendered = chart_renderer.render_plotly_chart(
        chart_document.default_chart_document("AAPL"), frame,
        chart_kwargs={"mas": (5,), "show_volume": True},
    )
    html = lightweight_embed.lightweight_chart_html(
        lightweight_embed.build_payload(rendered), height=420,
        store_key="AAPL:1h:lin:candlestick", range_sync_key="layout:range", live=True,
    )
    return re.findall(r"<script>(.*?)</script>", html, re.S)[-1]


_HARNESS = r"""
function check(value, message) { if (!value) { throw new Error(message); } }
const elements = {};
function element(id) {
  if (!elements[id]) elements[id] = {
    id, style:{}, textContent:"", clientWidth:900, clientHeight:390,
    classList:{add(v){this.value=v;}}, handlers:{},
    addEventListener(name, cb){this.handlers[name]=cb;},
  };
  return elements[id];
}
const storage = {};
const storageHandlers = [];
global.window = global;
global.document = {getElementById: element};
global.localStorage = {
  setItem(k,v){storage[k]=String(v);}, getItem(k){return storage[k] || null;}
};
window.parent = {postMessage(value){window.lastMessage=value;}};
window.addEventListener = (name, cb) => {if(name === "storage") storageHandlers.push(cb);};
global.queueMicrotask = cb => cb();
global.setTimeout = cb => { window.pendingTimeout = cb; return 1; };
global.clearTimeout = () => {};
global.ResizeObserver = class { constructor(cb){this.cb=cb;} observe(){this.cb([{contentRect:{width:777,height:333}}]);} };
const allSeries = [];
const chartState = {rangeCb:null,range:null,options:[],crosshair:null};
function makeSeries(type) {
  const state = {type,data:[],updates:[],lines:[],scaleOptions:[]};
  const api = {
    state,
    setData(rows){state.data=rows;},
    update(row){state.updates.push(row);},
    createPriceLine(row){state.lines.push(row);},
    priceScale(){return {applyOptions(row){state.scaleOptions.push(row);}};},
  };
  allSeries.push(api); return api;
}
const chart = {
  addSeries(type){return makeSeries(type);},
  applyOptions(value){chartState.options.push(value);},
  subscribeCrosshairMove(cb){chartState.crosshair=cb;},
  timeScale(){return {
    fitContent(){chartState.fit=true;},
    subscribeVisibleLogicalRangeChange(cb){chartState.rangeCb=cb;},
    setVisibleLogicalRange(value){chartState.range=value;},
  };},
};
global.LightweightCharts = {
  CandlestickSeries:"candlestick", BarSeries:"bar", LineSeries:"line",
  AreaSeries:"area", BaselineSeries:"baseline", HistogramSeries:"histogram",
  createChart(){return chart;},
  createSeriesMarkers(series, markers){series.state.markers=markers;},
};
__SCRIPT__
check(window.__tnCanvasChart, "canvas api missing");
check(window.__tnCanvasChart.series.state.data.length === 30, "initial bars missing");
check(allSeries.length >= 3, "volume or overlay series missing");
check(chartState.options.some(v => v.width === 777 && v.height === 333), "resize not applied");
window.__tnCanvasChart.applyLive(JSON.stringify({p:155.25,w:Date.now()}));
check(window.__tnCanvasChart.series.state.updates.length === 1, "live update missing");
check(window.__tnCanvasChart.series.state.updates[0].close === 155.25, "live close wrong");
chartState.rangeCb({from:2,to:9});
const rangeKey = "tnrange-lwc:layout:range";
check(JSON.parse(storage[rangeKey]).from === 2, "range not published");
storageHandlers[0]({key:rangeKey,newValue:JSON.stringify({from:4,to:12,src:"other",ts:1})});
check(chartState.range.from === 4 && chartState.range.to === 12, "remote range not applied");
check(element("readout").textContent.includes("AAPL"), "readout missing");
console.log("OK canvas-runtime");
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_canvas_runtime_initializes_updates_and_synchronizes(tmp_path):
    runner = tmp_path / "canvas-runtime.js"
    runner.write_text(_HARNESS.replace("__SCRIPT__", _inline_script()), encoding="utf-8")

    result = subprocess.run([NODE, str(runner)], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "OK canvas-runtime" in result.stdout


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_canvas_runtime_missing_library_shows_plotly_fallback(tmp_path):
    script = _inline_script()
    harness = r"""
const elements = {};
function element(id) { return elements[id] || (elements[id]={style:{},textContent:"",handlers:{},classList:{add(){}},addEventListener(n,cb){this.handlers[n]=cb;}}); }
global.window=global;
global.document={getElementById:element};
global.localStorage={values:{},setItem(k,v){this.values[k]=v;}};
window.parent={postMessage(v){window.message=v;}};
window.addEventListener=()=>{};
global.setTimeout=cb=>{cb();return 1;};
global.clearTimeout=()=>{};
__SCRIPT__
if (element("error").style.display !== "block") throw new Error("missing error state");
if (!element("error-text").textContent.includes("로드 실패")) throw new Error("missing error copy");
element("fallback").handlers.click();
if (localStorage.values["tnrenderer:AAPL:1h:lin:candlestick"] !== "plotly") throw new Error("fallback preference missing");
if (!window.message || window.message.renderer !== "plotly") throw new Error("fallback message missing");
console.log("OK canvas-fallback");
"""
    runner = tmp_path / "canvas-fallback.js"
    runner.write_text(harness.replace("__SCRIPT__", script), encoding="utf-8")

    result = subprocess.run([NODE, str(runner)], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "OK canvas-fallback" in result.stdout
