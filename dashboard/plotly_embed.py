"""dashboard/plotly_embed.py — 커스텀 plotly 임베드 (팬 시 부드러운 y축 자동 리스케일 + 드로잉 도구).

st.plotly_chart 는 팬/줌(relayout)을 서버에 안 알려줘 y 자동맞춤이 불가 — 이 모듈은
차트를 st.components.v1.html 로 임베드하고 브라우저 JS 가 `plotly_relayout` 을 받아
보이는 x구간의 고저(High/Low)로 y축을 즉시(디바운스 50ms·재진입 가드) 재설정한다
(TradingView 방식). 마커 클릭 상세는 iframe 이 서버 콜백 불가라 인차트 박스로 렌더.

드로잉 도구(TradingView 풍·전부 클라이언트측):
  🧲 자석 — 선·박스 그리기/편집 시 끝점을 가장 가까운 봉의 OHLC 에 스냅 (기본 ON)
  ─ 수평선 — 짧게 긋면 시작점 가격의 전폭 수평선 + 우측 가격 라벨
  🔱 피보나치 — 고↔저로 긋면 되돌림 레벨(0·23.6·38.2·50·61.8·78.6·100%) 자동
  📏 측정 — 박스 드래그로 Δ가격·Δ%·봉수·기간 (상승 초록/하락 빨강)
  🗑 지우기 — 직접 그린 도형 전체 제거 (서버 오버레이는 보존)

plotly.js 는 CDN(파이썬 plotly 번들과 동일 버전 핀) — 실패 시 안내 문구,
호출부(ticker 페이지)가 구형 렌더러(st.plotly_chart) 폴백 토글 제공.
순수 함수(HTML 문자열 반환) — 단위테스트 가능. f-string 중괄호 함정 회피를 위해
JS 템플릿은 평문 + `@@TOKEN@@` 치환(치환 순서: 스칼라 → bounds → fig 마지막).
"""
from __future__ import annotations

import json


def _plotlyjs_cdn() -> str:
    try:
        from plotly.offline import get_plotlyjs_version
        return f"https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"
    except Exception:
        return "https://cdn.plot.ly/plotly-3.6.0.min.js"


def price_bounds_json(hist) -> str:
    """[[epoch_ms, low, high, volume, open, close], ...] — y 자동맞춤 + 🧲 스냅용 (JS 소비)."""
    if hist is None or getattr(hist, "empty", True):
        return "[]"
    cols = set(hist.columns)
    close = hist["Close"]
    lo = hist["Low"] if "Low" in cols else close
    hi = hist["High"] if "High" in cols else close
    op = hist["Open"] if "Open" in cols else close
    vol = hist["Volume"] if "Volume" in cols else None
    out = []
    for i, (ts, l, h, o, c) in enumerate(zip(hist.index, lo.values, hi.values,
                                             op.values, close.values)):
        if l == l and h == h:                                   # NaN 스킵
            v = float(vol.iloc[i]) if vol is not None and vol.iloc[i] == vol.iloc[i] else 0.0
            out.append([int(ts.timestamp() * 1000), float(l), float(h), v,
                        float(o) if o == o else float(l), float(c) if c == c else float(h)])
    return json.dumps(out)


def compare_bounds_json(main_hist, compare: dict, view_days=None) -> str:
    """비교 모드 y 자동맞춤 프레임 — 전 시리즈를 %로 정규화해 병합 (JS yFit 소비).

    행 = [epoch_ms, pct, pct, volume] (4열 — OHLC 없음 → 🧲 스냅은 값 자체에만).
    메인 행만 거래량 탑재(거래량 패널=메인 기준). yFit 은 구간 내 행들의 min/max
    스캔이라 시리즈별 행이 섞여 있어도 동작.
    """
    from dashboard.charts import normalize_pct
    rows = []

    def _rows(series, vol=None):
        ns = normalize_pct(series, view_days)
        if ns is None:
            return
        for i, (ts, p) in enumerate(zip(ns.index, ns.values)):
            if p == p:                                       # NaN 스킵
                v = 0.0
                if vol is not None:
                    try:
                        vv = float(vol.loc[ts])
                        v = vv if vv == vv else 0.0
                    except Exception:
                        v = 0.0
                rows.append([int(ts.timestamp() * 1000), float(p), float(p), v])

    if main_hist is not None and not getattr(main_hist, "empty", True):
        cols = set(main_hist.columns)
        _rows(main_hist["Close"], main_hist["Volume"] if "Volume" in cols else None)
    for s in (compare or {}).values():
        if s is not None and len(s):
            _rows(s)
    rows.sort(key=lambda r: r[0])
    return json.dumps(rows)


