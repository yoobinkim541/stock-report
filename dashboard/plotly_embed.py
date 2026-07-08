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
    """[[epoch_ms, low, high], ...] — y 자동맞춤용 경량 배열 (JS 이진탐색 소비)."""
    if hist is None or getattr(hist, "empty", True):
        return "[]"
    cols = set(hist.columns)
    lo = hist["Low"] if "Low" in cols else hist["Close"]
    hi = hist["High"] if "High" in cols else hist["Close"]
    out = []
    for ts, l, h in zip(hist.index, lo.values, hi.values):
        if l == l and h == h:                                   # NaN 스킵
            out.append([int(ts.timestamp() * 1000), float(l), float(h)])
    return json.dumps(out)


def pannable_chart_html(fig, hist, *, height: int = 460, view_days=None,
                        rsi_pane: bool = False) -> str:
    """fig(charts.price_chart 산출) → 자동 y 리스케일·드로잉·인차트 마커 상세 임베드 HTML."""
    fig_json = fig.to_json()
    bounds = price_bounds_json(hist)
    config = json.dumps({
        "scrollZoom": True, "displaylogo": False, "displayModeBar": True,
        "modeBarButtonsToRemove": ["select2d", "lasso2d"],
        "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "eraseshape"],
    })
    view_ms = int(view_days) * 86400000 if view_days else 0
    return f"""
<div id="chart" style="width:100%"></div>
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
  let guard = false, timer = null;

  function yFit(x0, x1) {{                       // 보이는 구간 고저 + 6% 패딩
    if (!bounds.length) return null;
    let lo = Infinity, hi = -Infinity;
    for (const [t, l, h] of bounds) {{
      if (t >= x0 && t <= x1) {{ if (l < lo) lo = l; if (h > hi) hi = h; }}
    }}
    if (!isFinite(lo)) return null;
    const pad = Math.max((hi - lo) * 0.06, hi * 0.002);
    return [lo - pad, hi + pad];
  }}

  function rescale() {{
    const xr = gd.layout.xaxis.range;
    if (!xr) return;
    const r = yFit(Date.parse(xr[0]), Date.parse(xr[1]));
    if (!r) return;
    guard = true;
    Plotly.relayout(gd, {{"yaxis.range": r}}).then(() => {{ guard = false; }});
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
    gd.on("plotly_relayout", (e) => {{           // 팬/줌/슬라이더 → 부드러운 y 자동맞춤
      if (guard) return;
      const keys = Object.keys(e || {{}});
      if (keys.some(k => k.startsWith("xaxis.range")) || e["xaxis.autorange"]) {{
        clearTimeout(timer);
        timer = setTimeout(rescale, 50);
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
