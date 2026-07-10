"""plotly_embed 드로잉 도구 **런타임** 계약 — node 로 iframe JS 를 실제 실행.

iframe 내부 JS 는 AppTest 로 못 건드리므로(브라우저 전용), Plotly/document 를 스텁한
node 하니스로 IIFE 를 구동해 자석 스냅·피보나치·수평선·측정·지우기·서버도형(평단선) 보호를
검증한다. node 없으면 skip(무네트워크·CI 안전). 순수 계약은 test_plotly_embed.py 가 커버.
"""
import os
import re
import shutil
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("plotly")
import pandas as pd  # noqa: E402

from dashboard import charts, plotly_embed  # noqa: E402

_NODE = shutil.which("node")

# 스텁 하니스 — Plotly.relayout 얕은적용 + gd 이벤트 에뮬레이터로 도구 로직 구동
_HARNESS = r"""
const relayoutCalls = [];
let gd = null;
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, style: {}, innerHTML: "", _h: {}, _s: new Set(id === "bt-mag" ? ["on"] : []),
    classList: { toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); }, get _set() { return null; } },
    on(e, f) { this._h[e] = f; }, emit(e, p) { if (this._h[e]) this._h[e](p); },
    getBoundingClientRect() { return { top: 0 }; } };
  els[id].classList._s = els[id]._s;
  if (id === "chart") gd = els[id];
  return els[id];
}
global.document = { getElementById: el };
global.window = { frameElement: null, parent: { innerHeight: 900, addEventListener() {} } };
global.performance = { now: () => 1 };
global.requestAnimationFrame = () => null;
global.Plotly = {
  newPlot(g, d, l, c) { g.data = d; g.layout = l; return { then(cb) { cb(); return this; } }; },
  relayout(g, u) { relayoutCalls.push(u);
    for (const k of Object.keys(u)) if (!k.includes(".") && !k.includes("[")) g.layout[k] = u[k];
    return { then(cb) { cb(); return this; } }; },
};
__SCRIPT__
const iso = (d) => new Date(d).toISOString();
const D0 = Date.parse("2025-02-01"), D1 = Date.parse("2025-03-01");
const BASE = JSON.parse(JSON.stringify(gd.layout.shapes || []));
if (!BASE.length) { console.error("NO_SERVER_SHAPES"); process.exit(2); }
const append = (a) => { gd.layout.shapes = BASE.concat(a); return gd.layout.shapes; };
function fail(m) { console.error("FAIL " + m); process.exit(1); }

// 0) 서버 도형(평단·현재가선) 자석 불변
for (let i = 0; i < BASE.length; i++) {
  if ((BASE[i].yref || "y") !== "y" || BASE[i].type !== "line") continue;
  gd.layout.shapes = JSON.parse(JSON.stringify(BASE));
  const b = gd.layout.shapes[i].y0;
  gd.emit("plotly_relayout", { ["shapes[" + i + "].y0"]: b });
  if (gd.layout.shapes[i].y0 !== b) fail("server_shape_moved " + i);
}
// 1) 자석 — 봉 OHLC 로 정수 스냅
append([{ type: "line", xref: "x", yref: "y", x0: iso(D0 + 3e5), y0: 131.4, x1: iso(D1 + 3e5), y1: 158.2 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
let sh = gd.layout.shapes[gd.layout.shapes.length - 1];
if (sh.y0 !== Math.round(sh.y0) || sh.y1 !== Math.round(sh.y1)) fail("magnet_snap " + sh.y0 + "," + sh.y1);
// 2) 피보나치 — 레벨 7 + 밴드 6
el("bt-fib").onclick();
append([{ type: "line", xref: "x", yref: "y", x0: iso(D0), y0: 130, x1: iso(D1), y1: 160 }]);
gd.layout.annotations = [];
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
if (gd.layout.shapes.filter(s => s.name === "tool-fib" && s.type === "line").length !== 7) fail("fib_lines");
if (gd.layout.shapes.filter(s => s.name === "tool-fib" && s.type === "rect").length !== 6) fail("fib_bands");
// 3) 수평선 — paper 전폭 + 라벨
el("bt-hline").onclick();
append(gd.layout.shapes.slice(BASE.length).concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 140.3, x1: iso(D0 + 864e5), y1: 141 }]));
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const hl = gd.layout.shapes.filter(s => s.name === "tool-hline");
if (hl.length !== 1 || hl[0].xref !== "paper") fail("hline");
if ((gd.layout.annotations || []).filter(a => a.name === "tool-hline").length !== 1) fail("hline_ann");
// 4) 측정 — rect 1 + 통계
el("bt-meas").onclick();
append(gd.layout.shapes.slice(BASE.length).concat([{ type: "rect", xref: "x", yref: "y",
  x0: iso(D0), y0: 120, x1: iso(D1), y1: 150 }]));
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const mAnn = (gd.layout.annotations || []).filter(a => a.name === "tool-meas");
if (gd.layout.shapes.filter(s => s.name === "tool-meas").length !== 1 || !mAnn.length
    || mAnn[0].text.indexOf("봉") < 0) fail("measure");
// 5) 지우기 — 서버 도형만 정확 복원
el("bt-clear").onclick();
if (JSON.stringify(gd.layout.shapes) !== JSON.stringify(BASE)) fail("clear_restore");
if ((gd.layout.annotations || []).some(a => String(a.name || "").startsWith("tool-"))) fail("clear_ann");
console.log("OK");
"""


@pytest.mark.skipif(_NODE is None, reason="node 미설치 — 런타임 JS 검증 스킵")
def test_drawing_tools_runtime(tmp_path):
    idx = pd.date_range("2025-01-01", periods=70, freq="D")
    df = pd.DataFrame({"Open": range(100, 170), "High": range(101, 171),
                       "Low": range(99, 169), "Close": range(100, 170),
                       "Volume": [1e6] * 70}, index=idx)
    fig = charts.price_chart(df, "TEST", kind="candle", show_volume=True,
                             show_rsi=True, avg_cost=140.0)
    html = plotly_embed.pannable_chart_html(fig, df, height=460, view_days=90,
                                            vol_axis="yaxis2")
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    runner = tmp_path / "run.js"
    runner.write_text(_HARNESS.replace("__SCRIPT__", js), encoding="utf-8")
    r = subprocess.run([_NODE, str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"runtime fail: {r.stdout}\n{r.stderr}"
    assert "OK" in r.stdout