# ── HTML/JS 템플릿 (평문 — f-string 아님 · @@TOKEN@@ 치환) ─────────────────────
_TEMPLATE = r"""
<style>
  #tools { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin:0 0 6px 2px;
           font:12px Pretendard, -apple-system, sans-serif; }
  .tbtn { background:#131722; color:#d1d4dc; border:1px solid #1e222d; border-radius:6px;
          padding:3px 9px; font:12px Pretendard, -apple-system, sans-serif; cursor:pointer;
          line-height:1.5; }
  .tbtn:hover { border-color:#2f81f7; }
  .tbtn.on { border-color:#2f81f7; color:#2f81f7; background:rgba(47,129,247,.08); }
  #tool-hint { color:#9198a6; font-size:11px; margin-left:4px; }
</style>
<div id="tools">
  <button id="bt-mag" class="tbtn on" title="그리기·편집 시 봉의 시가/고가/저가/종가에 착 붙음">🧲 자석</button>
  <button id="bt-hline" class="tbtn" title="차트에 짧게 긋기 = 시작점 가격 수평선">─ 수평선</button>
  <button id="bt-fib" class="tbtn" title="고점↔저점 긋기 = 되돌림 레벨 자동">🔱 피보나치</button>
  <button id="bt-meas" class="tbtn" title="박스 드래그 = Δ가격·Δ%·봉수·기간">📏 측정</button>
  <button id="bt-clear" class="tbtn" title="직접 그린 도형 전체 제거">🗑 지우기</button>
  <span id="tool-hint"></span>
  <span id="ohlcbar" style="margin-left:auto;font:11px 'JetBrains Mono', ui-monospace, monospace;
        color:#9198a6;white-space:nowrap"></span>
</div>
<div id="chart" style="width:100%;min-height:@@HEIGHT@@px"></div>
<div id="detail" style="display:none;margin-top:6px;padding:8px 12px;border:1px solid #1e222d;
  border-radius:8px;background:#131722;color:#d1d4dc;
  font:12px 'JetBrains Mono', ui-monospace, monospace"></div>
<script src="@@CDN@@"></script>
<script>
(function() {
  if (typeof Plotly === "undefined") {
    document.getElementById("chart").innerHTML =
      "<div style='color:#9198a6;padding:20px'>plotly.js CDN 로드 실패 — 📐 팝오버의 '구형 렌더러'를 켜세요</div>";
    return;
  }
  const fig = @@FIG@@;
  const bounds = @@BOUNDS@@;                     // [[ms, low, high, vol, open, close], ...] 시간 오름차순
  const gd = document.getElementById("chart");
  const fitVH = @@FIT_VH@@;                      // 풀뷰 — 부모 창 높이에 맞춰 리사이즈
  const pctMode = @@PCT_MODE@@;                  // 비교(%) 모드 — 가격 포맷 대신 %
  const yLog = @@Y_LOG@@;                        // 로그 스케일 — 도형 y 좌표는 log10 공간
  function vhFit() {                             // same-origin iframe — frameElement 직접 리사이즈
    try {
      const fe = window.frameElement;
      const top = fe.getBoundingClientRect().top;
      const h = Math.max(480, window.parent.innerHeight - top - 64);
      fe.style.height = (h + 48) + "px";
      return h;
    } catch (e) { return @@HEIGHT@@; }
  }
  fig.layout.height = fitVH ? vhFit() : @@HEIGHT@@;
  if (fitVH) {
    try {
      window.parent.addEventListener("resize", () => {
        const h = vhFit();
        guard = true;
        Plotly.relayout(gd, {height: h}).then(() => { guard = false; });
      });
    } catch (e) {}
  }
  // y축은 전부 프로그램 제어(자동 맞춤) — 사용자 팬이 y를 끌지 않게 고정해
  // '드래그 중 y 싸움 → 종료 시 스냅' 흔들림의 근원을 제거 (TradingView 방식)
  for (const k of Object.keys(fig.layout)) {
    if (k.startsWith("yaxis")) fig.layout[k].fixedrange = true;
  }
  const hoverMode = fig.layout.hovermode || "x unified";
  let guard = false, dragging = false, hoverOff = false;

  const volAxis = @@VOL_AXIS@@;                  // 거래량 패널 축 id (없으면 null)
  // 전역 거래량 상위 2% 분위 캡 — 스파이크가 창에 들어와도 평상 막대가 읽히게
  const volCap = (() => {
    const vs = bounds.map(b => b[3]).filter(v => v > 0).sort((a, b) => a - b);
    if (!vs.length) return Infinity;
    return vs[Math.floor(vs.length * 0.98)] * 1.6;
  })();

  function lowerBound(t0) {                      // 시간 정렬 bounds 이진 탐색
    let lo = 0, hi = bounds.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (bounds[m][0] < t0) lo = m + 1; else hi = m; }
    return lo;
  }

  const toY = (v) => (yLog ? Math.log10(v) : v);        // 데이터값 → 축좌표
  const fromY = (v) => (yLog ? Math.pow(10, v) : v);    // 축좌표 → 데이터값

  function yFit(x0, x1) {                        // 보이는 구간 고저·거래량 최대 + 패딩
    if (!bounds.length) return null;
    let lo = Infinity, hi = -Infinity, vmax = 0;
    for (let i = lowerBound(x0); i < bounds.length && bounds[i][0] <= x1; i++) {
      const b = bounds[i];
      if (b[1] < lo) lo = b[1]; if (b[2] > hi) hi = b[2]; if (b[3] > vmax) vmax = b[3];
    }
    if (!isFinite(lo)) return null;
    const pad = Math.max((hi - lo) * 0.06, Math.abs(hi) * 0.002);
    return {price: [toY(Math.max(lo - pad, yLog ? lo * 0.98 : -Infinity)), toY(hi + pad)],
            vol: [0, Math.min(vmax, volCap) * 1.1 || 1]};
  }

  // ── 부드러운 y 전환 — 시간 기반 lerp + **비용 적응형 스로틀** ──
  // 일목 구름·채널처럼 fill 폴리곤이 많으면 relayout 1회가 비싸다 → 실제 리드로우
  // 비용(EMA)을 재서 갱신 간격을 자동으로 벌리고(무거우면 ~10fps·가벼우면 60fps),
  // 목표와 차이가 시각적으로 무의미하면(데드밴드) relayout 자체를 생략한다.
  let target = null, curY = null, raf = null, busy = false;
  let costMs = 8, lastApply = 0, lastStep = 0;

  function animStep() {
    raf = null;
    if (!target) return;
    const now = performance.now();
    const dt = Math.min(100, now - (lastStep || now));
    lastStep = now;
    if (!curY) curY = {price: target.price.slice(), vol: target.vol.slice()};
    const a = 1 - Math.exp(-dt / 90);            // 프레임률 무관 ~90ms 수렴 ease-out
    curY.price[0] += (target.price[0] - curY.price[0]) * a;
    curY.price[1] += (target.price[1] - curY.price[1]) * a;
    curY.vol[1]   += (target.vol[1] - curY.vol[1]) * a;
    const span = Math.abs(target.price[1] - target.price[0]) || 1;
    const dist = Math.abs(curY.price[0] - target.price[0])
               + Math.abs(curY.price[1] - target.price[1]);
    const done = dist < span * 0.002;
    if (done) curY = {price: target.price.slice(), vol: target.vol.slice()};
    const minGap = Math.max(16, costMs * 1.2);   // 리드로우 비용만큼 간격 자동 확대
    const applied = gd.layout.yaxis.range || [];
    const visDelta = Math.abs((applied[0] ?? 1e18) - curY.price[0])
                   + Math.abs((applied[1] ?? 1e18) - curY.price[1]);
    if (!busy && now - lastApply >= minGap && visDelta > span * 0.004) {
      busy = true; guard = true; lastApply = now;
      const t0 = performance.now();
      const upd = {"yaxis.range": curY.price.slice()};
      if (volAxis && !dragging) upd[volAxis + ".range"] = [0, curY.vol[1]];
      Plotly.relayout(gd, upd).then(() => {
        costMs = costMs * 0.7 + (performance.now() - t0) * 0.3;   // 비용 EMA
        busy = false; guard = false;
      });
    }
    if (!done || dragging) raf = requestAnimationFrame(animStep);
  }

  function setTarget(x0, x1) {
    const r = yFit(x0, x1);
    if (!r) return;
    target = r;
    if (!raf) raf = requestAnimationFrame(animStep);
  }

  function evXRange(e) {                         // relayout(ing) 페이로드 → [ms, ms]
    if (!e) return null;
    let r0 = e["xaxis.range[0]"], r1 = e["xaxis.range[1]"];
    if (e["xaxis.range"]) { r0 = e["xaxis.range"][0]; r1 = e["xaxis.range"][1]; }
    if (r0 == null || r1 == null) return null;
    return [Date.parse(r0) || +r0, Date.parse(r1) || +r1];
  }

  const lastClose = @@LAST_CLOSE@@;

  function callouts(x0, x1, upd) {              // 보이는 구간 최고/최저 콜아웃 팬 추종
    let hi = -Infinity, hiT = 0, lo = Infinity, loT = 0;
    for (const [t, l, h] of bounds) {
      if (t >= x0 && t <= x1) {
        if (h > hi) { hi = h; hiT = t; }
        if (l < lo) { lo = l; loT = t; }
      }
    }
    if (!isFinite(lo) || !lastClose) return;
    const anns = gd.layout.annotations || [];
    const fmt = (v) => Math.round(v).toLocaleString();
    const pct = (v) => ((lastClose / v - 1) * 100).toFixed(1);
    for (let i = 0; i < anns.length; i++) {
      if (anns[i].name === "tn-hi") {
        upd[`annotations[${i}].x`] = new Date(hiT).toISOString();
        upd[`annotations[${i}].y`] = toY(hi);
        upd[`annotations[${i}].text`] = `${fmt(hi)} (${pct(hi) > 0 ? "+" : ""}${pct(hi)}%)`;
      }
      if (anns[i].name === "tn-lo") {
        upd[`annotations[${i}].x`] = new Date(loT).toISOString();
        upd[`annotations[${i}].y`] = toY(lo);
        upd[`annotations[${i}].text`] = `${fmt(lo)} (+${pct(lo)}%)`.replace("(+-", "(-");
      }
    }
  }

  function muteHover() {                         // 제스처 중 hover 연산 중지 (1회)
    if (hoverOff) return;
    hoverOff = true; guard = true;
    Plotly.relayout(gd, {hovermode: false}).then(() => { guard = false; });
  }

  function finishGesture() {                     // 제스처 끝 1회 — 콜아웃·hover 복원
    const xr = gd.layout.xaxis.range;
    if (!xr) return;
    const x0 = Date.parse(xr[0]), x1 = Date.parse(xr[1]);
    setTarget(x0, x1);                           // y 는 lerp 루프가 수렴
    const upd = {};
    callouts(x0, x1, upd);                       // 무거운 주석 갱신은 여기서만
    if (hoverOff) { upd["hovermode"] = hoverMode; hoverOff = false; }
    if (Object.keys(upd).length) {
      guard = true;
      Plotly.relayout(gd, upd).then(() => { guard = false; });
    }
  }
  const rescale = finishGesture;                 // 초기 표시창 경로 하위호환

  // ── OHLC 데이터창 — 호버 봉의 시·고·저·종·거래량·등락% 리드아웃 (bounds 재사용) ──
  const ohlcEl = document.getElementById("ohlcbar");
  function ohlcReadout(ms) {
    if (!ohlcEl || !bounds.length) return;
    const i = Math.min(Math.max(lowerBound(ms), 0), bounds.length - 1);
    const idx = (i > 0 && Math.abs(bounds[i - 1][0] - ms) < Math.abs(bounds[i][0] - ms)) ? i - 1 : i;
    const b = bounds[idx];
    const f = (v) => (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString() : (+v).toFixed(2));
    if (b.length < 6) {                          // 비교(%) 프레임 — 값만
      ohlcEl.textContent = (b[1] >= 0 ? "+" : "") + (+b[1]).toFixed(2) + "%";
      return;
    }
    const prevC = idx > 0 ? bounds[idx - 1][5] : b[4];
    const chg = prevC ? ((b[5] / prevC - 1) * 100) : 0;
    const col = b[5] >= b[4] ? "#26a69a" : "#ef5350";
    const vol = b[3] >= 1e6 ? (b[3] / 1e6).toFixed(1) + "M" : Math.round(b[3]).toLocaleString();
    ohlcEl.innerHTML = `시 ${f(b[4])} 고 ${f(b[2])} 저 ${f(b[1])} ` +
      `종 <b style="color:${col}">${f(b[5])}</b> ` +
      `<span style="color:${col}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span> · ${vol}`;
  }
  if (bounds.length) ohlcReadout(bounds[bounds.length - 1][0]);   // 기본 = 마지막 봉

  // ══ 드로잉 도구 — 🧲 자석 스냅 · 수평선 · 피보나치 · 측정 · 지우기 ══════════
  let magnet = true, tool = null;                // tool: null | 'hline' | 'fib' | 'meas'

  // ── 드로잉 영속화 — localStorage (키=티커:봉:스케일 · 서버 도형 이후만 저장) ──
  const storeKey = @@STORE_KEY@@;                // null 이면 영속화 없음 (구형/테스트)
  let saveTimer = null;
  function saveDrawings() {
    if (!storeKey) return;
    try {
      const shapes = (gd.layout.shapes || []).slice(baseShapeCount);
      const anns = (gd.layout.annotations || []).filter(
        (a) => String(a.name || "").startsWith("tool-"));
      const k = "tndraw:" + storeKey;
      if (!shapes.length && !anns.length) { localStorage.removeItem(k); return; }
      localStorage.setItem(k, JSON.stringify({v: 1, shapes: shapes, anns: anns}));
    } catch (e) {}                               // 사파리 프라이빗 등 — 조용히 비영속
  }
  function scheduleSave() {
    if (!storeKey) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveDrawings, 250);
  }
  function loadDrawings() {
    if (!storeKey) return null;
    try {
      const d = JSON.parse(localStorage.getItem("tndraw:" + storeKey) || "null");
      if (!d || d.v !== 1 || !Array.isArray(d.shapes) || !Array.isArray(d.anns)) return null;
      return d;
    } catch (e) { return null; }
  }
  // 서버 오버레이(평단선·현재가선·RSI 밴드)는 사용자 도형과 구분 불가(둘 다 무명 shape) →
  // 개수가 아닌 **깊은 복사본**을 보존 기준으로 삼는다(지우개로 인덱스가 밀려도 안전).
  const baseShapes = JSON.parse(JSON.stringify(fig.layout.shapes || []));
  const baseShapeCount = baseShapes.length;      // 앞쪽 N개 = 서버 도형 → 스냅/편집 금지

  function nearestRow(ms) {                      // 가장 가까운 봉 (이진 탐색 + 이웃 비교)
    if (!bounds.length) return null;
    let i = lowerBound(ms);
    if (i >= bounds.length) i = bounds.length - 1;
    if (i > 0 && Math.abs(bounds[i - 1][0] - ms) <= Math.abs(bounds[i][0] - ms)) i -= 1;
    return bounds[i];
  }

  function snapPoint(ms, yAxisVal) {             // [ms, y축좌표] → 봉시각 + 최근접 OHLC
    const row = nearestRow(ms);
    if (!row) return [ms, yAxisVal];
    const cand = row.length >= 6 ? [row[1], row[2], row[4], row[5]] : [row[1], row[2]];
    let best = null;
    for (const v of cand) {
      if (yLog && v <= 0) continue;
      const av = toY(v);
      if (best === null || Math.abs(av - yAxisVal) < Math.abs(best - yAxisVal)) best = av;
    }
    return [row[0], best === null ? yAxisVal : best];
  }

  const toMs = (v) => (typeof v === "number" ? v : Date.parse(v));
  const toISO = (ms) => new Date(ms).toISOString();

  function fmtVal(yAxisVal) {                    // 축좌표 → 표시 문자열 (가격/%)
    const v = fromY(yAxisVal);
    if (pctMode) return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
    if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString();
    return v.toFixed(2);
  }

  function setTool(next) {                       // 도구 토글 (상호 배타) + dragmode 전환
    tool = (tool === next) ? null : next;
    const dm = {hline: "drawline", fib: "drawline", meas: "drawrect"}[tool] || "pan";
    guard = true;
    Plotly.relayout(gd, {dragmode: dm}).then(() => { guard = false; });
    for (const [id, name] of [["bt-hline", "hline"], ["bt-fib", "fib"], ["bt-meas", "meas"]])
      document.getElementById(id).classList.toggle("on", tool === name);
    document.getElementById("tool-hint").textContent = {
      hline: "차트에 짧게 긋기 = 시작점 가격 수평선",
      fib: "고점↔저점으로 드래그 = 되돌림 레벨",
      meas: "측정할 구간을 박스로 드래그",
    }[tool] || "";
  }

  function applyDraw(shapes, anns) {             // 도형/주석 일괄 반영 (자기이벤트 가드)
    guard = true;
    Plotly.relayout(gd, {shapes: shapes, annotations: anns}).then(() => {
      guard = false;
      scheduleSave();                            // 도구 산출물 영속화
    });
  }

  function curAnns() { return (gd.layout.annotations || []).slice(); }

  function makeHline(sh, shapes, idx) {          // 그은 선 → 전폭 수평선 + 우측 가격 라벨
    let y = sh.y0;
    if (magnet) y = snapPoint(toMs(sh.x0), y)[1];
    shapes.splice(idx, 1);
    shapes.push({type: "line", name: "tool-hline", xref: "paper", x0: 0, x1: 1,
                 yref: "y", y0: y, y1: y, editable: true,
                 line: {color: "#f59e0b", width: 1.2, dash: "dot"}});
    const anns = curAnns();
    anns.push({name: "tool-hline", xref: "paper", x: 1, xanchor: "left", y: y, yref: "y",
               showarrow: false, text: "<b>" + fmtVal(y) + "</b>",
               font: {size: 10, color: "#ffffff"}, bgcolor: "#f59e0b", borderpad: 2});
    applyDraw(shapes, anns);
    setTool(null);
  }

  const FIB_LEVELS = [[0, "#787b86"], [0.236, "#f23645"], [0.382, "#ff9800"], [0.5, "#4caf50"],
                      [0.618, "#089981"], [0.786, "#00bcd4"], [1, "#787b86"]];

  function makeFib(sh, shapes, idx) {            // 그은 선 → 피보나치 되돌림 레벨
    let x0 = toMs(sh.x0), y0 = sh.y0, x1 = toMs(sh.x1), y1 = sh.y1;
    if (magnet) { [x0, y0] = snapPoint(x0, y0); [x1, y1] = snapPoint(x1, y1); }
    shapes.splice(idx, 1);
    const xa = toISO(Math.min(x0, x1)), xb = toISO(Math.max(x0, x1));
    const anns = curAnns();
    let prevY = null;
    for (const [r, c] of FIB_LEVELS) {
      const y = y1 - (y1 - y0) * r;              // 0 = 종점 · 1 = 시점 (TV 관례)
      if (prevY !== null)                        // 레벨 사이 옅은 밴드
        shapes.push({type: "rect", name: "tool-fib", xref: "x", x0: xa, x1: xb,
                     yref: "y", y0: prevY, y1: y, line: {width: 0}, fillcolor: c + "0d"});
      shapes.push({type: "line", name: "tool-fib", xref: "x", x0: xa, x1: xb,
                   yref: "y", y0: y, y1: y, line: {color: c, width: 1}});
      anns.push({name: "tool-fib", x: xb, xanchor: "left", y: y, yref: "y", showarrow: false,
                 text: (r * 100).toFixed(1) + " · " + fmtVal(y),
                 font: {size: 9, color: c}, bgcolor: "rgba(19,23,34,.75)"});
      prevY = y;
    }
    applyDraw(shapes, anns);
    setTool(null);
  }

  function makeMeasure(sh, shapes, idx) {        // 박스 → Δ가격·Δ%·봉수·기간 (1개 유지)
    let x0 = toMs(sh.x0), y0 = sh.y0, x1 = toMs(sh.x1), y1 = sh.y1;
    if (magnet) { [x0, y0] = snapPoint(x0, y0); [x1, y1] = snapPoint(x1, y1); }
    const v0 = fromY(y0), v1 = fromY(y1), up = v1 >= v0;
    const color = up ? "#26a69a" : "#ef5350";
    let stat;
    if (pctMode) stat = "Δ " + (up ? "+" : "") + (v1 - v0).toFixed(2) + "%p";
    else stat = "Δ " + (up ? "+" : "") + (v1 - v0).toLocaleString(undefined,
                    {maximumFractionDigits: 2})
              + " (" + (up ? "+" : "") + (v0 ? ((v1 / v0 - 1) * 100).toFixed(2) : "?") + "%)";
    let nBars = 0;
    const lo = Math.min(x0, x1), hi = Math.max(x0, x1);
    for (let i = lowerBound(lo); i < bounds.length && bounds[i][0] <= hi; i++) nBars++;
    const days = Math.round((hi - lo) / 86400000);
    // 이전 측정 제거 (측정은 항상 1개)
    const kept = shapes.filter((s, i) => i !== idx && (s.name || "") !== "tool-meas");
    kept.push({type: "rect", name: "tool-meas", xref: "x", x0: toISO(lo), x1: toISO(hi),
               yref: "y", y0: y0, y1: y1, editable: true,
               line: {color: color, width: 1, dash: "dot"}, fillcolor: color + "14"});
    const anns = curAnns().filter((a) => (a.name || "") !== "tool-meas");
    anns.push({name: "tool-meas", x: toISO((lo + hi) / 2), y: Math.max(y0, y1), yref: "y",
               yanchor: "bottom", showarrow: false,
               text: "<b>" + stat + "</b> · " + nBars + "봉 · " + days + "일",
               font: {size: 10, color: "#ffffff"}, bgcolor: color, borderpad: 3, opacity: 0.9});
    applyDraw(kept, anns);
    setTool(null);
  }

  function snapShape(sh, shapes) {               // 🧲 — 선/박스 끝점을 봉·OHLC 로
    if (sh.type === "line" && sh.xref !== "paper") {
      const p0 = snapPoint(toMs(sh.x0), sh.y0), p1 = snapPoint(toMs(sh.x1), sh.y1);
      sh.x0 = toISO(p0[0]); sh.y0 = p0[1]; sh.x1 = toISO(p1[0]); sh.y1 = p1[1];
    } else if (sh.type === "rect") {
      const p0 = snapPoint(toMs(sh.x0), sh.y0), p1 = snapPoint(toMs(sh.x1), sh.y1);
      sh.x0 = toISO(p0[0]); sh.y0 = p0[1]; sh.x1 = toISO(p1[0]); sh.y1 = p1[1];
    } else if (sh.type === "line" && sh.xref === "paper" && (sh.name || "") === "tool-hline") {
      const y = snapPoint(bounds.length ? bounds[bounds.length - 1][0] : 0, sh.y0)[1];
      sh.y0 = sh.y1 = y;                         // 수평선 편집 — 가격만 스냅
      const nth = shapes.filter(s => (s.name || "") === "tool-hline").indexOf(sh);
      const anns = curAnns();
      let seen = -1;
      for (const a of anns) {                    // n번째 수평선 ↔ n번째 라벨 동기화
        if ((a.name || "") === "tool-hline" && ++seen === nth) {
          a.y = y; a.text = "<b>" + fmtVal(y) + "</b>"; break;
        }
      }
      applyDraw(shapes, anns);
      return true;
    } else return false;
    applyDraw(shapes, curAnns());
    return true;
  }

  function reconcileHlineAnns(shapes) {          // 지우개로 수평선 삭제 시 고아 라벨 정리
    const ys = shapes.filter(s => (s.name || "") === "tool-hline").map(s => s.y0);
    const anns = curAnns();
    const kept = anns.filter(a => (a.name || "") !== "tool-hline"
        || ys.some(y => Math.abs(y - a.y) < 1e-9));
    if (kept.length !== anns.length) { applyDraw(shapes.slice(), kept); return true; }
    return false;
  }

  function handleShapes(e) {                     // 새 도형/편집 relayout → 도구·자석 적용
    let idx = null, isNew = false;
    if (Array.isArray(e.shapes)) { idx = e.shapes.length - 1; isNew = true; }
    else {
      for (const k of Object.keys(e)) {
        const m = k.match(/^shapes\[(\d+)\]\./);
        if (m) { idx = +m[1]; break; }
      }
    }
    if (isNew && reconcileHlineAnns((gd.layout.shapes || []).slice())) return true;
    if (idx == null || idx < 0) return false;
    const shapes = (gd.layout.shapes || []).slice();
    const sh = shapes[idx];
    if (!sh || sh.type === "path") return true;  // 자유곡선 — 스냅 제외
    if (sh.yref && sh.yref !== "y") return true; // 가격 패널 외(거래량·RSI) — 제외
    // 서버 도형(평단선·현재가선)은 사용자 편집/자석 대상 아님 — 스냅이 평단을 움직이면 안 됨
    if (idx < baseShapeCount && !String(sh.name || "").startsWith("tool-")) return true;
    if (isNew && tool === "hline" && sh.type === "line") { makeHline(sh, shapes, idx); return true; }
    if (isNew && tool === "fib" && sh.type === "line") { makeFib(sh, shapes, idx); return true; }
    if (isNew && tool === "meas" && sh.type === "rect") { makeMeasure(sh, shapes, idx); return true; }
    if (magnet && (sh.name || "") !== "tool-meas" && !String(sh.name || "").startsWith("tool-fib"))
      return snapShape(sh, shapes) || true;
    return true;
  }

  document.getElementById("bt-mag").onclick = (ev) => {
    magnet = !magnet;
    ev.target.classList.toggle("on", magnet);
  };
  document.getElementById("bt-hline").onclick = () => setTool("hline");
  document.getElementById("bt-fib").onclick = () => setTool("fib");
  document.getElementById("bt-meas").onclick = () => setTool("meas");
  document.getElementById("bt-clear").onclick = () => {   // 서버 오버레이만 남기고 제거
    // 도형 = 보존 복사본으로 되돌림(직접 그린 것·도구 도형 모두 제거·인덱스 밀림 무관).
    // 주석 = 이름 필터 (tn-hi/tn-lo 콜아웃은 팬 중 갱신되므로 복사본 복원 금지)
    const anns = curAnns().filter((a) => !String(a.name || "").startsWith("tool-"));
    applyDraw(JSON.parse(JSON.stringify(baseShapes)), anns);
    try { if (storeKey) localStorage.removeItem("tndraw:" + storeKey); } catch (e) {}
    document.getElementById("detail").style.display = "none";
  };

  Plotly.newPlot(gd, fig.data, fig.layout, @@CONFIG@@).then(() => {
    // 저장된 드로잉 복원 — 서버 도형 뒤에 append (지우기·보호 가드와 정합).
    // 하단 지표 구성이 바뀌어 사라진 서브패널 축(y3 등)을 참조하는 도형은 제외(고아 방지)
    const saved = loadDrawings();
    if (saved && (saved.shapes.length || saved.anns.length)) {
      const axes = new Set(Object.keys(fig.layout).filter((k) => k.startsWith("yaxis"))
        .map((k) => "y" + k.slice(5)));
      const okRef = (r) => !r || r === "paper" || axes.has(r);
      guard = true;
      Plotly.relayout(gd, {
        shapes: (gd.layout.shapes || []).concat(saved.shapes.filter((s) => okRef(s.yref))),
        annotations: (gd.layout.annotations || []).concat(
          saved.anns.filter((a) => okRef(a.yref))),
      }).then(() => { guard = false; });
    }
    const last = bounds.length ? bounds[bounds.length - 1][0] : null;
    if (last && @@VIEW_MS@@) {                   // 초기 표시창 (기간 라디오)
      const x0 = last - @@VIEW_MS@@;
      const first = bounds[0][0];
      if (x0 > first) {
        guard = true;
        Plotly.relayout(gd, {"xaxis.range": [new Date(x0).toISOString(),
                                             new Date(last + @@VIEW_MS@@ * 0.02).toISOString()]})
          .then(() => { guard = false; rescale(); });
      }
    }
    let gestureTimer = null;
    gd.on("plotly_relayouting", (e) => {         // 드래그 **중** — y 가 실시간 따라옴 (스냅 제거)
      if (guard) return;
      const xr = evXRange(e);
      if (!xr) return;
      dragging = true;
      muteHover();
      setTarget(xr[0], xr[1]);
    });
    // 휠 줌은 틱마다 relayout 이 발생(연속 제스처) — 틱 중엔 y 목표 갱신만(저비용),
    // 콜아웃·hover 복원 등 무거운 마무리는 마지막 틱 후 160ms 에 1회 (줌 랙 제거)
    gd.on("plotly_relayout", (e) => {
      if (guard) return;
      dragging = false;
      if (handleShapes(e)) { scheduleSave(); return; }   // 드로잉 도구·🧲 자석 경로 + 영속화
      const keys = Object.keys(e || {});
      if (keys.some(k => k.startsWith("xaxis.range")) || e["xaxis.autorange"]) {
        muteHover();
        const xr = evXRange(e);
        if (xr) setTarget(xr[0], xr[1]);
        clearTimeout(gestureTimer);
        gestureTimer = setTimeout(finishGesture, 160);
      }
    });
    gd.on("plotly_hover", (ev) => {              // 데이터창 — 호버 봉 OHLC 리드아웃
      const p = (ev.points || [])[0];
      if (!p || p.x == null) return;
      const ms = typeof p.x === "number" ? p.x : Date.parse(p.x);
      if (ms === ms) ohlcReadout(ms);            // NaN 가드
    });
    gd.on("plotly_unhover", () => {              // 이탈 시 마지막 봉으로 복귀
      if (bounds.length) ohlcReadout(bounds[bounds.length - 1][0]);
    });
    gd.on("plotly_click", (ev) => {              // ▲▼ 마커 클릭 → 인차트 상세
      const p = (ev.points || [])[0];
      if (!p || !p.customdata) return;
      const c = p.customdata;                    // [event_id, 구분, qty, px, avg, account, source, ts, note, cur]
      const el = document.getElementById("detail");
      el.style.display = "block";
      el.innerHTML = `<b>${c[1]}</b> ${c[7] || ""} &nbsp;·&nbsp; 수량 ${c[2]}주 ` +
        `&nbsp;·&nbsp; 체결 ${c[9] || ""} ${Number(c[3]).toLocaleString()} ` +
        `&nbsp;·&nbsp; 평단 ${c[4] != null ? Number(c[4]).toLocaleString() : "—"} ` +
        `<span style="color:#9198a6">${c[5] || ""} · ${c[6] || ""} ${c[8] ? "· " + c[8] : ""}</span>`;
    });
  });
})();
</script>
"""


