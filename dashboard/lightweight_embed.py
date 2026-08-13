"""Bounded Lightweight Charts payloads and standalone Streamlit component HTML."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


PAYLOAD_VERSION = 1
MAX_INITIAL_BARS = 20_000
LIGHTWEIGHT_CHARTS_VERSION = "5.1.0"
LIGHTWEIGHT_CHARTS_URL = (
    f"https://unpkg.com/lightweight-charts@{LIGHTWEIGHT_CHARTS_VERSION}/"
    "dist/lightweight-charts.standalone.production.js"
)
_SERIES_TYPES = {
    "line": "line",
    "area": "area",
    "baseline": "baseline",
    "candlestick": "candlestick",
    "hollow_candle": "candlestick",
    "heikin_ashi": "candlestick",
    "bars": "bar",
    "high_low": "bar",
}
_UP = "#26a69a"
_DOWN = "#ef5350"


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _epoch_seconds(value: Any) -> int | None:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return int(timestamp.timestamp())


def _clean_frame(frame: pd.DataFrame, max_bars: int) -> tuple[pd.DataFrame, int, bool]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("no finite bars for Canvas renderer")
    required = ["Open", "High", "Low", "Close"]
    if any(column not in frame for column in required):
        raise ValueError("Canvas renderer requires OHLC columns")
    cleaned = frame.copy(deep=False)
    cleaned = cleaned.assign(CanvasTime=[_epoch_seconds(value) for value in cleaned.index])
    for column in required:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    finite = cleaned[required].apply(
        lambda column: column.map(lambda value: math.isfinite(float(value)) if pd.notna(value) else False),
    ).all(axis=1)
    cleaned = cleaned[cleaned["CanvasTime"].notna() & finite]
    source_bar_count = len(cleaned)
    if cleaned.empty:
        raise ValueError("no finite bars for Canvas renderer")
    cleaned = cleaned.sort_values("CanvasTime", kind="stable").drop_duplicates("CanvasTime", keep="last")
    limit = max(1, min(int(max_bars), MAX_INITIAL_BARS))
    truncated = len(cleaned) > limit
    if truncated:
        cleaned = cleaned.tail(limit)
    return cleaned, source_bar_count, truncated


def _color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text.startswith(("#", "rgb", "hsl")) else fallback


def _trace_axis(trace: Any) -> str:
    return str(getattr(trace, "yaxis", None) or "y")


def _trace_values(trace: Any, name: str) -> Any:
    values = getattr(trace, name, None)
    return () if values is None else values


def _line_overlays(figure: Any, valid_times: set[int]) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    traces = list(getattr(figure, "data", ()) or ())
    for trace in traces:
        if getattr(trace, "type", "") != "scatter" or _trace_axis(trace) != "y":
            continue
        mode = str(getattr(trace, "mode", None) or "lines")
        if "lines" not in mode or "markers" in mode:
            continue
        points: list[dict[str, Any]] = []
        for raw_time, raw_value in zip(_trace_values(trace, "x"), _trace_values(trace, "y")):
            timestamp = _epoch_seconds(raw_time)
            value = _finite(raw_value)
            if timestamp in valid_times and value is not None:
                points.append({"time": timestamp, "value": value})
        if not points:
            continue
        line = getattr(trace, "line", None)
        overlays.append({
            "name": str(getattr(trace, "name", None) or "보조선"),
            "color": _color(getattr(line, "color", None), "#8b949e"),
            "width": _finite(getattr(line, "width", None)) or 1.0,
            "data": points,
        })
    return overlays


def _custom_item(customdata: Any, index: int) -> Any:
    if customdata is None:
        return None
    try:
        return customdata[index]
    except (IndexError, KeyError, TypeError):
        return None


def _marker_id(custom: Any, fallback: str) -> str:
    if isinstance(custom, Mapping):
        return str(custom.get("event_id") or custom.get("id") or fallback)
    if isinstance(custom, Sequence) and not isinstance(custom, (str, bytes)) and custom:
        return str(custom[0] or fallback)
    return fallback


def _markers(figure: Any, valid_times: set[int], *, compact: bool) -> list[dict[str, Any]]:
    if compact:
        return []
    definitions = {
        "buy": ("belowBar", "arrowUp", _UP),
        "sell": ("aboveBar", "arrowDown", _DOWN),
        "alert": ("inBar", "circle", "#f59e0b"),
    }
    markers: list[dict[str, Any]] = []
    for trace_index, trace in enumerate(list(getattr(figure, "data", ()) or ())):
        mode = str(getattr(trace, "mode", None) or "")
        if getattr(trace, "type", "") != "scatter" or "markers" not in mode:
            continue
        name = str(getattr(trace, "name", None) or "event")
        key = name.lower()
        position, shape, color = definitions.get(key, ("inBar", "circle", "#8b949e"))
        customdata = getattr(trace, "customdata", None)
        texts = getattr(trace, "text", None)
        for point_index, raw_time in enumerate(_trace_values(trace, "x")):
            timestamp = _epoch_seconds(raw_time)
            if timestamp not in valid_times:
                continue
            custom = _custom_item(customdata, point_index)
            text = _custom_item(texts, point_index) or name
            markers.append({
                "id": _marker_id(custom, f"marker-{trace_index}-{point_index}"),
                "time": timestamp,
                "position": position,
                "shape": shape,
                "color": color,
                "text": str(text)[:80],
            })
    return sorted(markers, key=lambda row: (row["time"], row["id"]))


def _price_lines(figure: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    layout = getattr(figure, "layout", None)
    for index, shape in enumerate(list(getattr(layout, "shapes", ()) or ())):
        y0 = _finite(getattr(shape, "y0", None))
        y1 = _finite(getattr(shape, "y1", None))
        if y0 is None or y1 is None or abs(y0 - y1) > 1e-12:
            continue
        line = getattr(shape, "line", None)
        lines.append({
            "id": str(getattr(shape, "name", None) or f"line-{index}"),
            "price": y0,
            "color": _color(getattr(line, "color", None), "#8b949e"),
            "width": _finite(getattr(line, "width", None)) or 1.0,
            "style": str(getattr(line, "dash", None) or "solid"),
        })
    return lines


def build_payload(rendered: Any, *, compact: bool = False, max_bars: int = MAX_INITIAL_BARS) -> dict[str, Any]:
    """Serialize one compatible RenderedChart into finite bounded JSON data."""
    transform = getattr(rendered, "transform", None)
    if str(getattr(transform, "x_mode", "time")) != "time":
        raise ValueError("Canvas renderer supports time-based charts only")
    document = getattr(rendered, "document", None)
    if not isinstance(document, Mapping):
        raise ValueError("Canvas renderer requires a ChartDocument")
    chart_type = str((document.get("chart") or {}).get("type") or "")
    if chart_type not in _SERIES_TYPES:
        raise ValueError(f"unsupported Canvas chart type: {chart_type}")
    frame, source_bar_count, truncated = _clean_frame(getattr(rendered, "frame", None), max_bars)
    valid_times = {int(value) for value in frame["CanvasTime"]}
    bars = [{
        "time": int(row.CanvasTime),
        "open": float(row.Open),
        "high": float(row.High),
        "low": float(row.Low),
        "close": float(row.Close),
    } for row in frame.itertuples()]
    volume: list[dict[str, Any]] = []
    if "Volume" in frame:
        for row in frame.itertuples():
            value = _finite(getattr(row, "Volume", None))
            if value is not None and value >= 0:
                volume.append({
                    "time": int(row.CanvasTime),
                    "value": value,
                    "color": "rgba(38,166,154,.38)" if row.Close >= row.Open else "rgba(239,83,80,.38)",
                })
    figure = getattr(rendered, "figure", None)
    payload = {
        "version": PAYLOAD_VERSION,
        "symbol": str(document.get("symbol") or ""),
        "timezone": str(document.get("timezone") or "UTC"),
        "series_type": _SERIES_TYPES[chart_type],
        "chart_type": chart_type,
        "log_scale": (document.get("scale") or {}).get("type") == "log",
        "bars": bars,
        "volume": volume,
        "overlays": _line_overlays(figure, valid_times),
        "markers": _markers(figure, valid_times, compact=compact),
        "price_lines": _price_lines(figure),
        "source_bar_count": source_bar_count,
        "truncated": truncated,
        "compact": bool(compact),
    }
    json.dumps(payload, allow_nan=False)
    return payload


_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body,#wrap{margin:0;width:100%;height:100%;overflow:hidden;background:transparent;color:@@TEXT@@;font:12px system-ui,sans-serif}
#wrap{position:relative}#chart{position:absolute;inset:28px 0 0}.compact #chart{inset:0}
#readout{position:absolute;left:10px;top:6px;z-index:2;color:@@TEXT@@;white-space:nowrap;pointer-events:none}
.compact #readout{display:none}#error{display:none;padding:18px;color:@@TEXT@@}
button{border:1px solid @@GRID@@;background:@@PANEL@@;color:@@TEXT@@;padding:7px 10px;border-radius:4px;cursor:pointer}
</style>
<script src="@@CDN@@"></script></head><body>
<div id="wrap"><div id="readout"></div><div id="chart"></div><div id="error"><p id="error-text"></p><button id="fallback">Plotly로 다시 열기</button></div></div>
<script>
(() => {
  "use strict";
  const payload = @@PAYLOAD@@;
  const storeKey = @@STORE_KEY@@;
  const rendererKey = @@RENDERER_KEY@@;
  const rangeSyncKey = @@RANGE_SYNC_KEY@@;
  const live = @@LIVE@@;
  const compact = @@COMPACT@@;
  const root = document.getElementById("wrap");
  const chartEl = document.getElementById("chart");
  const readout = document.getElementById("readout");
  const error = document.getElementById("error");
  const errorText = document.getElementById("error-text");
  if (compact) root.classList.add("compact");
  function fail(message) {
    chartEl.style.display = "none";
    error.style.display = "block";
    errorText.textContent = message || "고성능 차트를 불러오지 못했습니다.";
  }
  document.getElementById("fallback").addEventListener("click", () => {
    try { localStorage.setItem(rendererKey, "plotly"); } catch (_) {}
    window.parent.postMessage({type:"tn-renderer-fallback", renderer:"plotly", key:storeKey}, "*");
  });
  const loadTimer = setTimeout(() => {
    if (!window.LightweightCharts) fail("Canvas 라이브러리 로드 실패");
  }, 5000);
  if (!window.LightweightCharts) return;
  clearTimeout(loadTimer);
  try {
    const LC = window.LightweightCharts;
    const chart = LightweightCharts.createChart(chartEl, {
      width: Math.max(1, chartEl.clientWidth), height: Math.max(1, chartEl.clientHeight),
      attributionLogo: true,
      layout: {background:{type:"solid",color:"transparent"},textColor:@@TEXT_JSON@@},
      grid: {vertLines:{color:@@GRID_JSON@@},horzLines:{color:@@GRID_JSON@@}},
      rightPriceScale: {borderColor:@@GRID_JSON@@,mode:payload.log_scale ? 1 : 0},
      timeScale: {borderColor:@@GRID_JSON@@,timeVisible:true,secondsVisible:false,rightOffset:3},
      crosshair: {mode:1},
      handleScroll: true, handleScale: true,
    });
    const options = {priceLineVisible:false,lastValueVisible:true};
    let series;
    if (payload.series_type === "line") {
      series = chart.addSeries(LightweightCharts.LineSeries, {...options,color:"#2f81f7",lineWidth:2});
      series.setData(payload.bars.map(b => ({time:b.time,value:b.close})));
    } else if (payload.series_type === "area") {
      series = chart.addSeries(LightweightCharts.AreaSeries, {...options,lineColor:"#2f81f7",topColor:"rgba(47,129,247,.30)",bottomColor:"rgba(47,129,247,.02)"});
      series.setData(payload.bars.map(b => ({time:b.time,value:b.close})));
    } else if (payload.series_type === "baseline") {
      series = chart.addSeries(LightweightCharts.BaselineSeries, {...options,baseValue:{type:"price",price:payload.bars[0].close}});
      series.setData(payload.bars.map(b => ({time:b.time,value:b.close})));
    } else if (payload.series_type === "bar") {
      series = chart.addSeries(LightweightCharts.BarSeries, {...options,upColor:"#26a69a",downColor:"#ef5350"});
      series.setData(payload.bars);
    } else {
      series = chart.addSeries(LightweightCharts.CandlestickSeries, {...options,upColor:"#26a69a",downColor:"#ef5350",borderVisible:false,wickUpColor:"#26a69a",wickDownColor:"#ef5350"});
      series.setData(payload.bars);
    }
    if (payload.volume.length) {
      const volume = chart.addSeries(LightweightCharts.HistogramSeries, {priceScaleId:"volume",priceFormat:{type:"volume"},lastValueVisible:false,priceLineVisible:false});
      volume.priceScale().applyOptions({scaleMargins:{top:.80,bottom:0}});
      volume.setData(payload.volume);
    }
    for (const overlay of payload.overlays) {
      const item = chart.addSeries(LightweightCharts.LineSeries, {color:overlay.color,lineWidth:Math.max(1,Math.min(4,overlay.width)),priceLineVisible:false,lastValueVisible:false});
      item.setData(overlay.data);
    }
    if (payload.markers.length && LightweightCharts.createSeriesMarkers) {
      LightweightCharts.createSeriesMarkers(series, payload.markers.map(m => ({time:m.time,position:m.position,color:m.color,shape:m.shape,text:m.text})));
    }
    const lineStyles = {solid:0,dot:1,dash:2,longdash:3,dashdot:4};
    for (const line of payload.price_lines) {
      series.createPriceLine({price:line.price,color:line.color,lineWidth:Math.max(1,Math.min(4,line.width)),lineStyle:lineStyles[line.style] || 0,axisLabelVisible:true,title:line.id === "tn-last" ? "" : line.id});
    }
    const byTime = new Map(payload.bars.map(b => [b.time,b]));
    function showBar(bar) {
      if (!bar) return;
      readout.textContent = `${payload.symbol}  O ${bar.open.toLocaleString()}  H ${bar.high.toLocaleString()}  L ${bar.low.toLocaleString()}  C ${bar.close.toLocaleString()}`;
    }
    showBar(payload.bars[payload.bars.length - 1]);
    chart.subscribeCrosshairMove(param => showBar(byTime.get(Number(param.time))));
    const resize = new ResizeObserver(entries => {
      const box = entries[0] && entries[0].contentRect;
      if (box) chart.applyOptions({width:Math.max(1,Math.floor(box.width)),height:Math.max(1,Math.floor(box.height))});
    });
    resize.observe(chartEl);
    chart.timeScale().fitContent();
    let applyingRange = false;
    const syncStorageKey = rangeSyncKey ? "tnrange-lwc:" + rangeSyncKey : null;
    if (syncStorageKey) {
      chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (applyingRange || !range) return;
        try { localStorage.setItem(syncStorageKey, JSON.stringify({from:range.from,to:range.to,src:storeKey,ts:Date.now()})); } catch (_) {}
      });
    }
    function applyRemoteRange(raw) {
      if (!raw || !syncStorageKey) return;
      try {
        const value = JSON.parse(raw);
        if (!value || value.src === storeKey || !Number.isFinite(value.from) || !Number.isFinite(value.to)) return;
        applyingRange = true;
        chart.timeScale().setVisibleLogicalRange({from:value.from,to:value.to});
        queueMicrotask(() => { applyingRange = false; });
      } catch (_) {}
    }
    const liveKey = "tnrt:" + payload.symbol.split(":")[0];
    let lastBar = {...payload.bars[payload.bars.length - 1]};
    function applyLive(raw) {
      if (!live || !raw) return;
      try {
        const value = JSON.parse(raw), price = Number(value.p);
        if (!(price > 0) || !Number.isFinite(price)) return;
        lastBar = {...lastBar,high:Math.max(lastBar.high,price),low:Math.min(lastBar.low,price),close:price};
        if (["line","area","baseline"].includes(payload.series_type)) series.update({time:lastBar.time,value:price});
        else series.update(lastBar);
        showBar(lastBar);
      } catch (_) {}
    }
    window.addEventListener("storage", event => {
      if (event.key === syncStorageKey) applyRemoteRange(event.newValue);
      if (event.key === liveKey) applyLive(event.newValue);
    });
    if (live) { try { applyLive(localStorage.getItem(liveKey)); } catch (_) {} }
    window.__tnCanvasChart = {chart,series,payload,applyLive,applyRemoteRange};
  } catch (err) {
    fail("Canvas 차트 초기화 실패: " + String(err && err.message || err));
  }
})();
</script></body></html>"""


