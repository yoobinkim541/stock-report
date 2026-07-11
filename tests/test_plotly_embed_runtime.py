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
    appendChild() {}, addEventListener() {}, querySelector() { return null; },
    getBoundingClientRect() { return { top: 0 }; } };
  els[id].classList._s = els[id]._s;
  if (id === "chart") gd = els[id];
  return els[id];
}
global.document = { getElementById: el,
                    createElement: () => ({ style: {}, textContent: "" }) };
global.window = { frameElement: null, parent: { innerHeight: 900, addEventListener() {} } };
global.performance = { now: () => 1 };
global.requestAnimationFrame = () => null;
global.Plotly = {
  newPlot(g, d, l, c) { g.data = d; g.layout = l; return { then(cb) { cb(); return this; } }; },
  relayout(g, u) { relayoutCalls.push(u);
    for (const k of Object.keys(u)) if (!k.includes(".") && !k.includes("[")) g.layout[k] = u[k];
    return { then(cb) { cb(); return this; } }; },
};
// localStorage 스텁 + setTimeout 동기화(디바운스 저장 즉시 flush) — 영속화 검증용
const _ls = {};
global.localStorage = { getItem: (k) => (k in _ls ? _ls[k] : null),
                        setItem: (k, v) => { _ls[k] = String(v); },
                        removeItem: (k) => { delete _ls[k]; } };
global.setTimeout = (fn) => { fn(); return 0; };
global.clearTimeout = () => {};
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
// 4.5) 신규 도구 — 수직선·크로스·레이·롱 포지션 (TV 확장 도구 세트 · 자석 OFF 경로)
el("bt-mag").onclick({ target: el("bt-mag") });   // 자석 OFF — 원값 그대로 검증
el("bt-vline").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D0 + 864e5), y1: 131 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const vl = gd.layout.shapes.filter((s) => s.name === "tool-vline");
if (vl.length !== 1 || vl[0].yref !== "paper" || vl[0].x0 !== vl[0].x1) fail("vline");
el("bt-cross").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 140, x1: iso(D0 + 864e5), y1: 141 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
if (gd.layout.shapes.filter((s) => s.name === "tool-cross").length !== 2) fail("cross_pair");
el("bt-ray").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D1), y1: 150 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const ray = gd.layout.shapes.filter((s) => s.name === "tool-ray");
if (ray.length !== 1) fail("ray");
if (Date.parse(ray[0].x1) <= Date.parse(iso(D1))) fail("ray_not_extended");   // 우측 연장
el("bt-long").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "rect", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D1), y1: 150 }]);   // 진입 130 → 목표 150
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const pos = gd.layout.shapes.filter((s) => s.name === "tool-pos");
if (pos.length !== 2) fail("pos_zones " + pos.length);
const stopZone = pos.find((s) => Math.min(s.y0, s.y1) < 130);
if (!stopZone || Math.abs(Math.min(stopZone.y0, stopZone.y1) - 110) > 1e-6)
  fail("pos_rr_stop " + JSON.stringify(pos.map((s) => [s.y0, s.y1])));        // RR 1:1 → 110
const posAnn = (gd.layout.annotations || []).filter((a) => a.name === "tool-pos");
if (posAnn.length !== 1 || !/롱/.test(posAnn[0].text) || !/RR 1:1/.test(posAnn[0].text))
  fail("pos_ann");