def pannable_chart_html(fig, hist, *, height: int = 460, view_days=None,
                        vol_axis: str | None = None,
                        bounds_json: str | None = None,
                        fit_viewport: bool = False,
                        pct_mode: bool = False,
                        y_log: bool = False,
                        store_key: str | None = None) -> str:
    """fig(charts.price_chart 산출) → 자동 y 리스케일·드로잉 도구·인차트 마커 상세 임베드 HTML.

    bounds_json — y 맞춤 프레임 오버라이드 (비교 모드: compare_bounds_json 의 % 프레임).
    pct_mode — 비교(%) 모드: 도구 라벨을 가격 대신 % 로 포맷.
    y_log — 로그 스케일: 도형/축 y 좌표가 log10 공간 (스냅·측정이 실가격으로 환산).
    store_key — 드로잉 영속화 localStorage 키(예: "NVDA:1d:lin"). None=비영속.
                스케일(lin/log/pct)을 키에 포함해야 좌표계 혼선이 없다(호출부 책임).
    """
    bounds = bounds_json if bounds_json is not None else price_bounds_json(hist)
    config = json.dumps({
        "scrollZoom": True, "displaylogo": False,   # 모드바 = hover 시만 (기본) — 상시 노출 제거
        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
        "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "eraseshape"],
    })
    try:
        last_close = float(hist["Close"].iloc[-1])
    except Exception:
        last_close = 0.0
    html = (_TEMPLATE
            .replace("@@CDN@@", _plotlyjs_cdn())
            .replace("@@HEIGHT@@", str(int(height)))
            .replace("@@VIEW_MS@@", str(int(view_days) * 86400000 if view_days else 0))
            .replace("@@VOL_AXIS@@", json.dumps(vol_axis))
            .replace("@@FIT_VH@@", json.dumps(bool(fit_viewport)))
            .replace("@@PCT_MODE@@", json.dumps(bool(pct_mode)))
            .replace("@@Y_LOG@@", json.dumps(bool(y_log)))
            .replace("@@LAST_CLOSE@@", json.dumps(last_close))
            .replace("@@STORE_KEY@@", json.dumps(store_key))
            .replace("@@CONFIG@@", config)
            .replace("@@BOUNDS@@", bounds)
            .replace("@@FIG@@", fig.to_json()))       # fig JSON 은 마지막 (토큰 오염 차단)
    return html