def lightweight_chart_html(
    payload: Mapping[str, Any],
    *,
    height: int = 460,
    store_key: str | None = None,
    range_sync_key: str | None = None,
    live: bool = False,
    light: bool = False,
) -> str:
    """Return a self-contained component document for a validated payload."""
    if not isinstance(payload, Mapping) or payload.get("version") != PAYLOAD_VERSION:
        raise ValueError("invalid Canvas payload")
    if not payload.get("bars"):
        raise ValueError("Canvas payload contains no bars")
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    encoded = encoded.replace("</", "<\\/")
    text = "#24292f" if light else "#9aa7bd"
    grid = "#d0d7de" if light else "#283142"
    panel = "#ffffff" if light else "#111827"
    return (_HTML
            .replace("@@CDN@@", LIGHTWEIGHT_CHARTS_URL)
            .replace("@@PAYLOAD@@", encoded)
            .replace("@@STORE_KEY@@", json.dumps(store_key))
            .replace("@@RENDERER_KEY@@", json.dumps("tnrenderer:" + str(store_key or payload.get("symbol") or "chart")))
            .replace("@@RANGE_SYNC_KEY@@", json.dumps(range_sync_key))
            .replace("@@LIVE@@", json.dumps(bool(live)))
            .replace("@@COMPACT@@", json.dumps(bool(payload.get("compact"))))
            .replace("@@TEXT_JSON@@", json.dumps(text))
            .replace("@@GRID_JSON@@", json.dumps(grid))
            .replace("@@TEXT@@", text)
            .replace("@@GRID@@", grid)
            .replace("@@PANEL@@", panel)
            .replace("@@HEIGHT@@", str(max(120, int(height)))))