if (/NaN/.test(posAnn[0].text)) fail("pos_nan_label");
// 방향 가드 — 롱인데 아래로 드래그(진입 150→목표 130) = 도형 폐기·도구 유지 (넌센스 방지)
el("bt-long").onclick();
const nBefore = gd.layout.shapes.filter((s) => s.name === "tool-pos").length;
gd.layout.shapes = gd.layout.shapes.concat([{ type: "rect", xref: "x", yref: "y",
  x0: iso(D0), y0: 150, x1: iso(D1), y1: 130 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
if (gd.layout.shapes.filter((s) => s.name === "tool-pos").length !== nBefore)
  fail("pos_direction_guard");
el("bt-long").onclick();                          // 도구 해제 (가드는 도구 유지가 정상)
el("bt-mag").onclick({ target: el("bt-mag") });   // 자석 복원 (후속 영속화 테스트는 스냅 전제)

// 5) 지우기 — 서버 도형만 정확 복원 + 저장소도 클리어
el("bt-clear").onclick();
if (JSON.stringify(gd.layout.shapes) !== JSON.stringify(BASE)) fail("clear_restore");
if ((gd.layout.annotations || []).some(a => String(a.name || "").startsWith("tool-"))) fail("clear_ann");
if (Object.keys(_ls).length) fail("clear_storage " + JSON.stringify(_ls));
// 6) 영속화 — 새 선(자석 스냅) → localStorage 저장 → 재로드(스크립트 재실행) 시 복원
append([{ type: "line", xref: "x", yref: "y", x0: iso(D0), y0: 130, x1: iso(D1), y1: 150 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const savedKeys = Object.keys(_ls);
if (savedKeys.length !== 1 || !savedKeys[0].startsWith("tndraw:")) fail("persist_save " + savedKeys);
const savedDoc = JSON.parse(_ls[savedKeys[0]]);
if (savedDoc.v !== 1 || savedDoc.shapes.length !== 1) fail("persist_doc");
// 뷰 위치 복원 — plotly 원문(naive 문자열) 그대로 왕복해야 함 (Date 재직렬화 = KST −9h 밀림)
const NAIVE = ["2025-02-01 12:00:00", "2025-03-01 12:00:00"];
_ls["tnview:TEST:1d:lin"] = JSON.stringify({view: NAIVE, ts: Date.now(), vm: 90 * 864e5});
relayoutCalls.length = 0;
for (const k of Object.keys(els)) delete els[k];   // 새 세션 모사 — DOM 리셋·storage 유지
gd = null;
__SCRIPT__
if (!gd || !gd.layout) fail("reload_gd");
const vr = relayoutCalls.find((u) => u["xaxis.range"]);
if (!vr) fail("view_restore_missing");
if (vr["xaxis.range"][0] !== NAIVE[0] || vr["xaxis.range"][1] !== NAIVE[1])
  fail("view_restore_reserialized " + JSON.stringify(vr["xaxis.range"]));
const nRestored = (gd.layout.shapes || []).length - BASE.length;
if (nRestored !== 1) fail("persist_restore n=" + nRestored);
const rs = gd.layout.shapes[gd.layout.shapes.length - 1];
if (rs.y0 !== Math.round(rs.y0)) fail("persist_snapped_coords");   // 저장 전 스냅값 유지

// 6.5) 잔여 백로그 도구 — 채널(2단계)·피치포크·갠 팬·피보확장·타임존·순환선·엘리엇
el("bt-mag").onclick({ target: el("bt-mag") });   // 자석 OFF — 좌표 기대값을 정확히 검증
el("bt-chan").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D1), y1: 150 }]);      // ① 기준선
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
if (gd.layout.shapes.filter((s) => s.name === "tool-chan").length !== 1) fail("chan_base");
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 140, x1: iso(D0 + 864e5), y1: 141 }]);   // ② 폭 (오프셋 +10)
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const chan = gd.layout.shapes.filter((s) => s.name === "tool-chan");
if (chan.length !== 3) fail("chan_lines " + chan.length);   // 기준+평행+중간
const par = chan[1];
if (Math.abs((par.y0 - chan[0].y0) - (par.y1 - chan[0].y1)) > 1e-9) fail("chan_parallel");
el("bt-fork").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 120, x1: iso(D0 + 10 * 864e5), y1: 140 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0 + 20 * 864e5), y0: 125, x1: iso(D0 + 21 * 864e5), y1: 126 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
if (gd.layout.shapes.filter((s) => s.name === "tool-fork").length !== 3) fail("fork_tines");
el("bt-gann").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D1), y1: 150 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
if (gd.layout.shapes.filter((s) => s.name === "tool-gann").length !== 9) fail("gann_rays");
el("bt-fibext").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D0 + 10 * 864e5), y1: 150 }]);   // A→B (+20)
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0 + 15 * 864e5), y0: 140, x1: iso(D0 + 16 * 864e5), y1: 141 }]);  // C=140
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const fx = gd.layout.shapes.filter((s) => s.name === "tool-fibext");
if (fx.length !== 6) fail("fibext_levels " + fx.length);
if (!fx.some((s) => Math.abs(s.y0 - 160) < 1e-6)) fail("fibext_1x");   // C+파동×1 = 160
el("bt-fibtz").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D0 + 864e5), y1: 131 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const tz = gd.layout.shapes.filter((s) => s.name === "tool-fibtz");
if (tz.length < 5 || tz.some((s) => s.yref !== "paper")) fail("fibtz " + tz.length);
el("bt-cycle").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D0 + 5 * 864e5), y1: 131 }]);   // 주기 5일
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const cyc = gd.layout.shapes.filter((s) => s.name === "tool-cycle");
if (cyc.length < 5 || cyc.length > 40) fail("cycle " + cyc.length);
el("bt-ell").onclick();
for (let i = 0; i < 3; i++) {
  gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
    x0: iso(D0 + i * 5 * 864e5), y0: 130 + i * 5, x1: iso(D0 + (i * 5 + 1) * 864e5), y1: 131 }]);
  gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
}
if (gd.layout.shapes.filter((s) => s.name === "tool-ell").length !== 2) fail("ell_segments");
const ellAnn = (gd.layout.annotations || []).filter((a) => a.name === "tool-ell");
if (ellAnn.length !== 3 || !/1/.test(ellAnn[0].text)) fail("ell_labels " + ellAnn.length);
el("bt-ell").onclick();                            // 버튼 재클릭 = 완료(pending 해제)
// 세션 프로파일 — 일봉 bounds 에선 인트라데이 가드로 미생성
el("bt-sess").onclick();
if (gd.layout.shapes.some((s) => s.name === "tool-sess")) fail("sess_daily_guard");
if (!/인트라데이/.test(el("tool-hint").textContent || "")) fail("sess_hint");
el("bt-mag").onclick({ target: el("bt-mag") });   // 자석 복원
// 지우기 — 신규 도구 도형 전부 제거 (이후 기존 5)6) 섹션이 재검증)
el("bt-clear").onclick();
for (const nm of ["tool-chan", "tool-fork", "tool-gann", "tool-fibext", "tool-fibtz",
                  "tool-cycle", "tool-ell"])
  if (gd.layout.shapes.some((s) => (s.name || "") === nm)) fail("clear_" + nm);

