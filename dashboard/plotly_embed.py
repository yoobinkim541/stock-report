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
          line-height:1.5; white-space:nowrap; }
  .tbtn:hover { border-color:#2f81f7; }
  .tbtn.on { border-color:#2f81f7; color:#2f81f7; background:rgba(47,129,247,.08); }
  .tsep { width:1px; align-self:stretch; background:#1e222d; margin:0 2px; }
  #tool-hint { color:#9198a6; font-size:11px; margin-left:4px; }
  /* 좌측 도구 독 (풀뷰 — TradingView 배치) */
  #wrap.dock { display:flex; gap:8px; align-items:stretch; }
  #wrap.dock #tools { flex-direction:column; align-items:stretch; width:112px;
                      margin:0; align-self:flex-start; position:sticky; top:0; }
  #wrap.dock .tsep { width:auto; height:1px; margin:2px 0; }
  #wrap.dock #tool-hint { margin:2px 0 0; white-space:normal; }
  #wrap.dock #ohlcbar { order:-1; margin:0 0 4px; white-space:normal; }
  #wrap.dock #chartcol { flex:1; min-width:0; }
</style>
<div id="wrap">
<div id="tools">
  <button id="bt-mag" class="tbtn on" title="그리기·편집 시 봉의 시가/고가/저가/종가에 착 붙음">🧲 자석</button>
  <button id="bt-hline" class="tbtn" title="차트에 짧게 긋기 = 시작점 가격 수평선">─ 수평선</button>
  <button id="bt-vline" class="tbtn" title="차트에 짧게 긋기 = 시작점 날짜 수직선">│ 수직선</button>
  <button id="bt-cross" class="tbtn" title="차트에 짧게 긋기 = 시작점 크로스라인(수평+수직)">✚ 크로스</button>
  <button id="bt-ray" class="tbtn" title="두 점을 긋면 오른쪽으로 무한 연장">↗ 레이</button>
  <button id="bt-ext" class="tbtn" title="두 점을 긋면 양방향 무한 연장">⤢ 연장선</button>
  <span class="tsep"></span>
  <button id="bt-fib" class="tbtn" title="고점↔저점 긋기 = 되돌림 레벨 자동">🔱 피보나치</button>
  <button id="bt-meas" class="tbtn" title="박스 드래그 = Δ가격·Δ%·봉수·기간">📏 측정</button>
  <button id="bt-long" class="tbtn" title="진입→목표 위로 박스 드래그 = 롱 포지션(RR 1:1 손절 자동 · 조정은 지우고 다시)">📈 롱</button>
  <button id="bt-short" class="tbtn" title="진입→목표 아래로 박스 드래그 = 숏 포지션(RR 1:1 손절 자동)">📉 숏</button>
  <button id="bt-text" class="tbtn" title="차트에 짧게 긋기 = 시작점에 텍스트 메모">📝 메모</button>
  <span class="tsep"></span>
  <button id="bt-reg" class="tbtn" title="구간을 박스로 드래그 = 종가 회귀 추세선 ±2σ 채널">📐 회귀추세</button>
  <button id="bt-avwap" class="tbtn" title="차트에 짧게 긋기 = 그 봉부터 고정(앵커드) VWAP">⚓ 고정VWAP</button>
  <button id="bt-vprof" class="tbtn" title="구간을 박스로 드래그 = 가격대별 거래량 프로필 + POC">📊 볼륨프로필</button>
  <span class="tsep"></span>
  <button id="bt-replay" class="tbtn" title="과거 시점으로 되감아 한 봉씩 재생 — 매매 연습 (미래 봉 가림)">⏪ 리플레이</button>
  <button id="bt-clear" class="tbtn" title="직접 그린 도형 전체 제거">🗑 지우기</button>
  <span id="tool-hint"></span>
  <span id="ohlcbar" style="margin-left:auto;font:11px 'JetBrains Mono', ui-monospace, monospace;
        color:#9198a6;white-space:nowrap"></span>
</div>
<div id="chartcol">
<div id="replaybar" style="display:none; gap:6px; align-items:center; margin:0 0 6px 2px;
     font:12px Pretendard, -apple-system, sans-serif; color:#d1d4dc">
  <button id="rp-back" class="tbtn" title="10봉 뒤로">⏮</button>
  <button id="rp-play" class="tbtn" title="재생 / 일시정지">▶</button>
  <button id="rp-step" class="tbtn" title="한 봉 앞으로">▶|</button>
  <select id="rp-speed" class="tbtn" title="재생 속도">
    <option value="1">1×</option><option value="3">3×</option><option value="10">10×</option>
  </select>
  <input id="rp-slider" type="range" min="1" max="100" value="50" style="flex:1">
  <button id="rp-exit" class="tbtn" title="리플레이 종료">✕</button>
</div>
<div id="chart" style="width:100%;min-height:@@HEIGHT@@px"></div>
</div>
</div>
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
  if (@@DOCK@@) document.getElementById("wrap").classList.add("dock");   // 풀뷰 좌측 도구 독
  const live = @@LIVE@@;                         // ⚡ live — 피더 localStorage 실시간 패치
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
        guard++;
        Plotly.relayout(gd, {height: h}).then(() => { unguard(); });
      });
    } catch (e) {}
  }
  // y축은 전부 프로그램 제어(자동 맞춤) — 사용자 팬이 y를 끌지 않게 고정해
  // '드래그 중 y 싸움 → 종료 시 스냅' 흔들림의 근원을 제거 (TradingView 방식)
  for (const k of Object.keys(fig.layout)) {
    if (k.startsWith("yaxis")) fig.layout[k].fixedrange = true;
  }
  const hoverMode = fig.layout.hovermode || "x unified";
  // guard = **카운터** (boolean 금지). plotly relayout 은 자기 이벤트를 비동기 emit 하는데
  // (emit 은 항상 해당 호출의 .then 보다 앞 — plotly.js 3.6 소스 확정) 여러 relayout 이
  // 겹치면 boolean 은 다른 호출의 .then 이 조기 해제 → 메아리가 새어 무한 루프(탭 프리즈).
  // 카운터는 전 in-flight 가 끝나야 0 — 자기 메아리는 항상 guard>0 구간에 도착한다.
  let guard = 0, dragging = false, hoverOff = false;
  const unguard = () => { guard = Math.max(0, guard - 1); };
  let drawGuard = 0;                             // 도형/주석 자기 relayout 전용 카운터
  const undraw = () => { drawGuard = Math.max(0, drawGuard - 1); };
  // ⚠️ plotly 는 {shapes:[...]} relayout 의 자기 이벤트를 **비동기**로(promise .then 이
  // guard=false 로 되돌린 뒤) emit 한다 → boolean guard 만으론 자기 메아리를 못 막아,
  // applyDraw→메아리→magnet 재스냅→applyDraw 무한 루프로 탭이 얼어붙었다(실측 확정).
  // 방어는 **내용 기반**(카운터는 sync/async/수동 emit 에 드리프트): (1) 새 draw 이벤트의
  // 마지막 도형이 이미 우리가 만든 tool-*/서버 도형이면 = 자기 메아리 → 무시,
  // (2) 자석 snapShape 는 좌표가 실제로 바뀔 때만 applyDraw(멱등 — 이미 스냅된 메아리는
  //     재적용 안 해 루프가 끊김). 두 가드가 named/unnamed 도형을 각각 커버.

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

  let replayCut = null;                          // ⏪ 리플레이 컷(ms) — 이후 봉은 커튼+클램프

  function yFit(x0, x1) {                        // 보이는 구간 고저·거래량 최대 + 패딩
    if (!bounds.length) return null;
    if (replayCut !== null) x1 = Math.min(x1, replayCut);   // 미래 봉이 y 를 누출하면 안 됨
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
      busy = true; guard++; lastApply = now;
      const t0 = performance.now();
      const upd = {"yaxis.range": curY.price.slice()};
      if (volAxis && !dragging) upd[volAxis + ".range"] = [0, curY.vol[1]];
      Plotly.relayout(gd, upd).then(() => {
        costMs = costMs * 0.7 + (performance.now() - t0) * 0.3;   // 비용 EMA
        busy = false; unguard();
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
    if (replayCut !== null) x1 = Math.min(x1, replayCut);   // 리플레이 — 미래 극값 누출 차단
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
    hoverOff = true; guard++;
    Plotly.relayout(gd, {hovermode: false}).then(() => { unguard(); });
  }

  function finishGesture() {                     // 제스처 끝 1회 — 콜아웃·hover 복원
    const xr = gd.layout.xaxis.range;
    if (!xr) return;
    const x0 = Date.parse(xr[0]), x1 = Date.parse(xr[1]);
    setTarget(x0, x1);                           // y 는 lerp 루프가 수렴
    saveView(xr);                                // 뷰 위치 유지(60s 규칙) — 원문 저장
    const upd = {};
    callouts(x0, x1, upd);                       // 무거운 주석 갱신은 여기서만
    if (hoverOff) { upd["hovermode"] = hoverMode; hoverOff = false; }
    if (Object.keys(upd).length) {
      guard++;
      Plotly.relayout(gd, upd).then(() => { unguard(); });
    }
  }
  const rescale = finishGesture;                 // 초기 표시창 경로 하위호환

  // ── 커스텀 크로스헤어 — plotly 스파이크 대신 순수 DOM 오버레이 ──
  // 스파이크는 마우스무브마다 plotly 재그리기 유발(다중 트레이스 스터터·성능 회귀 확정).
  // DOM translate 는 리플로우/재그리기 0 — rAF 스로틀 + 가격 버블(y축 매핑·log 대응).
  const xhV = document.createElement("div");
  const xhH = document.createElement("div");
  const xhY = document.createElement("div");
  xhV.style.cssText = "position:absolute;top:0;left:0;width:0;border-left:1px dashed #6b7385;" +
    "pointer-events:none;display:none;z-index:3";
  xhH.style.cssText = "position:absolute;top:0;left:0;height:0;border-top:1px dashed #6b7385;" +
    "pointer-events:none;display:none;z-index:3";
  xhY.style.cssText = "position:absolute;top:0;left:0;pointer-events:none;display:none;z-index:4;" +
    "background:#2a2e39;color:#d1d4dc;font:10px 'JetBrains Mono',monospace;" +
    "padding:1px 4px;border-radius:3px;transform-origin:left center";
  let xhRaf = null, xhEvt = null;                      // 부착은 newPlot 후 (newPlot 이 div 를 비움)
  function xhHide() { xhV.style.display = xhH.style.display = xhY.style.display = "none"; }
  function xhApply() {
    xhRaf = null;
    const e = xhEvt;
    if (!e) return;
    const drag = gd.querySelector(".nsewdrag");        // 메인(가격) 플롯 영역
    if (!drag) return;
    const pr = drag.getBoundingClientRect(), gr = gd.getBoundingClientRect();
    if (e.clientX < pr.left || e.clientX > pr.right
        || e.clientY < pr.top || e.clientY > pr.bottom) { xhHide(); return; }
    const x = e.clientX - gr.left, y = e.clientY - gr.top;
    xhV.style.display = xhH.style.display = "block";
    xhV.style.transform = "translateX(" + x + "px)";
    xhV.style.top = (pr.top - gr.top) + "px";
    xhV.style.height = pr.height + "px";
    xhH.style.transform = "translateY(" + y + "px)";
    xhH.style.left = (pr.left - gr.left) + "px";
    xhH.style.width = pr.width + "px";
    const yr = gd.layout.yaxis && gd.layout.yaxis.range;
    if (yr) {                                          // y 가격 버블 (축좌표 → fmtVal 이 환산)
      const frac = (e.clientY - pr.top) / pr.height;
      xhY.textContent = fmtVal(yr[1] + (yr[0] - yr[1]) * frac);
      xhY.style.display = "block";
      xhY.style.transform = "translate(" + (pr.right - gr.left + 2) + "px, " + (y - 8) + "px)";
    }
  }
  gd.addEventListener("mousemove", (e) => {
    xhEvt = e;
    if (!xhRaf) xhRaf = requestAnimationFrame(xhApply);
  });
  gd.addEventListener("mouseleave", xhHide);

  // ── OHLC 데이터창 — 호버 봉의 시·고·저·종·거래량·등락% 리드아웃 (bounds 재사용) ──
  const ohlcEl = document.getElementById("ohlcbar");
  function ohlcReadout(ms) {
    if (!ohlcEl || !bounds.length) return;
    if (replayCut !== null && ms > replayCut) ms = replayCut;   // 커튼 뒤 봉 값 누출 차단
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
      const shapes = (gd.layout.shapes || []).slice(baseShapeCount)
        .filter((s) => (s.name || "") !== "replay-curtain");   // 리플레이 커튼은 일시 상태
      const anns = (gd.layout.annotations || []).filter(
        (a) => String(a.name || "").startsWith("tool-"));
      const k = "tndraw:" + storeKey;
      if (!shapes.length && !anns.length && !vwapAnchors.length) {
        localStorage.removeItem(k);
        return;
      }
      // vwaps = ⚓ 앵커(ms)만 저장 — 트레이스는 로드 시 bounds 로 재계산 (v1 하위호환)
      localStorage.setItem(k, JSON.stringify({v: 1, shapes: shapes, anns: anns,
                                              vwaps: vwapAnchors.slice()}));
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

  // ── 뷰(x 표시창) 유지 — 60초 신선 규칙: ⚡자동갱신·설정 변경 리런에선 보던 위치
  // 그대로, 새로 연 세션(>60s)은 기간 라디오 기본창 (TradingView 이어보기 절충) ──
  // ⚠️ 저장은 plotly 가 쓴 range **문자열 그대로**(무파싱) — plotly 는 naive 문자열을
  // UTC 로 합성하는데 Date.parse 는 로컬로 해석해, 파싱-재직렬화 왕복이 KST 에서
  // 9시간씩 뷰를 밀리게 한다(자동갱신마다 누적 — 적대 리뷰 확정). 원문 왕복은 무손실.
  function saveView(range) {
    if (!storeKey || !range) return;
    try {
      localStorage.setItem("tnview:" + storeKey,           // vm = 기간 라디오 식별 —
                           JSON.stringify({view: [range[0], range[1]],  // 라디오 변경 시 무시
                                           ts: Date.now(), vm: @@VIEW_MS@@}));
    } catch (e) {}
  }
  function loadFreshView() {
    if (!storeKey) return null;
    try {
      const v = JSON.parse(localStorage.getItem("tnview:" + storeKey) || "null");
      if (v && Array.isArray(v.view) && Date.now() - (v.ts || 0) < 60000
          && v.vm === @@VIEW_MS@@) return v.view;
    } catch (e) {}
    return null;
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

  const TOOL_BTNS = [["bt-hline", "hline"], ["bt-vline", "vline"], ["bt-cross", "cross"],
                     ["bt-ray", "ray"], ["bt-ext", "ext"], ["bt-fib", "fib"],
                     ["bt-meas", "meas"], ["bt-long", "long"], ["bt-short", "short"],
                     ["bt-text", "text"], ["bt-reg", "reg"], ["bt-avwap", "avwap"],
                     ["bt-vprof", "vprof"]];

  function setTool(next) {                       // 도구 토글 (상호 배타) + dragmode 전환
    // 고정VWAP·볼륨프로필은 실가격×거래량 계산 — 비교(%) 프레임(4열 bounds)에선 불가
    if (pctMode && (next === "avwap" || next === "vprof")) {
      document.getElementById("tool-hint").textContent = "비교(%) 모드에선 사용 불가";
      return;
    }
    tool = (tool === next) ? null : next;
    const dm = {hline: "drawline", vline: "drawline", cross: "drawline",
                ray: "drawline", ext: "drawline", fib: "drawline", text: "drawline",
                avwap: "drawline",
                meas: "drawrect", long: "drawrect", short: "drawrect",
                reg: "drawrect", vprof: "drawrect"}[tool] || "pan";
    guard++;
    Plotly.relayout(gd, {dragmode: dm}).then(() => { unguard(); });
    for (const [id, name] of TOOL_BTNS)
      document.getElementById(id).classList.toggle("on", tool === name);
    document.getElementById("tool-hint").textContent = {
      hline: "차트에 짧게 긋기 = 시작점 가격 수평선",
      vline: "차트에 짧게 긋기 = 시작점 날짜 수직선",
      cross: "차트에 짧게 긋기 = 시작점 크로스라인",
      ray: "두 점을 그으면 오른쪽으로 연장",
      ext: "두 점을 그으면 양방향 연장",
      fib: "고점↔저점으로 드래그 = 되돌림 레벨",
      meas: "측정할 구간을 박스로 드래그",
      long: "진입→목표 위로 드래그 = 롱 (손절 RR 1:1 자동)",
      short: "진입→목표 아래로 드래그 = 숏 (손절 RR 1:1 자동)",
      text: "차트에 짧게 긋기 = 그 지점 메모",
      reg: "회귀 구간을 박스로 드래그 = 추세선 ±2σ 채널",
      avwap: "차트에 짧게 긋기 = 그 봉부터 고정 VWAP",
      vprof: "프로필 구간을 박스로 드래그 = 가격대별 거래량 + POC",
    }[tool] || "";
  }

  function applyDraw(shapes, anns) {             // 도형/주석 일괄 반영 (자기이벤트 가드)
    drawGuard++;                                 // 도형 메아리 전용 카운터 — 루프 차단 핵심
    Plotly.relayout(gd, {shapes: shapes, annotations: anns}).then(() => {
      undraw();
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

  function makeVline(sh, shapes, idx) {          // 그은 선 → 전고 수직선 + 상단 날짜 라벨
    let ms = toMs(sh.x0);
    if (magnet) ms = snapPoint(ms, sh.y0)[0];
    shapes.splice(idx, 1);
    const x = toISO(ms);
    shapes.push({type: "line", name: "tool-vline", xref: "x", x0: x, x1: x,
                 yref: "paper", y0: 0, y1: 1,
                 line: {color: "#f59e0b", width: 1.2, dash: "dot"}});
    const anns = curAnns();
    anns.push({name: "tool-vline", x: x, xref: "x", y: 1, yref: "paper",
               yanchor: "bottom", showarrow: false,
               text: new Date(ms).toISOString().slice(0, 10),
               font: {size: 9, color: "#f59e0b"}, bgcolor: "rgba(19,23,34,.75)"});
    applyDraw(shapes, anns);
    setTool(null);
  }

  function makeCross(sh, shapes, idx) {          // 크로스라인 = 수평선 + 수직선 세트
    let ms = toMs(sh.x0), y = sh.y0;
    if (magnet) { const p = snapPoint(ms, y); ms = p[0]; y = p[1]; }
    shapes.splice(idx, 1);
    const x = toISO(ms);
    shapes.push({type: "line", name: "tool-cross", xref: "paper", x0: 0, x1: 1,
                 yref: "y", y0: y, y1: y, line: {color: "#8b93a7", width: 1, dash: "dot"}});
    shapes.push({type: "line", name: "tool-cross", xref: "x", x0: x, x1: x,
                 yref: "paper", y0: 0, y1: 1, line: {color: "#8b93a7", width: 1, dash: "dot"}});
    const anns = curAnns();
    anns.push({name: "tool-cross", xref: "paper", x: 1, xanchor: "left", y: y, yref: "y",
               showarrow: false, text: fmtVal(y),
               font: {size: 9, color: "#8b93a7"}, bgcolor: "rgba(19,23,34,.75)"});
    applyDraw(shapes, anns);
    setTool(null);
  }

  function makeRay(sh, shapes, idx, both) {      // 레이/연장선 — (ms,y) 기울기로 외삽
    let x0 = toMs(sh.x0), y0 = sh.y0, x1 = toMs(sh.x1), y1 = sh.y1;
    if (magnet) { [x0, y0] = snapPoint(x0, y0); [x1, y1] = snapPoint(x1, y1); }
    if (x1 === x0) { setTool(null); return; }    // 세로 = 기울기 무한 — 수직선 도구 안내
    if (x1 < x0) { const t = [x0, y0]; [x0, y0] = [x1, y1]; [x1, y1] = t; }
    const slope = (y1 - y0) / (x1 - x0);
    const lastMs = bounds.length ? bounds[bounds.length - 1][0] : x1;
    const span = Math.max(lastMs - (bounds.length ? bounds[0][0] : x0), x1 - x0);
    const xr = lastMs + span * 0.25;             // 오른쪽 여유 연장 (팬 시에도 길게)
    const yr = y0 + slope * (xr - x0);
    let xs = x0, ys = y0;
    if (both) { xs = x0 - (x1 - x0) - span * 0.25; ys = y0 + slope * (xs - x0); }
    shapes.splice(idx, 1);
    shapes.push({type: "line", name: both ? "tool-ext" : "tool-ray", xref: "x",
                 x0: toISO(xs), x1: toISO(xr), yref: "y", y0: ys, y1: yr,
                 line: {color: "#2f81f7", width: 1.4}});
    applyDraw(shapes, curAnns());
    setTool(null);
  }

  function makeText(sh, shapes, idx) {           // 메모 — 시작점에 텍스트 주석
    let ms = toMs(sh.x0), y = sh.y0;
    if (magnet) { const p = snapPoint(ms, y); ms = p[0]; y = p[1]; }
    shapes.splice(idx, 1);
    let txt = "";
    try { txt = (window.prompt("메모 내용", "") || "").slice(0, 60); } catch (e) {}
    const anns = curAnns();
    if (txt)
      anns.push({name: "tool-note", x: toISO(ms), y: y, yref: "y", showarrow: true,
                 arrowhead: 2, ax: 0, ay: -28, text: txt,
                 font: {size: 11, color: "#e8e8ea"}, bgcolor: "#2a2e39",
                 bordercolor: "#3d4354", borderpad: 4});
    applyDraw(shapes, anns);
    setTool(null);
  }

  function makePosition(sh, shapes, idx, dir) {  // 롱/숏 포지션 — RR 박스 (TV 스타일)
    let y0 = sh.y0, y1 = sh.y1;                  // 드래그 시작=진입, 끝=목표
    let x0 = toMs(sh.x0), x1 = toMs(sh.x1);
    if (magnet) { [x0, y0] = snapPoint(x0, y0); [x1, y1] = snapPoint(x1, y1); }
    const entry = fromY(y0), target = fromY(y1);
    if (!entry || entry === target) { setTool(null); return; }
    // 방향 가드 — 롱은 목표>진입, 숏은 목표<진입. 반대로 그으면 목표=손절 넌센스가
    // 되므로(적대 리뷰 확정) 도형을 버리고 힌트만 갱신, 도구는 유지해 재시도.
    if ((dir === "long") !== (target > entry)) {
      shapes.splice(idx, 1);
      applyDraw(shapes, curAnns());
      document.getElementById("tool-hint").textContent =
        dir === "long" ? "롱은 진입에서 위(목표)로 드래그하세요" : "숏은 진입에서 아래로 드래그하세요";
      return;
    }
    // 손절 = **축 공간** 대칭 RR 1:1 — 선형축에선 가격 대칭(종전과 동일), 로그축에선
    // 퍼센트 대칭이라 손절가가 0 이하(log10=NaN)로 떨어질 수 없다 (적대 리뷰 확정 픽스)
    const stopAx = y0 - (y1 - y0);
    const stop = fromY(stopAx);
    const xa = toISO(Math.min(x0, x1)), xb = toISO(Math.max(x0, x1));
    shapes.splice(idx, 1);
    shapes.push({type: "rect", name: "tool-pos", xref: "x", x0: xa, x1: xb,   // 보상 존
                 yref: "y", y0: y0, y1: y1,
                 line: {color: "#26a69a", width: 1}, fillcolor: "#26a69a22"});
    shapes.push({type: "rect", name: "tool-pos", xref: "x", x0: xa, x1: xb,   // 위험 존
                 yref: "y", y0: y0, y1: stopAx,
                 line: {color: "#ef5350", width: 1}, fillcolor: "#ef535022"});
    const pctT = entry ? ((target / entry - 1) * 100) : 0;
    const pctS = entry ? ((stop / entry - 1) * 100) : 0;
    const anns = curAnns();
    anns.push({name: "tool-pos", x: toISO((Math.min(x0, x1) + Math.max(x0, x1)) / 2),
               y: y1, yref: "y", yanchor: dir === "long" ? "bottom" : "top", showarrow: false,
               text: "<b>" + (dir === "long" ? "롱" : "숏") + "</b> 진입 " + fmtVal(y0)
                     + " · 목표 " + fmtVal(y1) + " (" + (pctT >= 0 ? "+" : "") + pctT.toFixed(2)
                     + "%) · 손절 " + fmtVal(stopAx) + " (" + (pctS >= 0 ? "+" : "")
                     + pctS.toFixed(2) + "%) · RR 1:1",
               font: {size: 10, color: "#ffffff"},
               bgcolor: dir === "long" ? "#26a69a" : "#ef5350", borderpad: 3, opacity: 0.92});
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

  function makeReg(sh, shapes, idx) {            // 📐 구간 회귀 추세 — OLS 중심선 ±2σ 채널
    const lo = Math.min(toMs(sh.x0), toMs(sh.x1)), hi = Math.max(toMs(sh.x0), toMs(sh.x1));
    shapes.splice(idx, 1);
    const xs = [], ys = [];
    for (let i = lowerBound(lo); i < bounds.length && bounds[i][0] <= hi; i++) {
      const b = bounds[i];
      xs.push(b[0]);
      ys.push(toY(b.length >= 6 ? b[5] : b[1]));  // 종가 (로그축은 log 공간에서 회귀)
    }
    if (xs.length < 3) { applyDraw(shapes, curAnns()); setTool(null); return; }
    const n = xs.length;
    const mx = xs.reduce((a, v) => a + v, 0) / n, my = ys.reduce((a, v) => a + v, 0) / n;
    let sxy = 0, sxx = 0;
    for (let i = 0; i < n; i++) { sxy += (xs[i] - mx) * (ys[i] - my); sxx += (xs[i] - mx) * (xs[i] - mx); }
    const slope = sxx ? sxy / sxx : 0, icpt = my - slope * mx;
    let sd = 0;
    for (let i = 0; i < n; i++) { const r = ys[i] - (icpt + slope * xs[i]); sd += r * r; }
    sd = Math.sqrt(sd / n);
    const xa = toISO(xs[0]), xb = toISO(xs[n - 1]);
    const yA = icpt + slope * xs[0], yB = icpt + slope * xs[n - 1];
    for (const [off, w, dash] of [[0, 1.3, "solid"], [2 * sd, 1, "dot"], [-2 * sd, 1, "dot"]])
      shapes.push({type: "line", name: "tool-reg", xref: "x", x0: xa, x1: xb, yref: "y",
                   y0: yA + off, y1: yB + off, line: {color: "#2f81f7", width: w, dash: dash}});
    const vA = fromY(yA), vB = fromY(yB);
    const pct = vA ? ((vB / vA - 1) * 100) : 0;
    const anns = curAnns();
    anns.push({name: "tool-reg", x: xb, xanchor: "left", y: yB, yref: "y", showarrow: false,
               text: "회귀 " + (pct >= 0 ? "+" : "") + pct.toFixed(1) + "% · " + n + "봉 · ±2σ",
               font: {size: 9, color: "#2f81f7"}, bgcolor: "rgba(19,23,34,.75)"});
    applyDraw(shapes, anns);
    setTool(null);
  }

  // ── ⚓ 고정(앵커드) VWAP — 앵커 봉부터 누적 (저+고+종)/3 × 거래량 트레이스 ──
  // 도형이 아닌 **트레이스**(hover·범례 가능) — 영속화는 앵커(ms) 목록만 저장·재계산.
  const vwapAnchors = [];
  function vwapTrace(anchorMs) {
    if (pctMode) return null;                    // 비교(%) 프레임 — 실가격 없음
    const xs = [], ys = [];
    let pv = 0, vv = 0;
    for (let i = lowerBound(anchorMs); i < bounds.length; i++) {
      const b = bounds[i];
      if (b.length < 6) return null;
      const v = b[3] || 0;
      pv += ((b[1] + b[2] + b[5]) / 3) * v;
      vv += v;
      if (vv > 0) { xs.push(toISO(b[0])); ys.push(pv / vv); }
    }
    if (xs.length < 2) return null;
    return {x: xs, y: ys, mode: "lines", name: "고정 VWAP", showlegend: false,
            meta: "tool-avwap", hovertemplate: "고정 VWAP %{y:,.2f}<extra></extra>",
            line: {color: "#e879f9", width: 1.4, dash: "dot"}};
  }
  function addVwap(anchorMs) {
    const tr = vwapTrace(anchorMs);
    if (!tr) return false;
    vwapAnchors.push(anchorMs);
    try { Plotly.addTraces(gd, [tr]); } catch (e) {}
    scheduleSave();
    return true;
  }
  function clearVwaps() {
    const idxs = (gd.data || []).map((t, i) => (t && t.meta === "tool-avwap" ? i : -1))
      .filter((i) => i >= 0);
    if (idxs.length) { try { Plotly.deleteTraces(gd, idxs); } catch (e) {} }
    vwapAnchors.length = 0;
  }
  function makeAvwap(sh, shapes, idx) {
    let ms = toMs(sh.x0);
    if (magnet) ms = snapPoint(ms, sh.y0)[0];
    shapes.splice(idx, 1);
    const anns = curAnns();
    const tr = vwapTrace(ms);
    if (tr)                                      // ⚓ 앵커 표식 (지우기·영속화는 tool-* 규약)
      anns.push({name: "tool-avwap", x: tr.x[0], y: toY(tr.y[0]), yref: "y",
                 showarrow: false, yanchor: "top", text: "⚓",
                 font: {size: 12, color: "#e879f9"}});
    applyDraw(shapes, anns);
    addVwap(ms);
    setTool(null);
  }

  function makeVprof(sh, shapes, idx) {          // 📊 고정범위 볼륨 프로필 — 가격빈 히스토그램+POC
    const lo = Math.min(toMs(sh.x0), toMs(sh.x1)), hi = Math.max(toMs(sh.x0), toMs(sh.x1));
    shapes.splice(idx, 1);
    const rows = [];
    let pLo = Infinity, pHi = -Infinity;
    for (let i = lowerBound(lo); i < bounds.length && bounds[i][0] <= hi; i++) {
      const b = bounds[i];
      if (b.length < 6) { applyDraw(shapes, curAnns()); setTool(null); return; }
      rows.push(b);
      if (b[1] < pLo) pLo = b[1];
      if (b[2] > pHi) pHi = b[2];
    }
    if (rows.length < 3 || !(pHi > pLo)) { applyDraw(shapes, curAnns()); setTool(null); return; }
    const NB = 24, binH = (pHi - pLo) / NB, vols = new Array(NB).fill(0);
    for (const b of rows) {
      const v = b[3] || 0;
      const b0 = Math.max(0, Math.min(NB - 1, Math.floor((b[1] - pLo) / binH)));
      const b1 = Math.max(b0, Math.min(NB - 1, Math.floor((b[2] - pLo) / binH)));
      const per = v / (b1 - b0 + 1);             // 봉 거래량을 고저가 걸친 빈에 균등 분배
      for (let k = b0; k <= b1; k++) vols[k] += per;
    }
    const vmax = Math.max.apply(null, vols) || 1;
    let poc = 0;
    for (let k = 1; k < NB; k++) if (vols[k] > vols[poc]) poc = k;
    const spanMs = Math.max(hi - lo, 1);
    for (let k = 0; k < NB; k++) {               // 히스토그램 — 구간 오른쪽 벽에서 왼쪽으로
      if (!vols[k]) continue;
      const w = (vols[k] / vmax) * spanMs * 0.35;
      shapes.push({type: "rect", name: "tool-vprof", xref: "x",
                   x0: toISO(hi - w), x1: toISO(hi), yref: "y",
                   y0: toY(pLo + k * binH), y1: toY(pLo + (k + 1) * binH),
                   line: {width: 0}, fillcolor: k === poc ? "#f59e0b66" : "#2f81f733"});
    }
    const pocY = pLo + (poc + 0.5) * binH;
    shapes.push({type: "line", name: "tool-vprof", xref: "x", x0: toISO(lo), x1: toISO(hi),
                 yref: "y", y0: toY(pocY), y1: toY(pocY),
                 line: {color: "#f59e0b", width: 1.2, dash: "dot"}});
    shapes.push({type: "rect", name: "tool-vprof", xref: "x", x0: toISO(lo), x1: toISO(hi),
                 yref: "y", y0: toY(pLo), y1: toY(pHi),
                 line: {color: "#8b93a7", width: 0.8, dash: "dot"},
                 fillcolor: "rgba(0,0,0,0)"});
    const anns = curAnns();
    anns.push({name: "tool-vprof", x: toISO(hi), xanchor: "left", y: toY(pocY), yref: "y",
               showarrow: false, text: "POC " + fmtVal(toY(pocY)),
               font: {size: 9, color: "#f59e0b"}, bgcolor: "rgba(19,23,34,.75)"});
    applyDraw(shapes, anns);
    setTool(null);
  }

  const _same = (a, b) => a === b || (typeof a === "number" && typeof b === "number"
                                      && Math.abs(a - b) < 1e-9);

  function snapShape(sh, shapes) {               // 🧲 — 선/박스 끝점을 봉·OHLC 로 (멱등)
    // ⚠️ 좌표가 실제로 바뀔 때만 applyDraw — 이미 스냅된 도형의 자기 메아리는 변화 0 이라
    // 재적용하지 않아 무한 루프가 끊긴다(실측 확정 프리즈의 핵심 방어).
    if (sh.type === "line" && sh.xref !== "paper") {
      const p0 = snapPoint(toMs(sh.x0), sh.y0), p1 = snapPoint(toMs(sh.x1), sh.y1);
      const x0 = toISO(p0[0]), x1 = toISO(p1[0]);
      if (_same(sh.x0, x0) && _same(sh.y0, p0[1]) && _same(sh.x1, x1) && _same(sh.y1, p1[1]))
        return true;                             // 변화 없음 → 자기 메아리 → 무시
      sh.x0 = x0; sh.y0 = p0[1]; sh.x1 = x1; sh.y1 = p1[1];
    } else if (sh.type === "rect") {
      const p0 = snapPoint(toMs(sh.x0), sh.y0), p1 = snapPoint(toMs(sh.x1), sh.y1);
      const x0 = toISO(p0[0]), x1 = toISO(p1[0]);
      if (_same(sh.x0, x0) && _same(sh.y0, p0[1]) && _same(sh.x1, x1) && _same(sh.y1, p1[1]))
        return true;
      sh.x0 = x0; sh.y0 = p0[1]; sh.x1 = x1; sh.y1 = p1[1];
    } else if (sh.type === "line" && sh.xref === "paper" && (sh.name || "") === "tool-hline") {
      const y = snapPoint(bounds.length ? bounds[bounds.length - 1][0] : 0, sh.y0)[1];
      if (_same(sh.y0, y) && _same(sh.y1, y)) return true;   // 변화 없음 → 무시
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
    // 새 draw 이벤트인데 마지막 도형이 이미 우리가 만든 tool-* 도형이면 = applyDraw 자기 메아리
    // (진짜 새 draw 는 항상 무명 raw 도형을 append) → 재처리 금지(무한 루프 차단·drift 방지)
    if (isNew && String(sh.name || "").startsWith("tool-")) return true;
    if (sh.yref && sh.yref !== "y") return true; // 가격 패널 외(거래량·RSI) — 제외
    // 서버 도형(평단선·현재가선)은 사용자 편집/자석 대상 아님 — 스냅이 평단을 움직이면 안 됨
    if (idx < baseShapeCount && !String(sh.name || "").startsWith("tool-")) return true;
    if (isNew && tool === "hline" && sh.type === "line") { makeHline(sh, shapes, idx); return true; }
    if (isNew && tool === "vline" && sh.type === "line") { makeVline(sh, shapes, idx); return true; }
    if (isNew && tool === "cross" && sh.type === "line") { makeCross(sh, shapes, idx); return true; }
    if (isNew && tool === "ray" && sh.type === "line") { makeRay(sh, shapes, idx, false); return true; }
    if (isNew && tool === "ext" && sh.type === "line") { makeRay(sh, shapes, idx, true); return true; }
    if (isNew && tool === "text" && sh.type === "line") { makeText(sh, shapes, idx); return true; }
    if (isNew && tool === "fib" && sh.type === "line") { makeFib(sh, shapes, idx); return true; }
    if (isNew && tool === "meas" && sh.type === "rect") { makeMeasure(sh, shapes, idx); return true; }
    if (isNew && tool === "long" && sh.type === "rect") { makePosition(sh, shapes, idx, "long"); return true; }
    if (isNew && tool === "short" && sh.type === "rect") { makePosition(sh, shapes, idx, "short"); return true; }
    if (isNew && tool === "reg" && sh.type === "rect") { makeReg(sh, shapes, idx); return true; }
    if (isNew && tool === "vprof" && sh.type === "rect") { makeVprof(sh, shapes, idx); return true; }
    if (isNew && tool === "avwap" && sh.type === "line") { makeAvwap(sh, shapes, idx); return true; }
    // 자석 스냅 제외: 측정·피보 + 회귀/볼륨프로필(파생 도형 — 스냅이 ±2σ·빈 정렬을 깨뜨림)
    if (magnet && (sh.name || "") !== "tool-meas" && !String(sh.name || "").startsWith("tool-fib")
        && !String(sh.name || "").startsWith("tool-reg")
        && !String(sh.name || "").startsWith("tool-vprof"))
      return snapShape(sh, shapes) || true;
    return true;
  }

  document.getElementById("bt-mag").onclick = (ev) => {
    magnet = !magnet;
    ev.target.classList.toggle("on", magnet);
  };
  for (const [id, name] of TOOL_BTNS)
    document.getElementById(id).onclick = () => setTool(name);
  document.getElementById("bt-clear").onclick = () => {   // 서버 오버레이만 남기고 제거
    // 도형 = 보존 복사본으로 되돌림(직접 그린 것·도구 도형 모두 제거·인덱스 밀림 무관).
    // 주석 = 이름 필터 (tn-hi/tn-lo 콜아웃은 팬 중 갱신되므로 복사본 복원 금지)
    clearVwaps();                                // ⚓ 고정 VWAP 트레이스도 제거
    const anns = curAnns().filter((a) => !String(a.name || "").startsWith("tool-"));
    applyDraw(JSON.parse(JSON.stringify(baseShapes)), anns);
    try { if (storeKey) localStorage.removeItem("tndraw:" + storeKey); } catch (e) {}
    document.getElementById("detail").style.display = "none";
    if (replayCut !== null) replayApply();       // 리플레이 중이면 커튼은 유지 (일시 상태)
  };

  // ── ⏪ 바 리플레이 — 과거로 되감아 한 봉씩 재생 (매매 연습 · 미래 봉 커튼) ──
  // 커튼 = layer:"above" rect 가 컷 이후를 가림. y맞춤·콜아웃·데이터창은 replayCut 으로
  // 클램프해 미래 정보(고저·값)가 새지 않게 한다. 커튼은 일시 상태 — 영속화 제외.
  let replayIdx = 0, replayTimer = null;
  const rpBar = document.getElementById("replaybar");
  const rpSlider = document.getElementById("rp-slider");
  const rpPlay = document.getElementById("rp-play");

  function curtainX0(i) {                        // 봉 i 와 다음 봉 사이 중간
    const t = bounds[i][0];
    const nxt = i + 1 < bounds.length ? bounds[i + 1][0]
                                      : t + (t - bounds[Math.max(0, i - 1)][0] || 864e5);
    return (t + nxt) / 2;
  }

  function replayApply() {                       // 커튼 갱신 + 뷰 우측을 컷에 정렬 + y 재맞춤
    if (replayCut === null || !bounds.length) return;
    const shapes = (gd.layout.shapes || []).filter((s) => (s.name || "") !== "replay-curtain");
    const farRight = bounds[bounds.length - 1][0] + 400 * 864e5;
    shapes.push({type: "rect", name: "replay-curtain", xref: "x", yref: "paper",
                 x0: toISO(replayCut), x1: toISO(farRight), y0: 0, y1: 1,
                 fillcolor: "#131722", opacity: 0.96, line: {width: 0}, layer: "above"});
    drawGuard++;
    Plotly.relayout(gd, {shapes: shapes}).then(() => { undraw(); });
    const xr = gd.layout.xaxis.range;
    if (xr) {
      const w = Math.max(864e5, Date.parse(xr[1]) - Date.parse(xr[0]));
      const x1 = replayCut + w * 0.10, x0 = x1 - w;
      guard++;
      Plotly.relayout(gd, {"xaxis.range": [toISO(x0), toISO(x1)]}).then(() => { unguard(); });
      setTarget(x0, x1);
    }
    ohlcReadout(bounds[replayIdx][0]);
    rpSlider.value = String(replayIdx);
  }

  function replayStop() {
    if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
    rpPlay.textContent = "▶";
    replayCut = null;
    rpBar.style.display = "none";
    document.getElementById("bt-replay").classList.toggle("on", false);
    const shapes = (gd.layout.shapes || []).filter((s) => (s.name || "") !== "replay-curtain");
    drawGuard++;
    Plotly.relayout(gd, {shapes: shapes}).then(() => { undraw(); });
    finishGesture();                             // 콜아웃·y 를 실제 최신 구간으로 복원
  }

  function replayStep(n) {
    if (replayCut === null) return;
    replayIdx = Math.max(1, Math.min(bounds.length - 1, replayIdx + n));
    replayCut = curtainX0(replayIdx);
    replayApply();
    if (replayIdx >= bounds.length - 1 && replayTimer) {   // 끝 도달 — 자동 정지
      clearInterval(replayTimer); replayTimer = null; rpPlay.textContent = "▶";
    }
  }

  document.getElementById("bt-replay").onclick = () => {
    if (replayCut !== null) { replayStop(); return; }
    if (!bounds.length) return;
    replayIdx = Math.max(1, Math.floor(bounds.length * 0.5));
    rpSlider.min = "1";
    rpSlider.max = String(bounds.length - 1);
    replayCut = curtainX0(replayIdx);
    rpBar.style.display = "flex";
    document.getElementById("bt-replay").classList.toggle("on", true);
    replayApply();
  };
  rpPlay.onclick = () => {
    if (replayCut === null) return;
    if (replayTimer) { clearInterval(replayTimer); replayTimer = null; rpPlay.textContent = "▶"; return; }
    const spd = parseFloat(document.getElementById("rp-speed").value || "1") || 1;
    replayTimer = setInterval(() => replayStep(1), Math.max(50, 600 / spd));
    rpPlay.textContent = "⏸";
  };
  document.getElementById("rp-step").onclick = () => replayStep(1);
  document.getElementById("rp-back").onclick = () => replayStep(-10);
  document.getElementById("rp-exit").onclick = () => replayStop();
  rpSlider.oninput = (e) => {
    if (replayCut === null) return;
    const v = parseInt(((e && e.target) || rpSlider).value, 10);
    if (!isNaN(v)) {
      replayIdx = Math.max(1, Math.min(bounds.length - 1, v));
      replayCut = curtainX0(replayIdx);
      replayApply();
    }
  };

  // ── ⚡ live 실시간 패치 — 피더 iframe 의 localStorage push 를 받아 **마지막 봉만**
  // in-place 갱신. live 모드의 메인 html 은 바이트 안정(서버 bake 없음)이라 8초
  // fragment 재실행이 iframe 을 재마운트하지 않는다 = 그리던 드로잉·뷰·상태 유지 ──
  function arr(a) {                                // plotly 6 typed-array 스펙 디코드
    // fig.to_json() 은 숫자 배열을 {dtype, bdata(base64)} 로 직렬화 — Array.from 은
    // 그 객체에서 빈 배열이 되므로 dtype 별 TypedArray 로 풀어 평범한 배열로 반환.
    if (Array.isArray(a)) return a.slice();
    if (a && a.bdata) {
      try {
        const bin = atob(a.bdata);
        const buf = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
        const T = {f8: Float64Array, f4: Float32Array, i4: Int32Array, u4: Uint32Array,
                   i2: Int16Array, u2: Uint16Array, i1: Int8Array, u1: Uint8Array}[a.dtype];
        if (T) return Array.from(new T(buf.buffer));
      } catch (e) {}
      return [];
    }
    return (a && a.length) ? Array.from(a) : [];
  }

  function patchLast(p) {
    if (!gd.layout || !gd.data) return;            // newPlot 완료 전 폴링 틱 무시
    if (!bounds.length || pctMode) return;
    const b = bounds[bounds.length - 1];           // bounds 갱신 — yFit·스냅·리드아웃 공유
    b[5] = p;
    if (p > b[2]) b[2] = p;
    if (p < b[1]) b[1] = p;
    const tr = (gd.data || [])[0];                 // 메인 가격 트레이스 = 항상 첫번째
    if (tr && tr.type === "candlestick") {
      const c = arr(tr.close), h = arr(tr.high), l = arr(tr.low);
      if (c.length) {
        c[c.length - 1] = p;
        h[h.length - 1] = Math.max(h[h.length - 1], p);
        l[l.length - 1] = Math.min(l[l.length - 1], p);
        Plotly.restyle(gd, {close: [c], high: [h], low: [l]}, [0]);
      }
    } else if (tr) {
      const y = arr(tr.y);
      if (y.length) {
        y[y.length - 1] = p;
        Plotly.restyle(gd, {y: [y]}, [0]);
      }
    }
    const upd = {};                                // 현재가 점선·우측 라벨 (tn-last)
    (gd.layout.shapes || []).forEach((s, i) => {
      if ((s.name || "") === "tn-last") {
        upd["shapes[" + i + "].y0"] = toY(p);
        upd["shapes[" + i + "].y1"] = toY(p);
      }
    });
    (gd.layout.annotations || []).forEach((a, i) => {
      if ((a.name || "") === "tn-last") {
        upd["annotations[" + i + "].y"] = toY(p);
        upd["annotations[" + i + "].text"] = "<b>" + fmtVal(toY(p)) + "</b>";
      }
    });
    if (Object.keys(upd).length) {
      guard++;                                     // 카운터 규약 (boolean 대입 금지)
      Plotly.relayout(gd, upd).then(() => { unguard(); });
    }
    const xr = gd.layout.xaxis && gd.layout.xaxis.range;
    if (xr) {                                      // 마지막 봉이 보이면 y 부드럽게 재맞춤
      const x1 = Date.parse(xr[1]) || +xr[1];
      if (x1 >= b[0]) setTarget(Date.parse(xr[0]) || +xr[0], x1);
    }
    ohlcReadout(b[0]);
  }
  if (live && storeKey) {
    const rtKey = "tnrt:" + String(storeKey).split(":")[0];   // 키 = 티커 (봉·스케일 공용)
    let lastP = null;
    const applyRt = () => {
      let d = null;
      try { d = JSON.parse(localStorage.getItem(rtKey) || "null"); } catch (e) {}
      if (!d || !(d.p > 0) || Date.now() - (d.w || 0) > 30000) return;   // 신선한 값만
      if (d.p === lastP) return;
      lastP = d.p;
      patchLast(d.p);
    };
    try {                                          // 피더(형제 iframe) 기록 = storage 이벤트
      window.addEventListener("storage", (ev) => {
        if (!ev || ev.key == null || ev.key === rtKey) applyRt();
      });
    } catch (e) {}
    setInterval(applyRt, 2000);                    // storage 이벤트 유실 폴백
  }

  Plotly.newPlot(gd, fig.data, fig.layout, @@CONFIG@@).then(() => {
    gd.style.position = "relative";                    // 크로스헤어 오버레이 부착 (newPlot 후)
    gd.appendChild(xhV); gd.appendChild(xhH); gd.appendChild(xhY);
    // 저장된 드로잉 복원 — 서버 도형 뒤에 append (지우기·보호 가드와 정합).
    // 하단 지표 구성이 바뀌어 사라진 서브패널 축(y3 등)을 참조하는 도형은 제외(고아 방지)
    const saved = loadDrawings();
    if (saved && (saved.shapes.length || saved.anns.length)) {
      const axes = new Set(Object.keys(fig.layout).filter((k) => k.startsWith("yaxis"))
        .map((k) => "y" + k.slice(5)));
      const okRef = (r) => !r || r === "paper" || axes.has(r);
      drawGuard++;                               // 복원도 도형 메아리 — drawGuard 로
      Plotly.relayout(gd, {
        shapes: (gd.layout.shapes || []).concat(saved.shapes.filter((s) => okRef(s.yref))),
        annotations: (gd.layout.annotations || []).concat(
          saved.anns.filter((a) => okRef(a.yref))),
      }).then(() => { undraw(); });
    }
    for (const ms of ((saved && saved.vwaps) || [])) addVwap(+ms);   // ⚓ 앵커 재계산 복원
    const last = bounds.length ? bounds[bounds.length - 1][0] : null;
    const freshView = loadFreshView();           // ⚡자동갱신·설정변경 직후 = 보던 위치 복원
    if (freshView) {
      guard++;                              // 저장된 원문 그대로 — 재직렬화 왕복 금지
      Plotly.relayout(gd, {"xaxis.range": [freshView[0], freshView[1]]})
        .then(() => { unguard(); rescale(); });
    } else if (last && @@VIEW_MS@@) {            // 초기 표시창 (기간 라디오)
      const x0 = last - @@VIEW_MS@@;
      const first = bounds[0][0];
      if (x0 > first) {
        guard++;
        Plotly.relayout(gd, {"xaxis.range": [new Date(x0).toISOString(),
                                             new Date(last + @@VIEW_MS@@ * 0.02).toISOString()]})
          .then(() => { unguard(); rescale(); });
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
      // 도형 이벤트는 drawGuard, 그 외(팬/줌/애니)는 guard — 가드 분리로
      // (1) 자기 도형 메아리 1차 차단(내용 기반 방어는 2차), (2) 팬 애니메이션
      // 중 사용자 도형 이벤트가 guard 에 삼켜져 자석이 간헐 미적용되던 것 해결.
      const hasShapes = e && (Array.isArray(e.shapes)
          || Object.keys(e).some((k) => k.startsWith("shapes[")));
      if (hasShapes) {
        if (drawGuard > 0) return;               // applyDraw/복원 자기 메아리
        dragging = false;
        if (handleShapes(e)) scheduleSave();     // 드로잉 도구·🧲 자석 경로 + 영속화
        return;
      }
      if (guard) return;
      dragging = false;
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
                        store_key: str | None = None,
                        dock: bool = False,
                        live: bool = False) -> str:
    """fig(charts.price_chart 산출) → 자동 y 리스케일·드로잉 도구·인차트 마커 상세 임베드 HTML.

    bounds_json — y 맞춤 프레임 오버라이드 (비교 모드: compare_bounds_json 의 % 프레임).
    pct_mode — 비교(%) 모드: 도구 라벨을 가격 대신 % 로 포맷.
    y_log — 로그 스케일: 도형/축 y 좌표가 log10 공간 (스냅·측정이 실가격으로 환산).
    store_key — 드로잉 영속화 localStorage 키(예: "NVDA:1d:lin"). None=비영속.
                스케일(lin/log/pct)을 키에 포함해야 좌표계 혼선이 없다(호출부 책임).
    dock — True 면 도구바를 좌측 세로 독으로 (풀뷰 — TradingView 배치).
    live — ⚡자동갱신: realtime_feed_html 피더의 localStorage push(tnrt:티커)를 받아
           마지막 봉·현재가선을 in-place 패치. 호출부는 live 시 서버측 실시간 bake 를
           생략해 html 을 바이트 안정으로 유지해야 함(재마운트=드로잉 리셋 방지).
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
            .replace("@@LIVE@@", json.dumps(bool(live)))
            .replace("@@LAST_CLOSE@@", json.dumps(last_close))
            .replace("@@STORE_KEY@@", json.dumps(store_key))
            .replace("@@DOCK@@", json.dumps(bool(dock)))
            .replace("@@CONFIG@@", config)
            .replace("@@BOUNDS@@", bounds)
            .replace("@@FIG@@", fig.to_json()))       # fig JSON 은 마지막 (토큰 오염 차단)
    return html


def realtime_feed_html(store_key: str, price, seq=None) -> str:
    """⚡ live 피더 — 초소형 컴포넌트가 실시간가를 localStorage 로 차트 iframe 에 push (순수).

    live 모드의 메인 차트 html 은 바이트 안정이어야 한다(변경=iframe 재마운트=그리던
    드로잉 리셋+수 MB 재전송) — 가격은 이 <1KB 피더만 나른다. seq(기본 서버시각)가
    매 재실행 html 을 바꿔 피더만 재마운트→재기록: 가격이 같아도 신선도(w)가 갱신돼
    메인 차트의 30s stale 가드를 통과한다. storage 이벤트는 same-origin 형제 iframe
    에 전파(2s 폴링 폴백 병행). 키 = "tnrt:" + 티커(store_key 의 첫 세그먼트).
    """
    key = "tnrt:" + str(store_key).split(":")[0]
    try:
        p = float(price) if price and float(price) > 0 else None
    except Exception:
        p = None
    if seq is None:
        import time
        seq = int(time.time() * 1000)
    return ("<script>/*" + str(seq) + "*/(function(){try{localStorage.setItem("
            + json.dumps(key) + ",JSON.stringify({p:" + json.dumps(p)
            + ",w:Date.now()}))}catch(e){}})();</script>")
