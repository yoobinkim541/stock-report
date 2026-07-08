"""dashboard/plotly_embed.py — 커스텀 plotly 임베드 (팬 시 부드러운 y축 자동 리스케일).

st.plotly_chart 는 팬/줌(relayout)을 서버에 안 알려줘 y 자동맞춤이 불가 — 이 모듈은
차트를 st.components.v1.html 로 임베드하고 브라우저 JS 가 `plotly_relayout` 을 받아
보이는 x구간의 고저(High/Low)로 y축을 즉시(디바운스 50ms·재진입 가드) 재설정한다
(TradingView 방식). 마커 클릭 상세는 iframe 이 서버 콜백 불가라 인차트 박스로 렌더.

plotly.js 는 CDN(파이썬 plotly 번들과 동일 버전 핀) — 실패 시 안내 문구,
호출부(ticker 페이지)가 구형 렌더러(st.plotly_chart) 폴백 토글 제공.
순수 함수(HTML 문자열 반환) — 단위테스트 가능.
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
    """[[epoch_ms, low, high, volume], ...] — y 자동맞춤용 경량 배열 (JS 소비)."""
    if hist is None or getattr(hist, "empty", True):
        return "[]"
    cols = set(hist.columns)
    lo = hist["Low"] if "Low" in cols else hist["Close"]
    hi = hist["High"] if "High" in cols else hist["Close"]
    vol = hist["Volume"] if "Volume" in cols else None
    out = []
    for i, (ts, l, h) in enumerate(zip(hist.index, lo.values, hi.values)):
        if l == l and h == h:                                   # NaN 스킵
            v = float(vol.iloc[i]) if vol is not None and vol.iloc[i] == vol.iloc[i] else 0.0
            out.append([int(ts.timestamp() * 1000), float(l), float(h), v])
    return json.dumps(out)


def compare_bounds_json(main_hist, compare: dict, view_days=None) -> str:
    """비교 모드 y 자동맞춤 프레임 — 전 시리즈를 %로 정규화해 병합 (JS yFit 소비).

    행 = [epoch_ms, pct, pct, volume]. 메인 행만 거래량 탑재(거래량 패널=메인 기준).
    yFit 은 구간 내 행들의 min/max 스캔이라 시리즈별 행이 섞여 있어도 동작.
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