// 7) ⏪ 리플레이 — 커튼 생성/스텝 이동/저장 제외/종료 (매매 연습 모드)
el("bt-replay").onclick();
let cur = gd.layout.shapes.filter((s) => s.name === "replay-curtain");
if (cur.length !== 1 || cur[0].layer !== "above" || cur[0].yref !== "paper") fail("replay_curtain");
const cutBefore = cur[0].x0;
el("rp-step").onclick();                          // 한 봉 앞으로 → 커튼 전진
cur = gd.layout.shapes.filter((s) => s.name === "replay-curtain");
if (cur.length !== 1 || Date.parse(cur[0].x0) <= Date.parse(cutBefore)) fail("replay_step");
// 리플레이 중 새 도형을 그려도 커튼은 localStorage 에 저장되지 않아야 (일시 상태)
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 131, x1: iso(D1), y1: 151 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const savedRp = JSON.parse(_ls["tndraw:TEST:1d:lin"] || "{}");
if ((savedRp.shapes || []).some((s) => s.name === "replay-curtain")) fail("replay_persisted");
if (!(savedRp.shapes || []).length) fail("replay_draw_not_saved");   // 도형 자체는 저장
el("rp-exit").onclick();
if (gd.layout.shapes.some((s) => s.name === "replay-curtain")) fail("replay_exit");
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
                                            vol_axis="yaxis2", store_key="TEST:1d:lin")
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    runner = tmp_path / "run.js"
    runner.write_text(_HARNESS.replace("__SCRIPT__", js), encoding="utf-8")
    r = subprocess.run([_NODE, str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"runtime fail: {r.stdout}\n{r.stderr}"
    assert "OK" in r.stdout


# ── 무한 relayout 루프 회귀 (실브라우저 프리즈 재현) ──────────────────────────
# plotly 는 {shapes:[...]} relayout 의 자기 이벤트를 **비동기**(promise .then 이후)로 emit —
# 이 하니스는 그 비동기 메아리를 충실히 모델링한다. 수정 전 코드면 applyDraw→메아리→magnet
# 재스냅→applyDraw 무한 루프로 relayout 이 CAP 를 넘겨 탭이 얼어붙는다(실측 확정).
_ASYNC_HARNESS = r"""
let gd = null;
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, style: {}, innerHTML: "", textContent: "", _h: {},
    _s: new Set(id === "bt-mag" ? ["on"] : []),
    classList: { toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); } },
    on(e, f) { this._h[e] = f; }, emit(e, p) { if (this._h[e]) this._h[e](p); },
    appendChild() {}, addEventListener() {}, querySelector() { return null; },
    getBoundingClientRect() { return { top: 0, left: 0, right: 100, bottom: 100, width: 100, height: 100 }; } };
  els[id].classList._s = els[id]._s;
  if (id === "chart") gd = els[id];
  return els[id];
}
global.document = { getElementById: el, createElement: () => ({ style: {}, textContent: "" }) };
global.window = { frameElement: null, parent: { innerHeight: 900, addEventListener() {} } };
global.performance = { now: () => 1 };
global.requestAnimationFrame = () => null;
const _ls = {};
global.localStorage = { getItem: (k) => (k in _ls ? _ls[k] : null),
                        setItem: (k, v) => { _ls[k] = String(v); }, removeItem: (k) => { delete _ls[k]; } };
global.setTimeout = (fn) => { fn(); return 0; };
global.clearTimeout = () => {};

// ── 이벤트 루프 + plotly 비동기 자기 메아리 모델 ──
const micro = [];               // promise .then 큐
const echoes = [];              // plotly 가 뒤늦게 쏘는 plotly_relayout 큐 (.then 이후)
let RELAYOUTS = 0;
const CAP = 500;                // 이 이상이면 무한 루프로 간주
global.Plotly = {
  newPlot(g, d, l) { g.data = d; g.layout = l; return { then(cb) { cb(); return this; } }; },
  relayout(g, u) {
    RELAYOUTS++;
    if (RELAYOUTS > CAP) throw new Error("RELAYOUT_LOOP");   // 무한 루프 → 여기서 폭발
    for (const k of Object.keys(u)) if (!k.includes(".") && !k.includes("[")) g.layout[k] = u[k];
    if (u["xaxis.range"]) g.layout.xaxis = Object.assign(g.layout.xaxis || {}, {range: u["xaxis.range"]});
    let cb = null;
    // 1) 프로미스 resolve (guard=false 등) 를 먼저 큐잉
    micro.push(() => { if (cb) cb(); });
    // 2) shapes/annotations 변경이면 plotly 가 **그 뒤** plotly_relayout(자기 메아리) emit
    if ("shapes" in u || "annotations" in u || "dragmode" in u) {
      const payload = ("shapes" in u) ? { shapes: g.layout.shapes }
                    : ("dragmode" in u) ? { dragmode: u.dragmode }
                    : { annotations: g.layout.annotations };
      echoes.push(() => { if (gd._h["plotly_relayout"]) gd._h["plotly_relayout"](payload); });
    }
    return { then(f) { cb = f; return this; } };
  },
};
function pump() {                // .then 먼저 전부, 그 다음 메아리 하나씩 (각 메아리가 또 relayout 유발)
  let steps = 0;
  while (micro.length || echoes.length) {
    if (++steps > 5000) throw new Error("PUMP_RUNAWAY");
    if (micro.length) { micro.shift()(); continue; }
    echoes.shift()();
  }
}
__SCRIPT__
const iso = (d) => new Date(d).toISOString();
const D0 = Date.parse("2025-02-01"), D1 = Date.parse("2025-03-01");
const BASE = JSON.parse(JSON.stringify(gd.layout.shapes || []));
function fail(m) { console.error("FAIL " + m); process.exit(1); }

// 초기 newPlot .then(복원·초기뷰) 메아리 소진
try { pump(); } catch (e) { fail("init_pump " + e.message); }

// ── 수평선 그리기: plotly 가 dragmode='drawline' 로 raw line 을 append 하고 relayout emit ──
el("bt-hline").onclick();
try { pump(); } catch (e) { fail("hline_select " + e.message); }
RELAYOUTS = 0;
gd.layout.shapes = BASE.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 140.3, x1: iso(D0 + 864e5), y1: 141 }]);
gd._h["plotly_relayout"]({ shapes: gd.layout.shapes });   // 사용자 그리기 이벤트
try { pump(); } catch (e) { fail("HLINE_DRAW_LOOP (" + e.message + ") relayouts=" + RELAYOUTS); }
if (RELAYOUTS > 20) fail("hline relayout 폭주 " + RELAYOUTS);
if (gd.layout.shapes.filter(s => s.name === "tool-hline").length !== 1) fail("hline 미생성");

// ── 자석: 사용자가 raw 선을 그리면 스냅 후 안정(메아리로 재스냅 루프 없어야) ──
RELAYOUTS = 0;
gd.layout.shapes = BASE.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0 + 3e5), y0: 131.4, x1: iso(D1 + 3e5), y1: 158.2 }]);
gd._h["plotly_relayout"]({ shapes: gd.layout.shapes });
try { pump(); } catch (e) { fail("MAGNET_LOOP (" + e.message + ") relayouts=" + RELAYOUTS); }
if (RELAYOUTS > 20) fail("magnet relayout 폭주 " + RELAYOUTS);
const ln = gd.layout.shapes[gd.layout.shapes.length - 1];
if (ln.y0 !== Math.round(ln.y0)) fail("magnet 스냅 안됨 " + ln.y0);

// ── 측정 박스도 루프 없어야 ──
el("bt-meas").onclick();
try { pump(); } catch (e) { fail("meas_select " + e.message); }
RELAYOUTS = 0;
gd.layout.shapes = gd.layout.shapes.concat([{ type: "rect", xref: "x", yref: "y",
  x0: iso(D0), y0: 120, x1: iso(D1), y1: 150 }]);
gd._h["plotly_relayout"]({ shapes: gd.layout.shapes });
try { pump(); } catch (e) { fail("MEASURE_LOOP (" + e.message + ") relayouts=" + RELAYOUTS); }
if (RELAYOUTS > 20) fail("measure relayout 폭주 " + RELAYOUTS);
console.log("OK bounded");
"""


# ── ⚡ live 실시간 클라이언트 패치 (피더 localStorage → 마지막 봉 in-place) ────
_LIVE_HARNESS = r"""
const relayoutCalls = [];
let gd = null;
const els = {};
const intervalFns = [];
const winHandlers = {};
function el(id) {
  if (!els[id]) els[id] = { id, style: {}, innerHTML: "", _h: {}, _s: new Set(id === "bt-mag" ? ["on"] : []),
    classList: { toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); } },
    on(e, f) { this._h[e] = f; }, emit(e, p) { if (this._h[e]) this._h[e](p); },
    appendChild() {}, addEventListener() {}, querySelector() { return null; },
    getBoundingClientRect() { return { top: 0 }; } };
  els[id].classList._s = els[id]._s;
  if (id === "chart") gd = els[id];
  return els[id];
}
global.document = { getElementById: el,
                    createElement: () => ({ style: {}, textContent: "" }) };
global.window = { frameElement: null, parent: { innerHeight: 900, addEventListener() {} },
                  addEventListener(t, f) { winHandlers[t] = f; } };
global.performance = { now: () => 1 };
global.requestAnimationFrame = () => null;
global.Plotly = {
  newPlot(g, d, l, c) { g.data = d; g.layout = l; return { then(cb) { cb(); return this; } }; },
  relayout(g, u) { relayoutCalls.push(u);
    for (const k of Object.keys(u)) if (!k.includes(".") && !k.includes("[")) g.layout[k] = u[k];
    return { then(cb) { cb(); return this; } }; },
  restyle(g, u, idx) {
    const t = g.data[(idx || [0])[0]];
    for (const k of Object.keys(u)) t[k] = u[k][0];
    return { then(cb) { cb(); return this; } }; },
};
const _ls = {};
global.localStorage = { getItem: (k) => (k in _ls ? _ls[k] : null),
                        setItem: (k, v) => { _ls[k] = String(v); },
                        removeItem: (k) => { delete _ls[k]; } };
global.setTimeout = (fn) => { fn(); return 0; };
global.clearTimeout = () => {};
global.setInterval = (fn) => { intervalFns.push(fn); return 0; };
__SCRIPT__
function fail(m) { console.error("FAIL " + m); process.exit(1); }
if (!intervalFns.length) fail("no_poll_interval");        // live 폴링 미등록
if (!winHandlers.storage) fail("no_storage_listener");    // 피더 push 리스너 미등록
// 1) 신선한 push → 마지막 봉(캔들 close/high)·현재가선(tn-last)·리드아웃 패치
_ls["tnrt:TEST"] = JSON.stringify({ p: 210.5, w: Date.now() });
winHandlers.storage({ key: "tnrt:TEST" });
const c = gd.data[0].close;
if (c[c.length - 1] !== 210.5) fail("close_not_patched " + c[c.length - 1]);
if (gd.data[0].high[c.length - 1] !== 210.5) fail("high_not_patched");
const moved = relayoutCalls.some(u => Object.keys(u).some(
  k => /^shapes\[\d+\]\.y0$/.test(k) && u[k] === 210.5));
if (!moved) fail("tn_last_shape_not_moved");
if (!/210\.50/.test(el("ohlcbar").innerHTML)) fail("readout " + el("ohlcbar").innerHTML);
// 2) stale(>30s) push 는 무시 — 죽은 탭의 낡은 값 방어
_ls["tnrt:TEST"] = JSON.stringify({ p: 999.9, w: Date.now() - 60000 });
intervalFns.forEach(fn => fn());
if (gd.data[0].close[c.length - 1] !== 210.5) fail("stale_applied");
console.log("OK live");
"""


@pytest.mark.skipif(_NODE is None, reason="node 미설치 — 런타임 JS 검증 스킵")
def test_live_realtime_client_patch(tmp_path):
    """⚡ live — 피더 push 로 마지막 봉·현재가선 in-place 패치, stale 값은 무시."""
    idx = pd.date_range("2025-01-01", periods=70, freq="D")
    df = pd.DataFrame({"Open": range(100, 170), "High": range(101, 171),
                       "Low": range(99, 169), "Close": range(100, 170),
                       "Volume": [1e6] * 70}, index=idx)
    fig = charts.price_chart(df, "TEST", kind="candle", show_volume=True,
                             view_days=90, avg_cost=140.0)
    html = plotly_embed.pannable_chart_html(fig, df, height=460, view_days=90,
                                            vol_axis="yaxis2",
                                            store_key="TEST:1d:lin", live=True)
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    runner = tmp_path / "live.js"
    runner.write_text(_LIVE_HARNESS.replace("__SCRIPT__", js), encoding="utf-8")
    r = subprocess.run([_NODE, str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"live patch fail: {r.stdout}\n{r.stderr}"
    assert "OK live" in r.stdout


# ── guard 창 도형완성 드롭 회귀 ("자석 가끔 안 먹음") ─────────────────────────
# animStep y-lerp·muteHover·setTool 등 프로그램 relayout 이 in-flight(guard=true)인
# 순간 사용자의 도형완성 relayout 이 도착하면 통째로 드롭되던 버그 — 도형 이벤트는
# guard 를 우회해 항상 처리되어야 한다(메아리 루프는 내용 기반 가드가 차단).
_GUARD_DROP_BODY = r"""
const iso = (d) => new Date(d).toISOString();
const D0 = Date.parse("2025-02-01"), D1 = Date.parse("2025-03-01");
const BASE = JSON.parse(JSON.stringify(gd.layout.shapes || []));
function fail(m) { console.error("FAIL " + m); process.exit(1); }
try { pump(); } catch (e) { fail("init_pump " + e.message); }

// ── guard=true(프로그램 relayout in-flight) 창에서 수평선 완성 — 드롭 금지 ──
// setTool 의 dragmode relayout 직후(pump 전) = guard=true 인 창을 그대로 재현.
el("bt-hline").onclick();                        // guard=true (.then 아직 미해소)
gd.layout.shapes = BASE.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 140.3, x1: iso(D0 + 864e5), y1: 141 }]);
gd._h["plotly_relayout"]({ shapes: gd.layout.shapes });   // guard 창에서 도착
try { pump(); } catch (e) { fail("HLINE_GUARD_LOOP " + e.message); }
if (gd.layout.shapes.filter(s => s.name === "tool-hline").length !== 1)
  fail("guarded_hline_dropped");

// ── 자석(tool=null·raw 선)도 guard 창에서 스냅되어야 ──
el("bt-meas").onclick(); el("bt-meas").onclick(); // 토글 온·오프 → guard=true 창
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0 + 3e5), y0: 131.4, x1: iso(D1 + 3e5), y1: 158.2 }]);
gd._h["plotly_relayout"]({ shapes: gd.layout.shapes });
try { pump(); } catch (e) { fail("MAGNET_GUARD_LOOP " + e.message); }
const ln = gd.layout.shapes[gd.layout.shapes.length - 1];
if (ln.y0 !== Math.round(ln.y0)) fail("guarded_magnet_dropped " + ln.y0);
console.log("OK guard-bypass");
"""


@pytest.mark.skipif(_NODE is None, reason="node 미설치 — 런타임 JS 검증 스킵")
def test_shape_event_survives_guard_window(tmp_path):
    """guard=true 창에 도착한 도형완성 이벤트가 드롭되지 않는다 (자석·도구 신뢰성)."""
    idx = pd.date_range("2025-01-01", periods=70, freq="D")
    df = pd.DataFrame({"Open": range(100, 170), "High": range(101, 171),
                       "Low": range(99, 169), "Close": range(100, 170),
                       "Volume": [1e6] * 70}, index=idx)
    fig = charts.price_chart(df, "TEST", kind="candle", show_volume=True,
                             show_rsi=True, avg_cost=140.0)
    html = plotly_embed.pannable_chart_html(fig, df, height=460, view_days=90,
                                            vol_axis="yaxis2", store_key="TEST:1d:lin")
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    stub = _ASYNC_HARNESS.split("__SCRIPT__")[0]   # 비동기 메아리 스텁 재사용
    runner = tmp_path / "guard.js"
    runner.write_text(stub + js + _GUARD_DROP_BODY, encoding="utf-8")
    r = subprocess.run([_NODE, str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"guard drop fail: {r.stdout}\n{r.stderr}"
    assert "OK guard-bypass" in r.stdout


@pytest.mark.skipif(_NODE is None, reason="node 미설치 — 런타임 JS 검증 스킵")
def test_drawing_no_infinite_relayout_loop(tmp_path):
    """드로잉 시 자기 메아리 무한 relayout 루프(=탭 프리즈) 회귀 방어.

    plotly 의 비동기 shapes 메아리를 충실히 모델 → 수정 전이면 relayout 이 CAP(500) 초과.
    """
    idx = pd.date_range("2025-01-01", periods=70, freq="D")
    df = pd.DataFrame({"Open": range(100, 170), "High": range(101, 171),
                       "Low": range(99, 169), "Close": range(100, 170),
                       "Volume": [1e6] * 70}, index=idx)
    fig = charts.price_chart(df, "TEST", kind="candle", show_volume=True,
                             show_rsi=True, avg_cost=140.0)
    html = plotly_embed.pannable_chart_html(fig, df, height=460, view_days=90,
                                            vol_axis="yaxis2", store_key="TEST:1d:lin")
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    runner = tmp_path / "loop.js"
    runner.write_text(_ASYNC_HARNESS.replace("__SCRIPT__", js), encoding="utf-8")
    r = subprocess.run([_NODE, str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"loop guard fail: {r.stdout}\n{r.stderr}"
    assert "OK bounded" in r.stdout


# ── TV 갭 도구 3종 — 📐 회귀추세 · ⚓ 고정VWAP · 📊 볼륨프로필 (V-series) ────────
_TOOLS2_HARNESS = r"""
const relayoutCalls = [];
let gd = null;
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, style: {}, innerHTML: "", textContent: "", _h: {},
    _s: new Set(id === "bt-mag" ? ["on"] : []),
    classList: { toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); } },
    on(e, f) { this._h[e] = f; }, emit(e, p) { if (this._h[e]) this._h[e](p); },
    appendChild() {}, addEventListener() {}, querySelector() { return null; },
    getBoundingClientRect() { return { top: 0 }; } };
  els[id].classList._s = els[id]._s;
  if (id === "chart") gd = els[id];
  return els[id];
}
global.document = { getElementById: el,
                    createElement: () => ({ style: {}, textContent: "" }) };
global.window = { frameElement: null, parent: { innerHeight: 900, addEventListener() {} } };
global.performance = { now: () => 1 };
global.requestAnimationFrame = () => null;
global.Plotly = {
  newPlot(g, d, l, c) { g.data = d; g.layout = l; return { then(cb) { cb(); return this; } }; },
  relayout(g, u) { relayoutCalls.push(u);
    for (const k of Object.keys(u)) if (!k.includes(".") && !k.includes("[")) g.layout[k] = u[k];
    return { then(cb) { cb(); return this; } }; },
  addTraces(g, trs) { g.data = (g.data || []).concat(trs); },
  deleteTraces(g, idxs) { g.data = g.data.filter((t, i) => !idxs.includes(i)); },
};
const _ls = {};
global.localStorage = { getItem: (k) => (k in _ls ? _ls[k] : null),
                        setItem: (k, v) => { _ls[k] = String(v); },
                        removeItem: (k) => { delete _ls[k]; } };
global.setTimeout = (fn) => { fn(); return 0; };
global.clearTimeout = () => {};
__SCRIPT__
const iso = (d) => new Date(d).toISOString();
const D0 = Date.parse("2025-02-01"), D1 = Date.parse("2025-03-01");
const BASE = JSON.parse(JSON.stringify(gd.layout.shapes || []));
function fail(m) { console.error("FAIL " + m); process.exit(1); }

// 1) 📐 회귀추세 — 박스 드래그 = 중심선+상/하단 3선 + 라벨
el("bt-reg").onclick();
gd.layout.shapes = BASE.concat([{ type: "rect", xref: "x", yref: "y",
  x0: iso(D0), y0: 120, x1: iso(D1), y1: 160 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const regs = gd.layout.shapes.filter(s => s.name === "tool-reg");
if (regs.length !== 3) fail("reg_lines " + regs.length);
if (!(gd.layout.annotations || []).some(a => a.name === "tool-reg"
    && a.text.indexOf("±2σ") >= 0)) fail("reg_ann");
// 중심선 기울기 = 데이터 추세(우상향 합성 데이터) 반영
const mid = regs[0];
if (!(mid.y1 > mid.y0)) fail("reg_slope");

// 2) 📊 볼륨프로필 — 박스 드래그 = 빈 히스토그램 + POC 라인 + 외곽 박스
el("bt-vprof").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "rect", xref: "x", yref: "y",
  x0: iso(D0), y0: 120, x1: iso(D1), y1: 160 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const vps = gd.layout.shapes.filter(s => s.name === "tool-vprof");
if (vps.length < 6) fail("vprof_shapes " + vps.length);
if (!vps.some(s => s.type === "line")) fail("vprof_poc_line");
if (!(gd.layout.annotations || []).some(a => a.name === "tool-vprof"
    && a.text.indexOf("POC") >= 0)) fail("vprof_poc_ann");

// 3) ⚓ 고정VWAP — 짧게 긋기 = VWAP 트레이스 추가 + 앵커 영속화
const nData = gd.data.length;
el("bt-avwap").onclick();
gd.layout.shapes = gd.layout.shapes.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D0 + 864e5), y1: 131 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
const vtr = gd.data.filter(t => t && t.meta === "tool-avwap");
if (vtr.length !== 1) fail("avwap_trace " + vtr.length);
if (!(vtr[0].y.length > 2 && vtr[0].y[0] > 0)) fail("avwap_values");
const doc = JSON.parse(_ls["tndraw:TEST:1d:lin"] || "{}");
if (!Array.isArray(doc.vwaps) || doc.vwaps.length !== 1) fail("avwap_persist " + JSON.stringify(doc.vwaps));

// 4) 🗑 지우기 — VWAP 트레이스·도형·저장소 모두 정리
el("bt-clear").onclick();
if (gd.data.some(t => t && t.meta === "tool-avwap")) fail("clear_vwap_trace");
if (JSON.stringify(gd.layout.shapes) !== JSON.stringify(BASE)) fail("clear_shapes");
if (Object.keys(_ls).some(k => k.startsWith("tndraw:"))) fail("clear_storage");

// 5) 재로드 복원 — vwap 앵커 저장 → 새 세션에서 트레이스 재계산 (도형 없이 vwaps 만)
el("bt-avwap").onclick();
gd.layout.shapes = BASE.concat([{ type: "line", xref: "x", yref: "y",
  x0: iso(D0), y0: 130, x1: iso(D0 + 864e5), y1: 131 }]);
gd.emit("plotly_relayout", { shapes: gd.layout.shapes });
if (!JSON.parse(_ls["tndraw:TEST:1d:lin"] || "{}").vwaps.length) fail("persist_before_reload");
for (const k of Object.keys(els)) delete els[k];   // 새 세션 모사 — storage 유지
gd = null;
__SCRIPT__
if (!gd || !gd.data) fail("reload_gd");
if (gd.data.filter(t => t && t.meta === "tool-avwap").length !== 1) fail("vwap_restore");
console.log("OK tools2");
"""


@pytest.mark.skipif(_NODE is None, reason="node 미설치 — 런타임 JS 검증 스킵")
def test_new_drawing_tools_runtime(tmp_path):
    """📐 회귀추세·📊 볼륨프로필·⚓ 고정VWAP — 생성·영속화·지우기·재로드 복원."""
    idx = pd.date_range("2025-01-01", periods=70, freq="D")
    df = pd.DataFrame({"Open": range(100, 170), "High": range(101, 171),
                       "Low": range(99, 169), "Close": range(100, 170),
                       "Volume": [1e6] * 70}, index=idx)
    fig = charts.price_chart(df, "TEST", kind="candle", show_volume=True,
                             show_rsi=True, avg_cost=140.0)
    html = plotly_embed.pannable_chart_html(fig, df, height=460, view_days=90,
                                            vol_axis="yaxis2", store_key="TEST:1d:lin")
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    runner = tmp_path / "tools2.js"
    runner.write_text(_TOOLS2_HARNESS.replace("__SCRIPT__", js), encoding="utf-8")
    r = subprocess.run([_NODE, str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"tools2 fail: {r.stdout}\n{r.stderr}"
    assert "OK tools2" in r.stdout


@pytest.mark.skipif(_NODE is None, reason="node 미설치 — 런타임 JS 검증 스킵")
def test_new_tools_no_infinite_loop(tmp_path):
    """신규 도구 3종도 비동기 자기 메아리 무한 relayout 없이 유계 (프리즈 회귀 방어)."""
    idx = pd.date_range("2025-01-01", periods=70, freq="D")
    df = pd.DataFrame({"Open": range(100, 170), "High": range(101, 171),
                       "Low": range(99, 169), "Close": range(100, 170),
                       "Volume": [1e6] * 70}, index=idx)
    fig = charts.price_chart(df, "TEST", kind="candle", show_volume=True, avg_cost=140.0)
    html = plotly_embed.pannable_chart_html(fig, df, height=460, view_days=90,
                                            vol_axis="yaxis2", store_key="TEST:1d:lin")
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    stub = _ASYNC_HARNESS.split("__SCRIPT__")[0]
    stub = stub.replace("global.Plotly = {", "global.Plotly = {\n"
                        "  addTraces(g, trs) { g.data = (g.data || []).concat(trs); },\n"
                        "  deleteTraces(g, idxs) { g.data = g.data.filter((t, i) => !idxs.includes(i)); },")
    body = r"""
const iso = (d) => new Date(d).toISOString();
const D0 = Date.parse("2025-02-01"), D1 = Date.parse("2025-03-01");
const BASE = JSON.parse(JSON.stringify(gd.layout.shapes || []));
function fail(m) { console.error("FAIL " + m); process.exit(1); }
try { pump(); } catch (e) { fail("init " + e.message); }
for (const [btn, shape] of [
    ["bt-reg", { type: "rect", xref: "x", yref: "y", x0: iso(D0), y0: 120, x1: iso(D1), y1: 160 }],
    ["bt-vprof", { type: "rect", xref: "x", yref: "y", x0: iso(D0), y0: 120, x1: iso(D1), y1: 160 }],
    ["bt-avwap", { type: "line", xref: "x", yref: "y", x0: iso(D0), y0: 130, x1: iso(D0 + 864e5), y1: 131 }]]) {
  el(btn).onclick();
  try { pump(); } catch (e) { fail(btn + "_select " + e.message); }
  RELAYOUTS = 0;
  gd.layout.shapes = (gd.layout.shapes || []).concat([shape]);
  gd._h["plotly_relayout"]({ shapes: gd.layout.shapes });
  try { pump(); } catch (e) { fail(btn + "_LOOP (" + e.message + ") n=" + RELAYOUTS); }
  if (RELAYOUTS > 25) fail(btn + " relayout 폭주 " + RELAYOUTS);
}
console.log("OK tools2 bounded");
"""
    runner = tmp_path / "tools2loop.js"
    runner.write_text(stub + js + body, encoding="utf-8")
    r = subprocess.run([_NODE, str(runner)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"tools2 loop fail: {r.stdout}\n{r.stderr}"
    assert "OK tools2 bounded" in r.stdout