def pannable_chart_html(fig, hist, *, height: int = 460, view_days=None,
                        vol_axis: str | None = None,
                        bounds_json: str | None = None) -> str:
    """fig(charts.price_chart 산출) → 자동 y 리스케일·드로잉·인차트 마커 상세 임베드 HTML.

    bounds_json — y 맞춤 프레임 오버라이드 (비교 모드: compare_bounds_json 의 % 프레임).
    """
    fig_json = fig.to_json()
    bounds = bounds_json if bounds_json is not None else price_bounds_json(hist)
    config = json.dumps({
        "scrollZoom": True, "displaylogo": False,   # 모드바 = hover 시만 (기본) — 상시 노출 제거
        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
        "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "eraseshape"],
    })
    view_ms = int(view_days) * 86400000 if view_days else 0
    vol_axis_js = json.dumps(vol_axis)
    try:
        last_close = float(hist["Close"].iloc[-1])
    except Exception:
        last_close = 0.0
    return f"""
<div id="chart" style="width:100%;min-height:{height}px"></div>
<div id="detail" style="display:none;margin-top:6px;padding:8px 12px;border:1px solid #1e222d;
  border-radius:8px;background:#131722;color:#d1d4dc;
  font:12px 'JetBrains Mono', ui-monospace, monospace"></div>
<script src="{_plotlyjs_cdn()}"></script>
<script>
(function() {{
  if (typeof Plotly === "undefined") {{
    document.getElementById("chart").innerHTML =
      "<div style='color:#9198a6;padding:20px'>plotly.js CDN 로드 실패 — 📐 팝오버의 '구형 렌더러'를 켜세요</div>";
    return;
  }}
  const fig = {fig_json};
  const bounds = {bounds};                       // [[ms, low, high], ...] 시간 오름차순
  const gd = document.getElementById("chart");
  fig.layout.height = {height};
  // y축은 전부 프로그램 제어(자동 맞춤) — 사용자 팬이 y를 끌지 않게 고정해
  // '드래그 중 y 싸움 → 종료 시 스냅' 흔들림의 근원을 제거 (TradingView 방식)
  for (const k of Object.keys(fig.layout)) {{
    if (k.startsWith("yaxis")) fig.layout[k].fixedrange = true;
  }}
  const hoverMode = fig.layout.hovermode || "x unified";
  let guard = false, dragging = false, hoverOff = false;

  const volAxis = {vol_axis_js};                 // 거래량 패널 축 id (없으면 null)

  function lowerBound(t0) {{                     // 시간 정렬 bounds 이진 탐색
    let lo = 0, hi = bounds.length;
    while (lo < hi) {{ const m = (lo + hi) >> 1; if (bounds[m][0] < t0) lo = m + 1; else hi = m; }}
    return lo;
  }}

  function yFit(x0, x1) {{                       // 보이는 구간 고저·거래량 최대 + 패딩
    if (!bounds.length) return null;
    let lo = Infinity, hi = -Infinity, vmax = 0;
    for (let i = lowerBound(x0); i < bounds.length && bounds[i][0] <= x1; i++) {{
      const b = bounds[i];
      if (b[1] < lo) lo = b[1]; if (b[2] > hi) hi = b[2]; if (b[3] > vmax) vmax = b[3];
    }}
    if (!isFinite(lo)) return null;
    const pad = Math.max((hi - lo) * 0.06, Math.abs(hi) * 0.002);
    return {{price: [lo - pad, hi + pad], vol: [0, vmax * 1.1 || 1]}};
  }}

  // ── 부드러운 y 전환 — 시간 기반 lerp + **비용 적응형 스로틀** ──
  // 일목 구름·채널처럼 fill 폴리곤이 많으면 relayout 1회가 비싸다 → 실제 리드로우
  // 비용(EMA)을 재서 갱신 간격을 자동으로 벌리고(무거우면 ~10fps·가벼우면 60fps),
  // 목표와 차이가 시각적으로 무의미하면(데드밴드) relayout 자체를 생략한다.
  let target = null, curY = null, raf = null, busy = false;
  let costMs = 8, lastApply = 0, lastStep = 0;

  function animStep() {{
    raf = null;
    if (!target) return;
    const now = performance.now();
    const dt = Math.min(100, now - (lastStep || now));
    lastStep = now;
    if (!curY) curY = {{price: target.price.slice(), vol: target.vol.slice()}};
    const a = 1 - Math.exp(-dt / 90);            // 프레임률 무관 ~90ms 수렴 ease-out
    curY.price[0] += (target.price[0] - curY.price[0]) * a;
    curY.price[1] += (target.price[1] - curY.price[1]) * a;
    curY.vol[1]   += (target.vol[1] - curY.vol[1]) * a;
    const span = Math.abs(target.price[1] - target.price[0]) || 1;
    const dist = Math.abs(curY.price[0] - target.price[0])
               + Math.abs(curY.price[1] - target.price[1]);
    const done = dist < span * 0.002;
    if (done) curY = {{price: target.price.slice(), vol: target.vol.slice()}};
    const minGap = Math.max(16, costMs * 1.2);   // 리드로우 비용만큼 간격 자동 확대
    const applied = gd.layout.yaxis.range || [];
    const visDelta = Math.abs((applied[0] ?? 1e18) - curY.price[0])
                   + Math.abs((applied[1] ?? 1e18) - curY.price[1]);
    if (!busy && now - lastApply >= minGap && visDelta > span * 0.004) {{
      busy = true; guard = true; lastApply = now;
      const t0 = performance.now();
      const upd = {{"yaxis.range": curY.price.slice()}};
      if (volAxis && !dragging) upd[volAxis + ".range"] = [0, curY.vol[1]];
      Plotly.relayout(gd, upd).then(() => {{
        costMs = costMs * 0.7 + (performance.now() - t0) * 0.3;   // 비용 EMA
        busy = false; guard = false;
      }});
    }}
    if (!done || dragging) raf = requestAnimationFrame(animStep);
  }}

  function setTarget(x0, x1) {{
    const r = yFit(x0, x1);
    if (!r) return;
    target = r;
    if (!raf) raf = requestAnimationFrame(animStep);
  }}

  function evXRange(e) {{                        // relayout(ing) 페이로드 → [ms, ms]
    if (!e) return null;
    let r0 = e["xaxis.range[0]"], r1 = e["xaxis.range[1]"];
    if (e["xaxis.range"]) {{ r0 = e["xaxis.range"][0]; r1 = e["xaxis.range"][1]; }}
    if (r0 == null || r1 == null) return null;
    return [Date.parse(r0) || +r0, Date.parse(r1) || +r1];
  }}

  const lastClose = {last_close};

  function callouts(x0, x1, upd) {{             // 보이는 구간 최고/최저 콜아웃 팬 추종
    let hi = -Infinity, hiT = 0, lo = Infinity, loT = 0;
    for (const [t, l, h] of bounds) {{
      if (t >= x0 && t <= x1) {{
        if (h > hi) {{ hi = h; hiT = t; }}
        if (l < lo) {{ lo = l; loT = t; }}
      }}
    }}
    if (!isFinite(lo) || !lastClose) return;
    const anns = gd.layout.annotations || [];
    const fmt = (v) => Math.round(v).toLocaleString();
    const pct = (v) => ((lastClose / v - 1) * 100).toFixed(1);
    for (let i = 0; i < anns.length; i++) {{
      if (anns[i].name === "tn-hi") {{
        upd[`annotations[${{i}}].x`] = new Date(hiT).toISOString();
        upd[`annotations[${{i}}].y`] = hi;
        upd[`annotations[${{i}}].text`] = `${{fmt(hi)}} (${{pct(hi) > 0 ? "+" : ""}}${{pct(hi)}}%)`;
      }}
      if (anns[i].name === "tn-lo") {{
        upd[`annotations[${{i}}].x`] = new Date(loT).toISOString();
        upd[`annotations[${{i}}].y`] = lo;
        upd[`annotations[${{i}}].text`] = `${{fmt(lo)}} (+${{pct(lo)}}%)`.replace("(+-", "(-");
      }}
    }}
  }}

  function rescale() {{                          // 드래그 종료/휠/슬라이더 — 콜아웃 포함 최종 맞춤
    const xr = gd.layout.xaxis.range;
    if (!xr) return;
    const x0 = Date.parse(xr[0]), x1 = Date.parse(xr[1]);
    setTarget(x0, x1);                           // y 는 lerp 루프가 수렴
    const upd = {{}};
    callouts(x0, x1, upd);                       // 무거운 주석 갱신은 종료 시 1회만
    if (hoverOff) {{ upd["hovermode"] = hoverMode; hoverOff = false; }}
    if (Object.keys(upd).length) {{
      guard = true;
      Plotly.relayout(gd, upd).then(() => {{ guard = false; }});
    }}
  }}

  Plotly.newPlot(gd, fig.data, fig.layout, {config}).then(() => {{
    const last = bounds.length ? bounds[bounds.length - 1][0] : null;
    if (last && {view_ms}) {{                    // 초기 표시창 (기간 라디오)
      const x0 = last - {view_ms};
      const first = bounds[0][0];
      if (x0 > first) {{
        guard = true;
        Plotly.relayout(gd, {{"xaxis.range": [new Date(x0).toISOString(),
                                              new Date(last + {view_ms} * 0.02).toISOString()]}})
          .then(() => {{ guard = false; rescale(); }});
      }}
    }}
    gd.on("plotly_relayouting", (e) => {{        // 드래그 **중** — y 가 실시간 따라옴 (스냅 제거)
      if (guard) return;
      const xr = evXRange(e);
      if (!xr) return;
      if (!dragging) {{                          // 드래그 시작: hover 연산 중지 (비용 절감)
        dragging = true;
        if (!hoverOff) {{ hoverOff = true; guard = true;
          Plotly.relayout(gd, {{hovermode: false}}).then(() => {{ guard = false; }}); }}
      }}
      setTarget(xr[0], xr[1]);
    }});
    gd.on("plotly_relayout", (e) => {{           // 팬 종료/휠 줌/슬라이더 → 최종 맞춤+콜아웃
      if (guard) return;
      dragging = false;
      const keys = Object.keys(e || {{}});
      if (keys.some(k => k.startsWith("xaxis.range")) || e["xaxis.autorange"]) {{
        requestAnimationFrame(rescale);
      }}
    }});
    gd.on("plotly_click", (ev) => {{             // ▲▼ 마커 클릭 → 인차트 상세
      const p = (ev.points || [])[0];
      if (!p || !p.customdata) return;
      const c = p.customdata;                    // [event_id, 구분, qty, px, avg, account, source, ts, note, cur]
      const el = document.getElementById("detail");
      el.style.display = "block";
      el.innerHTML = `<b>${{c[1]}}</b> ${{c[7] || ""}} &nbsp;·&nbsp; 수량 ${{c[2]}}주 ` +
        `&nbsp;·&nbsp; 체결 ${{c[9] || ""}} ${{Number(c[3]).toLocaleString()}} ` +
        `&nbsp;·&nbsp; 평단 ${{c[4] != null ? Number(c[4]).toLocaleString() : "—"}} ` +
        `<span style="color:#9198a6">${{c[5] || ""}} · ${{c[6] || ""}} ${{c[8] ? "· " + c[8] : ""}}</span>`;
    }});
  }});
}})();
</script>
"""
