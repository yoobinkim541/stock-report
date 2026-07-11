"""dashboard/charts.py — plotly 차트 빌더 (순수 함수·단위테스트 가능).

데이터(dict/list) → plotly Figure. streamlit 의존 없음(st.plotly_chart 는 호출부에서).
색: 손익/방향은 초록(+)·빨강(-). 비중/크기는 단색.
"""
from __future__ import annotations

from dashboard import theme

# 팔레트 단일 진실원 = theme (TradingView Terminal Noir). 테스트가 _GREEN/_RED 참조.
_GREEN = theme.GREEN
_RED = theme.RED
_BLUE = theme.BLUE
_GRID = theme.GRID


def _go():
    import plotly.graph_objects as go
    return go


def _t(fig):
    """공통 다크 테마 적용 후 반환."""
    return theme.apply_plotly_theme(fig)


# 가격 차트 인터랙션 config — 드래그=이동(pan)·휠=확대/축소·더블클릭=원위치.
# select/lasso 는 마커 클릭(on_select) 과 간섭하므로 제거. 페이지들이 공용(단일 진실원).
PAN_CFG = {"scrollZoom": True, "displayModeBar": True, "displaylogo": False,
           "modeBarButtonsToRemove": ["select2d", "lasso2d"]}
PAN_HINT = "🖱️ 드래그=이동 · 휠=확대/축소 · 더블클릭=원위치 · 하단 미니차트 드래그=과거 구간"
# 수동 드로잉 도구 포함 config — 선/자유곡선/박스/지우개. 드로잉은 클라이언트 상태
# (Streamlit rerun 시 소실 — streamlit 이 shape 이벤트를 서버에 미노출·정직 한계)
PAN_DRAW_CFG = {**PAN_CFG,
                "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "eraseshape"]}


def _pannable(fig, *, rangeslider: bool = True, height: int = 360):
    """가격 차트 공통 내비게이션 — pan 드래그 + 하단 레인지슬라이더(과거 탐색)."""
    fig.update_layout(dragmode="pan", height=height,
                      xaxis=dict(rangeslider=dict(visible=rangeslider, thickness=0.08)))
    return fig


def _logr(lo, hi):
    """선형 y범위 → 로그축(plotly type='log' 는 range 를 log10 으로 해석)."""
    import math
    lo = max(lo, hi * 1e-4 if hi > 0 else 1e-6)      # 0·음수 방어
    return [math.log10(lo), math.log10(hi)] if hi > 0 else [lo, hi]


def _log_fixup_price_shapes(fig) -> None:
    """로그축 보정 — 가격 패널 도형·주석의 y 를 log10 으로 변환 (in-place).

    plotly 규약: 로그축의 shape/annotation y 는 log10(값)로 줘야 정위치(주석 docstring
    "you must take the log of your desired range"). 트레이스는 raw 값이라 무관 —
    이 함수는 avg_cost·최고저 콜아웃·현재가선·추세선 라벨 등 어느 헬퍼가 넣었든
    가격축 도형/주석만 골라 일괄 변환한다. 서브패널(y2·y3…)·paper 참조는 건드리지 않음.

    **yref 판별 함정**: `add_annotation(row/col 없이)` 는 yref 가 None 으로 남고
    plotly.js 가 렌더 시 첫 y축(=가격 패널)으로 coerce 한다 — 추세선/채널 라벨(전 구성)과
    panes==1 의 tn-hi/lo·현재가 라벨이 이 경로. 따라서 None 도 가격축으로 취급해 변환한다
    (서브패널 주석은 항상 명시적 y2·y3 를 가져 오변환 없음 — 적대 리뷰로 확정된 버그 픽스).
    """
    import math

    def _lg(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return v
        return math.log10(v) if v > 0 else v

    for sh in (fig.layout.shapes or []):
        if getattr(sh, "yref", None) in (None, "y"):
            if sh.y0 is not None:
                sh.y0 = _lg(sh.y0)
            if sh.y1 is not None:
                sh.y1 = _lg(sh.y1)
    for an in (fig.layout.annotations or []):
        if getattr(an, "yref", None) in (None, "y") and an.y is not None:
            an.y = _lg(an.y)


def _initial_view(fig, hist, view_days, *, lo_col="Low", hi_col="High", y_override=None,
                  log_scale: bool = False):
    """전체 히스토리 로드 상태에서 초기 화면만 최근 view_days 로 — 과거는 드래그/미니차트 탐색.

    x·y 초기 범위를 창에 맞춤(plotly 는 x창 추종 y 자동스케일이 없어 창 기준으로 시작 —
    팬/줌으로 조절·더블클릭=전체 복귀). 데이터가 창보다 짧으면 전체 표시.
    y_override=(lo, hi) — 비교(%) 모드처럼 트레이스 단위가 가격이 아닐 때 y 만 주입.
    log_scale — 로그축이면 계산한 y범위를 log10 으로 변환(y_override=% 모드는 로그 없음).
    """
    if not view_days or hist is None or getattr(hist, "empty", True):
        return fig
    import pandas as pd
    end = hist.index[-1]
    start = end - pd.Timedelta(days=view_days)
    if start <= hist.index[0]:
        if y_override:                               # 짧은 이력도 % y 프레임은 반영
            fig.update_layout(yaxis_range=list(y_override))
        return fig                                   # 창보다 짧은 이력 — 전체 그대로
    win = hist[hist.index >= start]
    if len(win) < 2:
        return fig
    if y_override:
        lo, hi = y_override
        fig.update_layout(xaxis_range=[start, end + (end - start) * 0.02],
                          yaxis_range=[lo, hi])
        return fig
    cols = set(getattr(hist, "columns", []))
    lo = float(win[lo_col].min()) if lo_col in cols else float(win["Close"].min())
    hi = float(win[hi_col].max()) if hi_col in cols else float(win["Close"].max())
    pad = max((hi - lo) * 0.06, hi * 0.002)
    yr = _logr(lo - pad, hi + pad) if log_scale else [lo - pad, hi + pad]
    fig.update_layout(xaxis_range=[start, end + (end - start) * 0.02], yaxis_range=yr)
    return fig


def cmp_initial_yrange(close, compare, view_days):
    """비교(%) 모드 초기 y 범위 — 전 시리즈 normalize_pct 후 표시창 min/max (순수).

    반환 (lo, hi) 또는 None(재료 부족 — 오토레인지 위임). 달러 가격으로 y 를 잡던
    _initial_view 가 % 축에 가격대(예: 45~55)를 넣어 선이 화면 밖으로 나가던 버그의 해법.
    """
    import pandas as pd
    lo = hi = None
    for s in [close] + list((compare or {}).values()):
        if s is None or len(getattr(s, "dropna", lambda: [])()) < 2:
            continue
        ns = normalize_pct(s, view_days)
        win = ns
        if view_days:
            start = ns.index[-1] - pd.Timedelta(days=int(view_days))
            w = ns[ns.index >= start]
            if len(w) >= 2:
                win = w
        wlo, whi = float(win.min()), float(win.max())
        lo = wlo if lo is None else min(lo, wlo)
        hi = whi if hi is None else max(hi, whi)
    if lo is None or hi is None or lo == hi:
        return None
    pad = max((hi - lo) * 0.06, 0.5)                 # % 축 최소 0.5%p 패딩
    return lo - pad, hi + pad


def _trade_price(hist, trade: dict):
    price = trade.get("price")
    try:
        if price is not None and float(price) > 0:
            return float(price)
    except (TypeError, ValueError):
        pass
    try:
        close = hist["Close"].dropna()
        if close.empty:
            return None
        import pandas as pd
        ts = pd.Timestamp(trade.get("timestamp") or trade.get("date"))
        loc = close.index.get_indexer([ts], method="nearest")[0]
        if loc >= 0:
            return float(close.iloc[loc])
    except Exception:
        return None
    return None


def _add_trade_markers(fig, hist, trades):
    if not trades:
        return
    go = _go()
    for side, color, symbol, name in (
        ("buy", _GREEN, "triangle-up", "Buy"),
        ("sell", _RED, "triangle-down", "Sell"),
    ):
        rows = [t for t in trades if str(t.get("side", "")).lower() == side]
        if not rows:
            continue
        xs, ys, custom = [], [], []
        for t in rows:
            px = _trade_price(hist, t)
            if px is None:
                continue
            xs.append(t.get("timestamp") or t.get("date"))
            ys.append(px)
            custom.append([
                t.get("event_id"),
                "매수" if side == "buy" else "매도",
                t.get("qty"),
                px,
                t.get("avg_price"),
                t.get("account"),
                t.get("source"),
                t.get("timestamp") or t.get("date"),
                t.get("note"),
                t.get("currency") or "USD",
            ])
        if not xs:
            continue
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="markers",
            name=name,
            showlegend=False,
            customdata=custom,
            marker=dict(symbol=symbol, size=13, color=color,
                        line=dict(color="#ffffff", width=0.8)),
            hovertemplate=(
                "<b>%{customdata[1]}</b> %{customdata[7]}<br>"
                "수량 %{customdata[2]:,.4g}주 · 체결 %{customdata[9]} %{customdata[3]:,.2f}<br>"
                "평단 %{customdata[9]} %{customdata[4]:,.2f}<br>"
                "%{customdata[5]} · %{customdata[6]}<br>"
                "%{customdata[8]}<extra></extra>"
            ),
        ))


def allocation_donut(holdings: list[dict]):
    """보유 비중 도넛. holdings: [{ticker, value, ...}]."""
    go = _go()
    items = [(h.get("ticker", "?"), h.get("value", 0) or 0, h.get("name") or h.get("ticker", "?"))
             for h in holdings if (h.get("value", 0) or 0) > 0]
    items.sort(key=lambda x: x[1], reverse=True)
    labels = [t for t, _, _ in items]
    values = [v for _, v, _ in items]
    names = [n for _, _, n in items]  # 호버에 회사명(웨지 라벨은 티커 유지 — 공간)
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.58, customdata=names,
                           textinfo="label+percent", textposition="outside", sort=False,
                           automargin=True,   # 바깥 라벨이 잘리지 않게 플롯이 여백 확보
                           textfont_size=12,
                           hovertemplate="%{customdata} (%{label})<br>%{percent} · %{value:,.0f}<extra></extra>"))
    # 바깥 라벨 공간(좁은 컬럼서도 안 잘리게): 여백 넉넉 + 높이 확대 + automargin
    fig.update_layout(margin=dict(t=36, b=36, l=48, r=48), height=380, showlegend=False)
    return _t(fig)


def price_line(hist, ticker: str = "", avg_cost=None, trades=None, view_days=None):
    """가격 라인 + 20/60일 이동평균 (+ 보유 시 평단 수평선). hist: OHLC DataFrame(Close 필요)."""
    go = _go()
    fig = go.Figure()
    if hist is None or getattr(hist, "empty", True) or "Close" not in getattr(hist, "columns", []):
        return _t(fig)
    close = hist["Close"]
    fig.add_trace(go.Scatter(x=hist.index, y=close, name=ticker or "종가", line=dict(color=_BLUE, width=2)))
    for win, color in ((20, "#f59e0b"), (60, "#9333ea")):
        if len(close) >= win:
            fig.add_trace(go.Scatter(x=hist.index, y=close.rolling(win).mean(),
                                     name=f"MA{win}", line=dict(width=1)))
    # 평단(avg cost) 수평 점선 — 보유 종목의 매수 평균가를 차트에 오버레이
    if avg_cost and avg_cost > 0:
        fig.add_hline(y=avg_cost, line=dict(color=theme.MUTED, dash="dash", width=1.2),
                      annotation_text=f"평단 ${avg_cost:,.2f}", annotation_position="top left",
                      annotation_font=dict(color=theme.MUTED, size=11))
    _add_trade_markers(fig, hist, trades or [])
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10),
                      legend=dict(orientation="h", y=1.1), hovermode="x unified")
    return _initial_view(_pannable(_t(fig)), hist, view_days, lo_col="Close", hi_col="Close")


def price_candle(hist, ticker: str = "", avg_cost=None, trades=None, view_days=None):
    """가격 캔들(OHLC) + 20/60일 이동평균 (+ 보유 시 평단 수평선). hist: OHLC DataFrame."""
    go = _go()
    fig = go.Figure()
    cols = set(getattr(hist, "columns", []))
    if hist is None or getattr(hist, "empty", True) or not {"Open", "High", "Low", "Close"} <= cols:
        return _t(fig)
    fig.add_trace(go.Candlestick(
        x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"], close=hist["Close"],
        name=ticker or "OHLC", increasing_line_color=_GREEN, decreasing_line_color=_RED,
        increasing_fillcolor=_GREEN, decreasing_fillcolor=_RED, line=dict(width=1)))
    close = hist["Close"]
    for win in (20, 60):
        if len(close) >= win:
            fig.add_trace(go.Scatter(x=hist.index, y=close.rolling(win).mean(),
                                     name=f"MA{win}", line=dict(width=1)))
    if avg_cost and avg_cost > 0:
        fig.add_hline(y=avg_cost, line=dict(color=theme.MUTED, dash="dash", width=1.2),
                      annotation_text=f"평단 ${avg_cost:,.2f}", annotation_position="top left",
                      annotation_font=dict(color=theme.MUTED, size=11))
    _add_trade_markers(fig, hist, trades or [])
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10),
                      legend=dict(orientation="h", y=1.1),
                      hovermode="x unified")
    return _initial_view(_pannable(_t(fig)), hist, view_days)


def market_treemap(rows: list[dict], height: int = 560):
    """시장 맵 트리맵 (Finviz 풍). rows:[{ticker,name,sector_kr,market_cap,pct,sub?}].

    섹터→(선택: 세부 카테고리 sub)→종목 2~3계층. 타일 크기=시총, 색=당일 등락%(±3 클램프).
    id/parent 체계로 라벨 충돌 방지 — 종목 노드 label=티커(클릭→정규화→분석 이동 계약 유지).
    """
    go = _go()
    rows = [r for r in (rows or []) if (r.get("market_cap") or 0) > 0 and r.get("pct") is not None]
    if not rows:
        return _t(go.Figure())
    from collections import defaultdict
    sec_sum: dict[str, float] = defaultdict(float)
    sub_sum: dict[tuple, float] = defaultdict(float)
    for r in rows:
        mc = float(r["market_cap"])
        sec_sum[r["sector_kr"]] += mc
        if r.get("sub"):
            sub_sum[(r["sector_kr"], r["sub"])] += mc
    ids, labels, parents, values, colors, texts, custom = [], [], [], [], [], [], []
    for s in sorted(sec_sum):                       # 섹터 루트노드 (값=자식 시총합)
        ids.append(f"sec:{s}"); labels.append(s); parents.append("")
        values.append(sec_sum[s]); colors.append(0.0)
        texts.append(f"<b>{s}</b>"); custom.append("")
    for (s, sub), v in sorted(sub_sum.items()):     # 세부 카테고리 (기술→반도체 등)
        ids.append(f"sub:{s}/{sub}"); labels.append(sub); parents.append(f"sec:{s}")
        values.append(v); colors.append(0.0)
        texts.append(f"<b>{sub}</b>"); custom.append("")
    for r in rows:
        parent = f"sub:{r['sector_kr']}/{r['sub']}" if r.get("sub") else f"sec:{r['sector_kr']}"
        ids.append(f"t:{r['ticker']}"); labels.append(r["ticker"]); parents.append(parent)
        values.append(float(r["market_cap"]))
        colors.append(max(-3.0, min(3.0, float(r["pct"]))))   # ±3 클램프(대비)
        texts.append(f'{r.get("tile") or r["ticker"]}<br>{r["pct"]:+.2f}%')
        custom.append(r.get("name") or r["ticker"])
    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        text=texts, textinfo="text", textposition="middle center",
        textfont=dict(size=11, color="white", family=theme._MONO),
        customdata=custom,
        marker=dict(colors=colors,
                    colorscale=[[0.0, _RED], [0.5, "#1a1d26"], [1.0, _GREEN]],
                    cmid=0, cmin=-3, cmax=3, showscale=False,
                    line=dict(width=1, color="#0e1117")),
        tiling=dict(pad=1),
        hovertemplate="%{customdata} (%{label})<br>시총 %{value:,.0f}<extra></extra>"))
    fig.update_layout(margin=dict(t=6, b=6, l=6, r=6), height=height)
    return _t(fig)


def hbar(labels: list[str], values: list[float], title: str = "", pct: bool = True,
         x_range=None):
    """가로 막대 (위험기여·비중 등). 큰 값이 위로. x_range=(lo, hi) — 고정축(백분위 등)."""
    go = _go()
    pairs = sorted(zip(labels, values), key=lambda x: x[1])
    fig = go.Figure(go.Bar(
        x=[v * 100 if pct else v for _, v in pairs],
        y=[l for l, _ in pairs], orientation="h", marker_color=_BLUE,
        text=[f"{v*100:.0f}%" if pct else f"{v:.2f}" for _, v in pairs], textposition="auto"))
    # t=10 이 제목을 잘라먹던 원인 — 제목 있을 때만 상단 여백 확보 + 좌측 앵커
    fig.update_layout(margin=dict(t=44 if title else 10, b=10, l=10, r=24),
                      height=max(180, 36 * len(pairs)),
                      xaxis_title=None, yaxis_title=None)
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    if x_range:
        fig.update_xaxes(range=list(x_range))
    if title:                       # None 제목은 plotly.js 가 "undefined" 로 렌더 → 비면 미설정
        fig.update_layout(title=dict(text=title, x=0.01, xanchor="left",
                                     font=dict(size=13)))
    return _t(fig)


def signed_bars(labels: list[str], values: list[float], title: str = ""):
    """부호 막대 (서프라이즈·팩터β 등). +초록 −빨강."""
    go = _go()
    colors = [_GREEN if (v or 0) >= 0 else _RED for v in values]
    fig = go.Figure(go.Bar(x=labels, y=values, marker_color=colors,
                           text=[f"{v:+.1f}" for v in values], textposition="auto",
                           cliponaxis=False))   # 음수 바 바깥 라벨(-0.1 등) 축 클리핑 방지
    # automargin + 여백 → x축 카테고리 라벨·바닥 값 안 잘림 (도넛 선례)
    fig.update_layout(margin=dict(t=14, b=52, l=14, r=14), height=300)
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    if title:
        fig.update_layout(title=title)
    return _t(fig)


def bullet_bands(price: float, rows: list, x_title: str = "적정가 ($)", height: int = 160):
    """현재가 vs 적정가 밴드(범용 수평 불릿) — rows=[(라벨, lo, mid, hi)]."""
    go = _go()
    fig = go.Figure()
    for name, lo, mid, hi in rows:
        if lo is None or hi is None:
            continue
        fig.add_trace(go.Scatter(x=[lo, hi], y=[name, name], mode="lines",
                                 line=dict(color=_GRID, width=10), showlegend=False))
        fig.add_trace(go.Scatter(x=[mid], y=[name], mode="markers",
                                 marker=dict(color=_BLUE, size=12), showlegend=False))
    if price:
        fig.add_vline(x=price, line=dict(color=_RED, dash="dash"),
                      annotation_text=f"현재 ${price:,.0f}")
    fig.update_layout(margin=dict(t=24, b=10, l=10, r=10), height=height,
                      xaxis_title=x_title)
    return _t(fig)


def value_bullet(price: float, rim: dict | None, ddm: dict | None):
    """현재가 vs RIM/DDM 적정가 밴드 (수평 범위 막대)."""
    go = _go()
    fig = go.Figure()
    rows = []
    if rim:
        rows.append(("RIM", rim.get("low"), rim.get("mid"), rim.get("high")))
    if ddm:
        rows.append(("DDM", ddm.get("low"), ddm.get("mid"), ddm.get("high")))
    for name, lo, mid, hi in rows:
        if lo is None or hi is None:
            continue
        fig.add_trace(go.Scatter(x=[lo, hi], y=[name, name], mode="lines",
                                 line=dict(color=_GRID, width=10), showlegend=False))
        fig.add_trace(go.Scatter(x=[mid], y=[name], mode="markers",
                                 marker=dict(color=_BLUE, size=12), showlegend=False))
    if price:
        fig.add_vline(x=price, line=dict(color=_RED, dash="dash"),
                      annotation_text=f"현재 ${price:,.0f}")
    fig.update_layout(margin=dict(t=24, b=10, l=10, r=10), height=180, xaxis_title="적정가 ($)")
    return _t(fig)


def equity_curve(equity):
    """백테스트 이퀴티 (단일 또는 다중 시리즈 DataFrame)."""
    go = _go()
    fig = go.Figure()
    try:
        import pandas as pd
        if isinstance(equity, pd.DataFrame):
            for col in equity.columns:
                fig.add_trace(go.Scatter(y=equity[col], name=str(col)))
        else:
            s = pd.Series(equity)
            fig.add_trace(go.Scatter(y=s, name="equity", line=dict(color=_BLUE)))
    except Exception:
        pass
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300,
                      legend=dict(orientation="h", y=1.1))
    return _t(fig)


def nav_curve(points: list[dict], currency: str = "₩"):
    """모의 계좌 NAV 시계열 (+ 인셉션 기준선). points: [{date, nav}] (paper_summary 출력)."""
    go = _go()
    fig = go.Figure()
    pts = [p for p in (points or []) if p.get("nav") is not None]
    if not pts:
        return _t(fig)
    dates = [p.get("date") for p in pts]
    navs = [float(p["nav"]) for p in pts]
    up = navs[-1] >= navs[0]
    color = _GREEN if up else _RED
    fig.add_trace(go.Scatter(x=dates, y=navs, name="NAV", mode="lines",
                             line=dict(color=color, width=2), fill="tozeroy",
                             fillcolor=("rgba(52,211,153,0.08)" if up else "rgba(248,113,113,0.08)"),
                             hovertemplate="%{x}<br>NAV " + currency + "%{y:,.0f}<extra></extra>"))
    fig.add_hline(y=navs[0], line=dict(color=theme.MUTED, dash="dash", width=1),
                  annotation_text="인셉션", annotation_position="top left",
                  annotation_font=dict(color=theme.MUTED, size=11))
    lo, hi = min(navs), max(navs)
    pad = (hi - lo) * 0.15 or hi * 0.01 or 1.0
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280, showlegend=False,
                      yaxis=dict(range=[lo - pad, hi + pad]), hovermode="x unified")
    return _t(fig)


def learning_curve(series):
    """모의 정책 학습 진화 — 주별 OOS 초과수익 + 순비용 IC(우축) + 채택 마커(★).

    series: [{date, excess, ic, adopted}] (evolution_summary 출력).
    """
    go = _go()
    fig = go.Figure()
    pts = [s for s in (series or []) if s.get("excess") is not None]
    if not pts:
        return _t(fig)
    dates = [s.get("date") for s in pts]
    fig.add_trace(go.Scatter(x=dates, y=[s["excess"] for s in pts], name="OOS 초과수익",
                             line=dict(color=_BLUE, width=2)))
    if any(s.get("ic") is not None for s in pts):
        fig.add_trace(go.Scatter(x=dates, y=[s.get("ic") for s in pts], name="순비용 IC",
                                 yaxis="y2", line=dict(color=theme.GREEN, dash="dot")))
    ax = [s["date"] for s in pts if s.get("adopted")]
    ay = [s["excess"] for s in pts if s.get("adopted")]
    if ax:
        fig.add_trace(go.Scatter(x=ax, y=ay, name="채택", mode="markers",
                                 marker=dict(color=theme.GREEN, size=12, symbol="star")))
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300,
                      legend=dict(orientation="h", y=1.12),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, zeroline=False))
    return _t(fig)


def intraday_candle(hist, ticker: str = "", trades=None, vwap=None,
                    or_range=None, levels=None):
    """단기(1m/5m) 캔들 + ▲▼ 트레이드 마커 + VWAP·시가범위(OR) 박스·스톱/목표선.

    hist: 분봉 OHLCV DataFrame · trades: trade_events 레코드(마커 클릭용 customdata 포함)
    vwap: hist.index 정렬 시계열 | None · or_range: (hi, lo, end_ts) | None ·
    levels: [{"y", "label", "color"}] (스톱·목표 수평 점선).
    """
    go = _go()
    fig = go.Figure()
    cols = set(getattr(hist, "columns", []))
    if hist is None or getattr(hist, "empty", True) or not {"Open", "High", "Low", "Close"} <= cols:
        return _t(fig)
    fig.add_trace(go.Candlestick(
        x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"], close=hist["Close"],
        name=ticker or "OHLC", increasing_line_color=_GREEN, decreasing_line_color=_RED,
        increasing_fillcolor=_GREEN, decreasing_fillcolor=_RED, line=dict(width=1)))
    if vwap is not None and len(vwap) == len(hist):
        fig.add_trace(go.Scatter(x=hist.index, y=list(vwap), name="VWAP",
                                 line=dict(color="#f59e0b", width=1.4, dash="dot")))
    if or_range:
        hi, lo, end = or_range
        fig.add_shape(type="rect", x0=hist.index[0], x1=end, y0=lo, y1=hi,
                      fillcolor="rgba(59,130,246,0.10)", line=dict(color="#3b82f6", width=1))
    for lv in (levels or []):
        if lv.get("y"):
            fig.add_hline(y=lv["y"], line=dict(color=lv.get("color", theme.MUTED),
                                               dash="dash", width=1.1),
                          annotation_text=lv.get("label", ""), annotation_position="right",
                          annotation_font=dict(size=10, color=lv.get("color", theme.MUTED)))
    _add_trade_markers(fig, hist, trades or [])
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10),
                      legend=dict(orientation="h", y=1.1), hovermode="x unified")
    return _pannable(_t(fig), height=400)


def analyst_ratings(dist: dict):
    """애널리스트 의견 분포 바 (토스 풍) — dist: {strong_sell,sell,hold,buy,strong_buy} 명수.

    최다 카테고리만 시맨틱 색(매수측 초록·매도측 빨강·중립 회색), 나머지는 딤 처리.
    """
    go = _go()
    order = [("strong_sell", "적극 매도"), ("sell", "매도"), ("hold", "중립"),
             ("buy", "매수"), ("strong_buy", "적극 매수")]
    counts = [max(0.0, float(dist.get(k) or 0)) for k, _ in order]
    if not any(counts):
        return _t(go.Figure())
    top = counts.index(max(counts))
    sem = {0: _RED, 1: _RED, 2: theme.MUTED, 3: _GREEN, 4: _GREEN}
    colors = [sem[i] if i == top else "#2a2e39" for i in range(5)]
    fig = go.Figure(go.Bar(
        x=[lb for _, lb in order], y=counts,
        text=[f"{int(c)}명" for c in counts], textposition="outside",
        marker=dict(color=colors, cornerradius=6),
        hovertemplate="%{x}: %{y:.0f}명<extra></extra>"))
    fig.update_layout(margin=dict(t=28, b=10, l=10, r=10), height=230, showlegend=False,
                      yaxis=dict(visible=False, range=[0, max(counts) * 1.25]),
                      xaxis=dict(showgrid=False))
    return _t(fig)


def target_price_fan(hist, price, high, mean, low, currency: str = "$"):
    """예상 목표주가 팬 차트 (토스 풍) — 과거 1y 종가 + 1년 후 최고/평균/최저 점선 투영.

    hist: Close 포함 DataFrame | None(가격 이력 없이 투영만). price: 현재가(필수).
    mean 없으면 빈 Figure. 라벨에 목표가·상승률 병기, 현재가 수평 점선 기준.
    """
    go = _go()
    fig = go.Figure()
    p = float(price or 0)
    if not p or not mean:
        return _t(fig)
    import pandas as pd
    if hist is not None and not getattr(hist, "empty", True) and "Close" in hist.columns:
        close = hist["Close"].dropna()
        if not close.empty:
            last_x = close.index[-1]
            fig.add_trace(go.Scatter(x=close.index, y=close, name="주가",
                                     line=dict(color="#8b93a7", width=1.8),
                                     hovertemplate="%{x|%y.%m.%d} %{y:,.0f}<extra></extra>"))
        else:
            last_x = pd.Timestamp.now()
    else:
        last_x = pd.Timestamp.now()
    future = last_x + pd.Timedelta(days=365)
    fmt = (lambda v: f"{currency}{v:,.0f}") if currency == "₩" else (lambda v: f"{currency}{v:,.2f}")
    targets = [("최고", high, _GREEN), ("평균", mean, "#f59e0b"), ("최저", low, _RED)]
    for name, tv, color in targets:
        if not tv:
            continue
        tv = float(tv)
        fig.add_trace(go.Scatter(
            x=[last_x, future], y=[p, tv], mode="lines+markers", name=name,
            line=dict(color=color, dash="dot", width=1.6),
            marker=dict(size=[0, 8], color=color),
            hovertemplate=f"{name} {fmt(tv)} ({(tv / p - 1) * 100:+.1f}%)<extra></extra>"))
        fig.add_annotation(x=future, y=tv, xanchor="left", showarrow=False,
                           text=f" {name} {fmt(tv)} ({(tv / p - 1) * 100:+.1f}%)",
                           font=dict(size=11, color=color))
    fig.add_hline(y=p, line=dict(color=theme.MUTED, dash="dash", width=1),
                  annotation_text=f"현재 {fmt(p)}", annotation_position="bottom left",
                  annotation_font=dict(size=10, color=theme.MUTED))
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=150), height=340, showlegend=False,
                      hovermode="closest")
    return _t(fig)


# ── 기술적 분석 지표 (순수 계산 + 합성 차트) ──────────────────────────────────

def _rsi_series(close, n: int = 14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


_MA_COLORS = {5: "#e879f9", 10: "#22d3ee", 20: "#f59e0b", 60: "#9333ea",
              120: "#34d399", 200: "#f43f5e"}
_CMP_COLORS = ["#f59e0b", "#e879f9", "#22d3ee"]   # 비교 종목 팔레트 (메인=BLUE)


def normalize_pct(series, view_days=None):
    """종가 시리즈 → 표시창 시작=0% 기준 상대수익률(%) — 비교 오버레이용 (순수).

    앵커 = 마지막 봉 − view_days 이후 첫 봉의 값(없으면 첫 봉). 앵커 이전 구간도
    같은 앵커로 환산해 팬 백 시 연속 표시(리베이스 앵커는 표시창 시작 고정 — v2 JS 재기준).
    """
    import pandas as pd
    s = series.dropna()
    if s is None or len(s) == 0:
        return s
    anchor_val = float(s.iloc[0])
    if view_days:
        start = s.index[-1] - pd.Timedelta(days=int(view_days))
        after = s[s.index >= start]
        if len(after):
            anchor_val = float(after.iloc[0])
    if anchor_val == 0:
        return s * float("nan")
    return (s / anchor_val - 1.0) * 100.0


def view_window(hist, view_days, pan_mult: int = 5, warmup_bars: int = 250,
                floor_bars: int = 800):
    """차트 직렬화용 데이터 윈도잉 — 뷰(기간)의 pan_mult배 팬버퍼 + 지표 워밍업 tail (순수).

    기간 라디오는 초기 표시창만 좁히고 데이터는 항상 전체(max·장기주 ~11k봉)를
    fig+bounds 로 직렬화하던 것이 지표/기간 토글마다 수 MB websocket push + 수초
    ScriptRunner 점유의 주원인("채널 쓰면 다운" 체감). 뷰의 pan_mult배 팬버퍼면
    과거 드래그 체감은 유지되고, warmup_bars 는 MA200·일목(52+26) 등 롤링 지표가
    팬버퍼 구간에서 깨지지 않을 여유분. view_days=None(전체)·짧은 데이터는 그대로
    반환. DataFrame/Series 모두 지원(tail 뷰 반환).
    """
    if view_days is None or hist is None or getattr(hist, "empty", True):
        return hist
    import pandas as pd
    try:
        cutoff = hist.index[-1] - pd.Timedelta(days=int(view_days))
        view_bars = int((hist.index >= cutoff).sum())
    except Exception:
        return hist                                 # 비시계열 인덱스 등 — 무윈도잉
    keep = max(view_bars * int(pan_mult) + int(warmup_bars), int(floor_bars))
    if keep >= len(hist):
        return hist
    return hist.iloc[-keep:]


def _add_event_markers(fig, hist, events, panes) -> None:
    """이벤트 마커 — 실적(E)·배당(D)·뉴스(N) 등을 봉 아래 원형 배지로 (표시·참고용).

    events: [{date, marker(1글자), color, hover}]. y = 해당 시점 봉 저가의 1.5% 아래
    (스케일 무관 비율 오프셋 — 로그축에서도 일정한 시각 간격). 봉 범위 밖 날짜는 스킵.
    """
    if not events:
        return
    import pandas as pd
    go = _go()
    cols = set(getattr(hist, "columns", []))
    low = (hist["Low"] if "Low" in cols else hist["Close"]).dropna()
    if low.empty:
        return
    kw = dict(row=1, col=1) if panes > 1 else {}
    groups: dict = {}
    for ev in events:
        try:
            ts = pd.Timestamp(ev.get("date"))
        except Exception:
            continue
        # tz 정합 — yfinance 인덱스는 tz-aware(미국장), 이벤트 날짜는 보통 naive
        idx_tz = getattr(low.index, "tz", None)
        if ts.tzinfo is None and idx_tz is not None:
            ts = ts.tz_localize(idx_tz)
        elif ts.tzinfo is not None and idx_tz is None:
            ts = ts.tz_localize(None)
        try:
            base = low.asof(ts)                     # 해당 시점(이전 최근접) 봉 저가
        except Exception:
            continue
        if base != base or ts < low.index[0]:       # NaN·범위 밖 스킵
            continue
        g = groups.setdefault((str(ev.get("marker", "•"))[:1],
                               ev.get("color") or theme.MUTED),
                              {"x": [], "y": [], "hover": []})
        g["x"].append(ts)
        g["y"].append(float(base) * 0.985)
        g["hover"].append(str(ev.get("hover") or ""))
    for (letter, color), g in groups.items():
        fig.add_trace(go.Scatter(
            x=g["x"], y=g["y"], mode="markers+text", name=f"이벤트 {letter}",
            showlegend=False, text=[letter] * len(g["x"]), customdata=g["hover"],
            textfont=dict(size=8, color="#ffffff"),
            marker=dict(symbol="circle", size=11, color=color, opacity=0.9),
            hovertemplate="%{customdata}<extra></extra>"), **kw)


def _add_entry_zones(fig, zones, panes) -> None:
    """진입 합류 존 밴드 — 지지 클러스터(재료 겹침)를 반투명 파랑 밴드로 (표시·참고용)."""
    kw = dict(row=1, col=1) if panes > 1 else {}
    for z in zones or []:
        lo, hi = z.get("lo"), z.get("hi")
        if not lo or not hi:
            continue
        if hi <= lo * 1.0005:                       # 점 존 → 얇은 밴드로 시각화
            lo, hi = lo * 0.9985, hi * 1.0015
        fig.add_hrect(y0=lo, y1=hi, fillcolor="rgba(41,98,255,0.10)", line_width=0, **kw)
        fig.add_annotation(xref="x domain", x=0.004, y=(lo + hi) / 2, yref="y",
                           xanchor="left", showarrow=False,
                           text=z.get("label", ""), font=dict(size=9, color="#7ea6ff"),
                           bgcolor="rgba(10,14,23,.55)",
                           **({"row": 1, "col": 1} if panes > 1 else {}))


def heikin_ashi(hist):
    """하이킨아시 변환 — 표시용 평활 캔들 (OHLC 재계산·Volume 보존·순수).

    HA종가=(O+H+L+C)/4 · HA시가=직전(HA시가+HA종가)/2 (재귀) · 고저는 원시고저 포함 최대/최소.
    **표시용 변형** — 실제 체결가와 다르므로 콜아웃·평단선 비교는 근사임을 호출부가 표기.
    OHLC 없으면 원본 그대로 반환(graceful).
    """
    import numpy as np
    import pandas as pd
    cols = set(getattr(hist, "columns", []))
    if hist is None or getattr(hist, "empty", True) or not {"Open", "High", "Low", "Close"} <= cols:
        return hist
    o_, h_, l_, c_ = (hist[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    ha_c = (o_ + h_ + l_ + c_) / 4.0
    ha_o = np.empty_like(ha_c)
    ha_o[0] = (o_[0] + c_[0]) / 2.0
    for i in range(1, len(ha_c)):                     # 재귀 정의 — 벡터화 불가
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    ha_h = np.maximum.reduce([h_, ha_o, ha_c])
    ha_l = np.minimum.reduce([l_, ha_o, ha_c])
    out = pd.DataFrame({"Open": ha_o, "High": ha_h, "Low": ha_l, "Close": ha_c},
                       index=hist.index)
    if "Volume" in cols:
        out["Volume"] = hist["Volume"].to_numpy()
    return out


def _add_trend_lines(fig, items: list[dict]) -> None:
    """자동 감지 추세선·채널 오버레이 (dashboard.trendlines 출력 스키마 소비).

    지지=초록 대시·저항=빨강 대시·채널=중심 점선+상하단(반투명 fill·path 폴백). 표시·참고용.
    """
    go = _go()
    for it in items or []:
        kind = it.get("kind")
        if kind == "channel":
            color = {"up": _GREEN, "down": _RED}.get((it.get("meta") or {}).get("trend"), "#8b93a7")
            path = it.get("path")
            if path:
                xs, up, lo = path["x"], path["upper"], path["lower"]
            else:
                xs = [it["x0"], it["x1"]]
                up, lo = list(it["upper"]), list(it["lower"])
            fig.add_trace(go.Scatter(x=xs, y=up, name=it["label"], legendgroup=it["label"],
                                     line=dict(color=color, width=1.1)))
            fig.add_trace(go.Scatter(x=xs, y=lo, showlegend=False, legendgroup=it["label"],
                                     line=dict(color=color, width=1.1),
                                     fill="tonexty", fillcolor="rgba(139,147,167,0.07)"))
            fig.add_trace(go.Scatter(x=[it["x0"], it["x1"]], y=[it["y0"], it["y1"]],
                                     showlegend=False, legendgroup=it["label"],
                                     line=dict(color=color, width=0.8, dash="dot")))
        else:
            color = _GREEN if kind == "support" else _RED
            fig.add_trace(go.Scatter(x=[it["x0"], it["x1"]], y=[it["y0"], it["y1"]],
                                     name=it["label"], line=dict(color=color, width=1.4, dash="dash")))
        fig.add_annotation(x=it["x1"], y=it["y1"], xanchor="left", showarrow=False,
                           text=it["label"], font=dict(size=10, color=theme.MUTED))


def price_chart(hist, ticker: str = "", *, kind: str = "line", avg_cost=None,
                trades=None, view_days=None, mas=(60, 120, 200),
                show_rsi: bool = False, bollinger: bool = False, ichimoku: bool = False,
                trend_lines=None, show_volume: bool = False, supertrend: bool = False,
                envelope: bool = False, fractals: bool = False, vol_profile: bool = False,
                emas=(), psar: bool = False, donchian_on: bool = False,
                vwap: bool = False, avwap: bool = False, compare=None,
                show_macd: bool = False, show_stoch: bool = False, log_scale: bool = False,
                keltner: bool = False, kama: bool = False, chandelier: bool = False,
                show_aroon: bool = False, show_bbpct: bool = False, show_pvt: bool = False,
                fundamentals=None, events=None, zones=None):
    """가격 차트 + 기술적 분석 도구 (TradingView 풍 멀티패널).

    패널: 가격(+MA·BB·일목·추세선·평단·기간 최고/최저·현재가 라벨) / 거래량(방향색 바+MA20)
    / RSI(14)+시그널(14MA)+30~70 밴드. 서브패널 있으면 레인지슬라이더 비활성(plotly 제약 —
    팬/줌 유지). 지표는 범례 클릭으로 개별 토글 가능.

    compare = {라벨: 종가 시리즈} — 비교 모드: 전 시리즈를 표시창 시작=0% 상대수익률로
    정규화해 겹침(y축 %). 가격절대 오버레이(캔들·평단·MA·BB·일목·추세선·상단지표·거래마커·
    최고/최저 콜아웃)는 자동 비활성 — RSI·거래량 서브패널은 메인 종목 기준 유지.
    """
    go = _go()
    cols = set(getattr(hist, "columns", []))
    if hist is None or getattr(hist, "empty", True) or "Close" not in cols:
        return _t(go.Figure())
    compare = {k: v for k, v in (compare or {}).items()
               if v is not None and len(getattr(v, "dropna", lambda: [])()) >= 2}
    cmp_mode = bool(compare)
    if cmp_mode:                       # 가격절대 오버레이 전부 비활성 (% 축과 공존 불가)
        kind, avg_cost, trades, trend_lines = "line", None, None, None
        mas, emas = (), ()
        bollinger = ichimoku = supertrend = envelope = fractals = vol_profile = False
        psar = donchian_on = vwap = avwap = False
        keltner = kama = chandelier = False
        log_scale = False              # % 축엔 로그 무의미
        events, zones = None, None     # 절대가격 오버레이 — % 축과 공존 불가
    close = hist["Close"]
    has_ohlc = {"Open", "High", "Low"} <= cols
    show_volume = show_volume and "Volume" in cols
    show_stoch = show_stoch and has_ohlc                # 스토캐스틱은 High/Low 필요
    show_aroon = show_aroon and has_ohlc                # Aroon 은 High/Low 필요
    show_pvt = show_pvt and "Volume" in cols            # PVT 는 거래량 필요

    # 유효 펀더멘털 행만 (매출 or 순이익 하나는 있어야) — ETF·매크로는 빈 rows → 패널 생략
    fund_rows = [r for r in (fundamentals or [])
                 if r.get("date") and (r.get("revenue") is not None
                                       or r.get("net_income") is not None)]

    # 하단 서브패널 — 순서 고정(거래량→RSI→MACD→스토→Aroon→%b→PVT→펀더멘털). 행 동적 배정.
    sub = [("vol", show_volume), ("rsi", show_rsi), ("macd", show_macd), ("stoch", show_stoch),
           ("aroon", show_aroon), ("bbpct", show_bbpct), ("pvt", show_pvt),
           ("fund", bool(fund_rows))]
    active = [name for name, on in sub if on]
    panes = 1 + len(active)
    row_of = {name: i + 2 for i, name in enumerate(active)}   # 가격=1, 서브=2..
    vol_row = row_of.get("vol")
    rsi_row = row_of.get("rsi")
    macd_row = row_of.get("macd")
    stoch_row = row_of.get("stoch")
    aroon_row = row_of.get("aroon")
    bbpct_row = row_of.get("bbpct")
    pvt_row = row_of.get("pvt")
    fund_row = row_of.get("fund")
    if panes > 1:
        from plotly.subplots import make_subplots
        # 2·3패널은 기존 튜닝 비율 유지(회귀 방어), 4·5패널만 일반 분배 규칙,
        # 6+ 패널(신규 하단지표 Aroon·%b·PVT)은 가격 0.40 + 서브 균등 분배
        _HEIGHTS = {2: [0.70, 0.30], 3: [0.58, 0.19, 0.23],
                    4: [0.52, 0.16, 0.16, 0.16], 5: [0.46, 0.135, 0.135, 0.135, 0.135]}
        heights = _HEIGHTS.get(panes) or ([0.40] + [0.60 / (panes - 1)] * (panes - 1))
        fig = make_subplots(rows=panes, cols=1, shared_xaxes=True,
                            row_heights=heights, vertical_spacing=0.05)
    else:
        fig = go.Figure()

    # ── 메인: 가격 ──
    # 대용량(≥1500봉) 라인은 WebGL(Scattergl) — 캔들/소용량은 SVG 유지(스타일 동일)
    _SC = go.Scattergl if len(close.dropna()) >= 1500 else go.Scatter
    if cmp_mode:                       # 비교 — % 상대수익 라인 (메인 + 비교 각자 인덱스)
        n_main = normalize_pct(close, view_days)
        fig.add_trace(_SC(x=n_main.index, y=n_main, name=ticker or "메인",
                          hovertemplate="%{y:+.2f}%<extra>" + (ticker or "메인") + "</extra>",
                          line=dict(color=_BLUE, width=2)))
        for i, (lab, s) in enumerate(compare.items()):
            ns = normalize_pct(s, view_days)
            fig.add_trace(_SC(x=ns.index, y=ns, name=lab,
                              hovertemplate="%{y:+.2f}%<extra>" + lab + "</extra>",
                              line=dict(color=_CMP_COLORS[i % len(_CMP_COLORS)],
                                        width=1.6)))
        fig.add_hline(y=0, line=dict(color=theme.MUTED, dash="dot", width=0.8),
                      row=1 if panes > 1 else None, col=1 if panes > 1 else None)
    elif kind == "candle" and has_ohlc:
        fig.add_trace(go.Candlestick(
            x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"],
            close=close, name=ticker or "OHLC",
            increasing_line_color=_GREEN, decreasing_line_color=_RED,
            increasing_fillcolor=_GREEN, decreasing_fillcolor=_RED, line=dict(width=1)))
    else:
        fig.add_trace(_SC(x=hist.index, y=close, name=ticker or "종가",
                          line=dict(color=_BLUE, width=2)))

    # ── 일목균형표 (구름은 MA 아래 깔리게 먼저) — 대용량은 WebGL(_SC) ──
    if ichimoku and has_ohlc and len(close) >= 52:
        h9 = (hist["High"].rolling(9).max() + hist["Low"].rolling(9).min()) / 2      # 전환선
        h26 = (hist["High"].rolling(26).max() + hist["Low"].rolling(26).min()) / 2   # 기준선
        spa = ((h9 + h26) / 2).shift(26)                                             # 선행스팬A
        spb = ((hist["High"].rolling(52).max()
                + hist["Low"].rolling(52).min()) / 2).shift(26)                      # 선행스팬B
        # 구름 fill 은 양쪽 다 유효한 구간만 (선두 NaN 제거 — WebGL fill 아티팩트 방지)
        cloud = spa.notna() & spb.notna()
        ci = hist.index[cloud]
        fig.add_trace(_SC(x=ci, y=spa[cloud], name="선행A",
                          line=dict(color="#26a69a", width=0.8), opacity=0.6))
        fig.add_trace(_SC(x=ci, y=spb[cloud], name="선행B(구름)",
                          line=dict(color="#ef5350", width=0.8), opacity=0.6,
                          fill="tonexty", fillcolor="rgba(120,140,180,0.12)"))
        fig.add_trace(_SC(x=hist.index, y=h9, name="전환선(9)",
                          line=dict(color="#22d3ee", width=1)))
        fig.add_trace(_SC(x=hist.index, y=h26, name="기준선(26)",
                          line=dict(color="#f59e0b", width=1)))
        fig.add_trace(_SC(x=hist.index, y=close.shift(-26), name="후행스팬",
                          line=dict(color="#e879f9", width=0.8, dash="dot")))

    # ── 이동평균 세트 ──
    for win in sorted(set(int(w) for w in (mas or []))):
        if len(close) >= win:
            fig.add_trace(_SC(
                x=hist.index, y=close.rolling(win).mean(), name=f"MA{win}",
                line=dict(width=1.1, color=_MA_COLORS.get(win))))

    # ── 볼린저밴드 (20, ±2σ) ──
    if bollinger and len(close) >= 20:
        ma20 = close.rolling(20).mean()
        sd = close.rolling(20).std()
        bb_ok = ma20.notna() & sd.notna()
        bi = hist.index[bb_ok]
        fig.add_trace(_SC(x=bi, y=(ma20 + 2 * sd)[bb_ok], name="BB상단",
                          line=dict(color="#8b93a7", width=0.8, dash="dot")))
        fig.add_trace(_SC(x=bi, y=(ma20 - 2 * sd)[bb_ok], name="BB하단",
                          line=dict(color="#8b93a7", width=0.8, dash="dot"),
                          fill="tonexty", fillcolor="rgba(139,147,167,0.08)"))

    # ── 켈트너 채널 (EMA20 ± 2×ATR10) ──
    if keltner and has_ohlc and len(close) >= 20:
        k_mid = close.ewm(span=20, adjust=False).mean()
        k_atr = _atr_series(hist, 10)
        k_ok = k_mid.notna() & k_atr.notna()
        ki = hist.index[k_ok]
        fig.add_trace(_SC(x=ki, y=(k_mid + 2 * k_atr)[k_ok], name="켈트너 상단",
                          line=dict(color="#22d3ee", width=0.8, dash="dot")))
        fig.add_trace(_SC(x=ki, y=(k_mid - 2 * k_atr)[k_ok], name="켈트너 하단",
                          line=dict(color="#22d3ee", width=0.8, dash="dot"),
                          fill="tonexty", fillcolor="rgba(34,211,238,0.06)"))
        fig.add_trace(_SC(x=ki, y=k_mid[k_ok], name="켈트너 중심", showlegend=False,
                          line=dict(color="#22d3ee", width=0.7)))

    # ── KAMA (카우프만 적응 이동평균 10·2·30) — 추세=빠르게·횡보=느리게 ──
    if kama and len(close) >= 12:
        fig.add_trace(_SC(x=hist.index, y=kama_series(close), name="KAMA(10·2·30)",
                          line=dict(color="#f59e0b", width=1.3)))

    # ── 샹들리에 엑시트 (22·3×ATR) — 트레일링 스탑 라인 ──
    if chandelier and has_ohlc and len(close) >= 22:
        c_atr = _atr_series(hist, 22)
        fig.add_trace(_SC(x=hist.index, y=hist["High"].rolling(22).max() - 3 * c_atr,
                          name="샹들리에 롱스탑",
                          line=dict(color=_GREEN, width=1, dash="dash")))
        fig.add_trace(_SC(x=hist.index, y=hist["Low"].rolling(22).min() + 3 * c_atr,
                          name="샹들리에 숏스탑",
                          line=dict(color=_RED, width=1, dash="dash")))

    if avg_cost and avg_cost > 0:
        fig.add_hline(y=avg_cost, line=dict(color=theme.MUTED, dash="dash", width=1.2),
                      annotation_text=f"평단 {avg_cost:,.2f}", annotation_position="top left",
                      annotation_font=dict(color=theme.MUTED, size=11),
                      row=1 if panes > 1 else None, col=1 if panes > 1 else None)
    _add_trend_lines(fig, trend_lines or [])
    _add_top_indicators(fig, hist, supertrend=supertrend, envelope=envelope,
                        fractals=fractals, vol_profile=vol_profile, panes=panes)
    _add_top_indicators2(fig, hist, emas=emas, psar=psar, donchian_on=donchian_on,
                         vwap=vwap, avwap=avwap, view_days=view_days, panes=panes)
    _add_trade_markers(fig, hist, trades or [])
    _add_event_markers(fig, hist, events, panes)
    _add_entry_zones(fig, zones, panes)

    # ── 기간 최고/최저 콜아웃 + 현재가 점선·우측 라벨 (TradingView 풍) ──
    if cmp_mode:                       # % 축 — 가격 콜아웃 대신 % 포맷만
        fig.update_yaxes(ticksuffix="%", tickformat=".1f",
                         row=1 if panes > 1 else None,
                         col=1 if panes > 1 else None)
    else:
        _add_extremes_and_last(fig, hist, view_days, panes)

    # ── 거래량 패널 (방향색 바 + 거래량 MA20) ──
    if show_volume:
        vol = hist["Volume"]
        if has_ohlc:
            up = (close >= hist["Open"]).values
        else:
            up = (close.diff().fillna(0) >= 0).values
        vcolors = [_GREEN if u else _RED for u in up]
        fig.add_trace(go.Bar(x=hist.index, y=vol, name="거래량", showlegend=False,
                             marker=dict(color=vcolors), opacity=0.55),
                      row=vol_row, col=1)
        if len(vol) >= 20:
            fig.add_trace(go.Scatter(x=hist.index, y=vol.rolling(20).mean(),
                                     name="거래량 MA20", showlegend=False, line=dict(color=_GREEN, width=1)),
                          row=vol_row, col=1)
        # 거래량 축 = 상위 2% 분위 캡 — 역사적 스파이크가 축을 지배해 평상 막대가
        # 바닥에 깔리는 것 방지(스파이크는 잘림 — 표준 처방). 최근 60봉 최대는 보장.
        _v = vol.dropna()
        if len(_v) > 20:
            _cap = max(float(_v.quantile(0.98)) * 1.6,
                       float(_v.tail(60).max() or 1) * 1.15)
            fig.update_yaxes(range=[0, _cap], row=vol_row, col=1, nticks=3)
        else:
            fig.update_yaxes(row=vol_row, col=1, nticks=3)

    # ── RSI 패널 (RSI + 시그널(14MA) + 30~70 밴드) ──
    if show_rsi:
        rsi = _rsi_series(close)
        fig.add_hrect(y0=30, y1=70, fillcolor="rgba(120,130,180,0.08)", line_width=0,
                      row=rsi_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=rsi, name="RSI(14)", showlegend=False,
                                 line=dict(color="#9333ea", width=1.2)), row=rsi_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=rsi.rolling(14).mean(), name="RSI 시그널(14)", showlegend=False,
                                 line=dict(color="#3b82f6", width=1.2)), row=rsi_row, col=1)
        for lv, c in ((70, _RED), (30, _GREEN)):
            fig.add_hline(y=lv, line=dict(color=c, dash="dot", width=0.7), row=rsi_row, col=1)
        fig.update_yaxes(range=[0, 100], row=rsi_row, col=1, tickvals=[30, 50, 70])

    # ── MACD 패널 (12·26·9) — 히스토그램(방향색) + MACD·시그널 라인 ──
    if macd_row:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        histo = macd - signal
        hcolors = [_GREEN if v >= 0 else _RED for v in histo.fillna(0)]
        fig.add_trace(go.Bar(x=hist.index, y=histo, name="MACD 히스토", showlegend=False,
                             marker=dict(color=hcolors), opacity=0.5), row=macd_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=macd, name="MACD(12·26)", showlegend=False,
                                 line=dict(color="#3b82f6", width=1.2)), row=macd_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=signal, name="시그널(9)", showlegend=False,
                                 line=dict(color="#f59e0b", width=1.2)), row=macd_row, col=1)
        fig.add_hline(y=0, line=dict(color=theme.MUTED, dash="dot", width=0.7),
                      row=macd_row, col=1)
        fig.update_yaxes(row=macd_row, col=1, nticks=3)

    # ── 스토캐스틱 패널 (%K 14·%D 3) — 20/80 밴드 ──
    if stoch_row:
        low14 = hist["Low"].rolling(14).min()
        high14 = hist["High"].rolling(14).max()
        rng = (high14 - low14).replace(0, float("nan"))
        pctk = ((close - low14) / rng * 100).rolling(3).mean()   # 슬로우 %K
        pctd = pctk.rolling(3).mean()                            # %D
        fig.add_hrect(y0=20, y1=80, fillcolor="rgba(120,130,180,0.08)", line_width=0,
                      row=stoch_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=pctk, name="%K(14)", showlegend=False,
                                 line=dict(color="#22d3ee", width=1.2)), row=stoch_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=pctd, name="%D(3)", showlegend=False,
                                 line=dict(color="#e879f9", width=1.1)), row=stoch_row, col=1)
        for lv, c in ((80, _RED), (20, _GREEN)):
            fig.add_hline(y=lv, line=dict(color=c, dash="dot", width=0.7), row=stoch_row, col=1)
        fig.update_yaxes(range=[0, 100], row=stoch_row, col=1, tickvals=[20, 50, 80])

    # ── Aroon 패널 (25) — 신고/신저 이후 경과 기반 추세 강도 (0~100) ──
    if aroon_row:
        n_ar = 25
        ar_up = hist["High"].rolling(n_ar + 1).apply(
            lambda x: float(x.argmax()) / n_ar * 100, raw=True)
        ar_dn = hist["Low"].rolling(n_ar + 1).apply(
            lambda x: float(x.argmin()) / n_ar * 100, raw=True)
        fig.add_trace(go.Scatter(x=hist.index, y=ar_up, name="Aroon Up", showlegend=False,
                                 line=dict(color=_GREEN, width=1.2)), row=aroon_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=ar_dn, name="Aroon Down", showlegend=False,
                                 line=dict(color=_RED, width=1.2)), row=aroon_row, col=1)
        fig.add_hline(y=70, line=dict(color=theme.MUTED, dash="dot", width=0.7),
                      row=aroon_row, col=1)
        fig.add_hline(y=30, line=dict(color=theme.MUTED, dash="dot", width=0.7),
                      row=aroon_row, col=1)
        fig.update_yaxes(range=[0, 100], row=aroon_row, col=1, tickvals=[30, 70])

    # ── 볼린저 %b 패널 (20·2σ) — 밴드 내 위치 (0=하단·1=상단) ──
    if bbpct_row:
        b_ma = close.rolling(20).mean()
        b_sd = close.rolling(20).std()
        pctb = (close - (b_ma - 2 * b_sd)) / (4 * b_sd).replace(0, float("nan"))
        fig.add_hrect(y0=0, y1=1, fillcolor="rgba(120,130,180,0.08)", line_width=0,
                      row=bbpct_row, col=1)
        fig.add_trace(go.Scatter(x=hist.index, y=pctb, name="%b(20·2σ)", showlegend=False,
                                 line=dict(color="#22d3ee", width=1.2)), row=bbpct_row, col=1)
        for lv, c in ((1.0, _RED), (0.0, _GREEN)):
            fig.add_hline(y=lv, line=dict(color=c, dash="dot", width=0.7),
                          row=bbpct_row, col=1)
        fig.update_yaxes(row=bbpct_row, col=1, tickvals=[0, 0.5, 1])

    # ── PVT 패널 — 가격 거래량 트렌드 (등락률×거래량 누적 · OBV 계열) ──
    if pvt_row:
        pvt = (close.pct_change().fillna(0) * hist["Volume"]).cumsum()
        fig.add_trace(go.Scatter(x=hist.index, y=pvt, name="PVT", showlegend=False,
                                 line=dict(color="#e879f9", width=1.2)), row=pvt_row, col=1)
        if len(pvt) >= 20:
            fig.add_trace(go.Scatter(x=hist.index, y=pvt.rolling(20).mean(),
                                     name="PVT MA20", showlegend=False,
                                     line=dict(color="#3b82f6", width=1)), row=pvt_row, col=1)
        fig.update_yaxes(row=pvt_row, col=1, nticks=3)

    # ── 펀더멘털 패널 — 분기(연간) 매출 바 + 순이익 라인 (TV img08 갭 · 전유 데이터) ──
    if fund_row:
        import pandas as pd
        f_xs = [pd.Timestamp(r["date"]) for r in fund_rows]
        f_rev = [r.get("revenue") for r in fund_rows]
        f_ni = [r.get("net_income") for r in fund_rows]
        f_hover = []
        for r in fund_rows:
            h = f"{r['date']} · 매출 {fmt_big(r.get('revenue'))} · 순이익 {fmt_big(r.get('net_income'))}"
            if r.get("margin") is not None:
                h += f" · 순마진 {r['margin'] * 100:.1f}%"
            f_hover.append(h)
        if any(v is not None for v in f_rev):
            # 바 폭 명시(간격의 45%·ms) — 자동 폭은 분기 간격 전체를 채워 뭉툭 (실측)
            if len(f_xs) >= 2:
                _bw = min((f_xs[i + 1] - f_xs[i]).total_seconds() * 1000
                          for i in range(len(f_xs) - 1)) * 0.45
            else:
                _bw = 40 * 86400000.0
            fig.add_trace(go.Bar(x=f_xs, y=f_rev, name="매출", showlegend=False,
                                 width=_bw,
                                 marker=dict(color="rgba(47,129,247,0.55)"),
                                 customdata=f_hover,
                                 hovertemplate="%{customdata}<extra></extra>"),
                          row=fund_row, col=1)
        if any(v is not None for v in f_ni):
            ni_colors = [_GREEN if (v or 0) >= 0 else _RED for v in f_ni]
            fig.add_trace(go.Scatter(x=f_xs, y=f_ni, name="순이익", showlegend=False,
                                     mode="lines+markers", customdata=f_hover,
                                     hovertemplate="%{customdata}<extra></extra>",
                                     marker=dict(size=5, color=ni_colors),
                                     line=dict(color="#f59e0b", width=1.3)),
                          row=fund_row, col=1)
        fig.add_hline(y=0, line=dict(color=theme.MUTED, dash="dot", width=0.7),
                      row=fund_row, col=1)
        fig.update_yaxes(row=fund_row, col=1, nticks=3, tickformat="~s")

    chart_height = ({1: 380, 2: 540, 3: 680, 4: 800, 5: 900}.get(panes)
                    or 900 + 85 * (panes - 5))
    fig.update_layout(margin=dict(t=14, b=64, l=14, r=46), dragmode="pan",
                      legend=dict(orientation="h", x=0.0, xanchor="left",
                                  y=1.0, yanchor="bottom", font=dict(size=10),
                                  bgcolor="rgba(0,0,0,0)", itemsizing="constant"),
                      hovermode="x unified", height=chart_height,
                      bargap=0.1, newshape=dict(line=dict(color="#f59e0b", width=2)))
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    # 십자선은 plotly 스파이크 대신 **embed JS DOM 오버레이** (plotly_embed) —
    # 스파이크는 마우스무브마다 전체 재그리기를 유발해 다중 트레이스에서 스터터(성능 회귀 확정)
    if log_scale:                          # 가격 패널만 로그 — 서브패널(RSI·MACD 등)은 선형 유지
        fig.update_yaxes(type="log", row=1 if panes > 1 else None,
                         col=1 if panes > 1 else None)
        _log_fixup_price_shapes(fig)       # 가격축 도형·주석 y → log10 (plotly 규약)
    _t(fig)
    if panes > 1:
        fig.update_xaxes(rangeslider_visible=False)   # 서브플롯 제약 — 팬/줌으로 탐색
        return _initial_view_sub(fig, hist, view_days,
                                  y_override=(cmp_initial_yrange(close, compare, view_days)
                                              if cmp_mode else None), log_scale=log_scale)
    fig.update_layout(xaxis=dict(rangeslider=dict(visible=True, thickness=0.08)))
    return _initial_view(fig, hist, view_days,
                         y_override=(cmp_initial_yrange(close, compare, view_days)
                                     if cmp_mode else None), log_scale=log_scale)


def _add_extremes_and_last(fig, hist, view_days, panes) -> None:
    """표시창 최고/최저 콜아웃(현재가 대비 %·날짜) + 현재가 점선·우측 라벨."""
    import pandas as pd
    cols = set(hist.columns)
    win = hist
    if view_days:
        start = hist.index[-1] - pd.Timedelta(days=view_days)
        w = hist[hist.index >= start]
        if len(w) >= 2:
            win = w
    last = float(hist["Close"].iloc[-1])
    hi_s = win["High"] if "High" in cols else win["Close"]
    lo_s = win["Low"] if "Low" in cols else win["Close"]
    hi_i, lo_i = hi_s.idxmax(), lo_s.idxmin()
    hi_v, lo_v = float(hi_s.max()), float(lo_s.min())
    kw = dict(row=1, col=1) if panes > 1 else {}
    if hi_v > 0:
        fig.add_annotation(name="tn-hi", x=hi_i, y=hi_v,
                           text=f"{hi_v:,.0f} ({last / hi_v - 1:+.1%})",
                           showarrow=True, arrowhead=2, ax=0, ay=-24,
                           font=dict(size=10, color=_RED), arrowcolor=_RED, **kw)
    if lo_v > 0:
        fig.add_annotation(name="tn-lo", x=lo_i, y=lo_v,
                           text=f"{lo_v:,.0f} ({last / lo_v - 1:+.1%})",
                           showarrow=True, arrowhead=2, ax=0, ay=24,
                           font=dict(size=10, color=_BLUE), arrowcolor=_BLUE, **kw)
    prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last
    chip = _GREEN if last >= prev else _RED
    # name=tn-last — ⚡live 클라이언트 패치(plotly_embed patchLast)가 이름으로 찾아
    # 현재가선·라벨을 in-place 이동 (tool-* 아님 → 드로잉 보호·지우기 로직과 무간섭)
    fig.add_hline(y=last, name="tn-last",
                  line=dict(color=chip, dash="dot", width=0.8), **kw)
    fig.add_annotation(name="tn-last", xref="x domain", x=1.0, y=last, xanchor="left",
                       showarrow=False,
                       text=f"<b>{last:,.0f}</b>", font=dict(size=11, color="#ffffff"),
                       bgcolor=chip, borderpad=2, **({"row": 1, "col": 1} if panes > 1 else {}))


def _initial_view_sub(fig, hist, view_days, y_override=None, log_scale: bool = False):
    """RSI 서브플롯용 초기 표시창 — x 공유축 범위만 (y 는 가격 창 기준).

    y_override=(lo, hi) — 비교(%) 모드에서 가격 창 대신 % 프레임 주입.
    log_scale — 로그축이면 가격 y범위를 log10 으로 변환.
    """
    if not view_days or hist is None or getattr(hist, "empty", True):
        return fig
    import pandas as pd
    end = hist.index[-1]
    start = end - pd.Timedelta(days=view_days)
    if start <= hist.index[0]:
        if y_override:
            fig.update_yaxes(range=list(y_override), row=1, col=1)
        return fig
    win = hist[hist.index >= start]
    if len(win) < 2:
        return fig
    fig.update_xaxes(range=[start, end + (end - start) * 0.02])
    if y_override:
        fig.update_yaxes(range=list(y_override), row=1, col=1)
        return fig
    cols = set(hist.columns)
    lo = float(win["Low"].min()) if "Low" in cols else float(win["Close"].min())
    hi = float(win["High"].max()) if "High" in cols else float(win["Close"].max())
    pad = max((hi - lo) * 0.06, hi * 0.002)
    yr = _logr(lo - pad, hi + pad) if log_scale else [lo - pad, hi + pad]
    fig.update_yaxes(range=yr, row=1, col=1)
    return fig


# ── 신규 상단 지표 — 슈퍼트렌드·엔벨로프·윌리엄스 프랙탈·매물대분석 ────────────

def _atr_series(hist, n: int = 10):
    import pandas as pd
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend_series(hist, period: int = 10, mult: float = 3.0):
    """슈퍼트렌드 — (line, trend[±1]) 시리즈. 표준 반복식(밴드 래칫)."""
    import numpy as np
    atr = _atr_series(hist, period).values
    hl2 = ((hist["High"] + hist["Low"]) / 2).values
    close = hist["Close"].values
    n = len(close)
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    fub, flb = ub.copy(), lb.copy()
    trend = np.ones(n, dtype=int)
    line = np.full(n, np.nan)
    for i in range(1, n):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1]) else flb[i - 1]
        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < flb[i] else 1
        else:
            trend[i] = 1 if close[i] > fub[i] else -1
        line[i] = flb[i] if trend[i] == 1 else fub[i]
    return line, trend


def fmt_big(v) -> str:
    """큰 통화 숫자 축약 — 1.23T/45.6B/789M (펀더멘털 hover·라벨용, 순수)."""
    if v is None or v != v:
        return "—"
    a = abs(float(v))
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"{float(v) / div:,.1f}{suf}"
    return f"{float(v):,.0f}"


def kama_series(close, n: int = 10, fast: int = 2, slow: int = 30):
    """카우프만 적응 이동평균 (KAMA) — 효율비(ER) 기반 스무딩, 표준 재귀식 (순수).

    추세 구간(ER→1)은 빠른 EMA, 횡보(ER→0)는 느린 EMA 로 자동 전환 —
    레짐 감지(ml/regime_classifier)와 같은 Kaufman ER 계보의 표시용 오버레이.
    """
    import numpy as np
    import pandas as pd
    c = close.astype(float)
    change = (c - c.shift(n)).abs()
    vol = c.diff().abs().rolling(n).sum()
    er = (change / vol.replace(0, np.nan)).clip(0, 1).fillna(0.0)
    sc = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    vals, scv = c.to_numpy(), sc.to_numpy()
    out = np.full(len(vals), np.nan)
    if len(vals) <= n:
        return pd.Series(out, index=c.index)
    out[n] = vals[n]
    for i in range(n + 1, len(vals)):             # 재귀 정의 — 벡터화 불가
        out[i] = out[i - 1] + scv[i] * (vals[i] - out[i - 1])
    return pd.Series(out, index=c.index)


def fractal_points(hist, k: int = 2):
    """윌리엄스 프랙탈 — (고점 프랙탈 idx, 저점 프랙탈 idx). 5봉(±k) 패턴·확정분만."""
    import numpy as np
    h, l = hist["High"].values, hist["Low"].values
    n = len(h)
    tops, bots = [], []
    for i in range(k, n - k):
        if h[i] == max(h[i - k:i + k + 1]):
            tops.append(i)
        if l[i] == min(l[i - k:i + k + 1]):
            bots.append(i)
    return np.array(tops, dtype=int), np.array(bots, dtype=int)


def volume_profile_bins(hist, bins: int = 40):
    """매물대분석 — 가격대별 거래량 히스토그램 (전 구간·typical price 가중).

    반환 (bin_centers, volumes) | None(Volume 없음).
    """
    import numpy as np
    if "Volume" not in hist.columns:
        return None
    typ = ((hist["High"] + hist["Low"] + hist["Close"]) / 3).values
    vol = hist["Volume"].fillna(0).values
    lo, hi = float(np.nanmin(hist["Low"])), float(np.nanmax(hist["High"]))
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(typ, edges) - 1, 0, bins - 1)
    out = np.zeros(bins)
    np.add.at(out, idx, vol)
    centers = (edges[:-1] + edges[1:]) / 2
    return centers, out


def _add_top_indicators(fig, hist, *, supertrend=False, envelope=False,
                        fractals=False, vol_profile=False, panes=1):
    """상단(가격 패널) 신규 지표 오버레이 — price_chart 내부 전용."""
    go = _go()
    kw = dict(row=1, col=1) if panes > 1 else {}
    if supertrend and len(hist) >= 12:
        line, trend = supertrend_series(hist)
        up = [v if t == 1 else None for v, t in zip(line, trend)]
        dn = [v if t == -1 else None for v, t in zip(line, trend)]
        fig.add_trace(go.Scatter(x=hist.index, y=up, name="슈퍼트렌드",
                                 line=dict(color=_GREEN, width=1.4)), **kw)
        fig.add_trace(go.Scatter(x=hist.index, y=dn, showlegend=False,
                                 legendgroup="슈퍼트렌드",
                                 line=dict(color=_RED, width=1.4)), **kw)
    if envelope and len(hist) >= 20:
        ma = hist["Close"].rolling(20).mean()
        for off, nm, show in ((0.06, "엔벨로프 +6%", True), (-0.06, "엔벨로프 -6%", False)):
            fig.add_trace(go.Scatter(x=hist.index, y=ma * (1 + off),
                                     name="엔벨로프(20,6%)", showlegend=show,
                                     legendgroup="엔벨로프",
                                     line=dict(color="#22d3ee", width=0.9, dash="dot")), **kw)
    if fractals and len(hist) >= 5:
        tops, bots = fractal_points(hist)
        shown = False
        if len(tops):
            fig.add_trace(go.Scatter(
                x=hist.index[tops], y=hist["High"].values[tops] * 1.002, name="프랙탈",
                legendgroup="프랙탈", showlegend=True,
                mode="markers", marker=dict(symbol="triangle-down", size=7, color=_RED)), **kw)
            shown = True
        if len(bots):
            fig.add_trace(go.Scatter(
                x=hist.index[bots], y=hist["Low"].values[bots] * 0.998, name="프랙탈",
                legendgroup="프랙탈", showlegend=not shown,
                mode="markers", marker=dict(symbol="triangle-up", size=7, color=_GREEN)), **kw)
    if vol_profile:
        vp = volume_profile_bins(hist)
        if vp is not None:
            centers, vols = vp
            vmax = float(vols.max()) or 1.0
            fig.add_trace(go.Bar(
                x=vols, y=centers, orientation="h", name="매물대",
                marker=dict(color="rgba(139,147,167,0.30)"), width=(centers[1] - centers[0]),
                xaxis="x9", hovertemplate="%{y:,.0f} 매물 %{x:,.0f}<extra>매물대</extra>"))
            fig.update_layout(xaxis9=dict(overlaying="x", side="top", visible=False,
                                          range=[0, vmax * 4], fixedrange=True))


# ── 신규 상단 지표 2차 — EMA·파라볼릭 SAR·프라이스 채널(돈치안)·VWAP·앵커드 VWAP ──

def parabolic_sar_series(hist, af: float = 0.02, af_step: float = 0.02, af_max: float = 0.2):
    """파라볼릭 SAR — (sar, trend[±1]). 표준 반복식 (Wilder)."""
    import numpy as np
    h, l = hist["High"].values, hist["Low"].values
    n = len(h)
    sar = np.full(n, np.nan)
    trend = np.ones(n, dtype=int)
    if n < 3:
        return sar, trend
    up = h[1] > h[0]
    trend[1] = 1 if up else -1
    sar[1] = l[0] if up else h[0]
    ep = h[1] if up else l[1]
    a = af
    for i in range(2, n):
        sar[i] = sar[i - 1] + a * (ep - sar[i - 1])
        if trend[i - 1] == 1:
            sar[i] = min(sar[i], l[i - 1], l[i - 2])
            if l[i] < sar[i]:                       # 추세 전환 ↓
                trend[i] = -1
                sar[i] = ep
                ep, a = l[i], af
            else:
                trend[i] = 1
                if h[i] > ep:
                    ep, a = h[i], min(a + af_step, af_max)
        else:
            sar[i] = max(sar[i], h[i - 1], h[i - 2])
            if h[i] > sar[i]:                       # 추세 전환 ↑
                trend[i] = 1
                sar[i] = ep
                ep, a = h[i], af
            else:
                trend[i] = -1
                if l[i] < ep:
                    ep, a = l[i], min(a + af_step, af_max)
    return sar, trend


def donchian(hist, n: int = 20):
    """프라이스 채널(돈치안) — (upper, lower, mid)."""
    up = hist["High"].rolling(n).max()
    lo = hist["Low"].rolling(n).min()
    return up, lo, (up + lo) / 2


def session_vwap(hist):
    """세션(일자별 리셋) VWAP — 인트라데이 전용. Volume 없으면 None."""
    if "Volume" not in hist.columns:
        return None
    tp = (hist["High"] + hist["Low"] + hist["Close"]) / 3
    v = hist["Volume"].fillna(0)
    day = hist.index.date
    cum_tv = (tp * v).groupby(day).cumsum()
    cum_v = v.groupby(day).cumsum().replace(0, float("nan"))
    return cum_tv / cum_v


def anchored_vwap(hist, anchor=None):
    """앵커드 VWAP — anchor(Timestamp) 이후 누적. Volume 없으면 None."""
    if "Volume" not in hist.columns:
        return None
    df = hist if anchor is None else hist[hist.index >= anchor]
    if df.empty:
        return None
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    v = df["Volume"].fillna(0)
    cum_v = v.cumsum().replace(0, float("nan"))
    return (tp * v).cumsum() / cum_v


def _add_top_indicators2(fig, hist, *, emas=(), psar=False, donchian_on=False,
                         vwap=False, avwap=False, view_days=None, panes=1):
    """상단 지표 2차 오버레이 — price_chart 내부 전용."""
    go = _go()
    kw = dict(row=1, col=1) if panes > 1 else {}
    close = hist["Close"]
    _EMA_COLORS = {5: "#f0abfc", 10: "#67e8f9", 20: "#fbbf24", 60: "#a78bfa",
                   120: "#6ee7b7", 200: "#fb7185"}
    for win in sorted(set(int(w) for w in (emas or []))):
        if len(close) >= win:
            fig.add_trace(go.Scatter(x=hist.index, y=close.ewm(span=win, adjust=False).mean(),
                                     name=f"EMA{win}",
                                     line=dict(width=1.1, dash="dash",
                                               color=_EMA_COLORS.get(win))), **kw)
    if psar and len(hist) >= 3:
        sar, trend = parabolic_sar_series(hist)
        up = [s if t == 1 else None for s, t in zip(sar, trend)]
        dn = [s if t == -1 else None for s, t in zip(sar, trend)]
        fig.add_trace(go.Scatter(x=hist.index, y=up, name="파라볼릭 SAR", mode="markers",
                                 marker=dict(size=3, color=_GREEN)), **kw)
        fig.add_trace(go.Scatter(x=hist.index, y=dn, showlegend=False, legendgroup="파라볼릭 SAR",
                                 mode="markers", marker=dict(size=3, color=_RED)), **kw)
    if donchian_on and len(hist) >= 20:
        up, lo, mid = donchian(hist)
        fig.add_trace(go.Scatter(x=hist.index, y=up, name="프라이스 채널(20)",
                                 line=dict(color="#8b93a7", width=0.9)), **kw)
        fig.add_trace(go.Scatter(x=hist.index, y=lo, showlegend=False,
                                 legendgroup="프라이스 채널(20)",
                                 line=dict(color="#8b93a7", width=0.9),
                                 fill="tonexty", fillcolor="rgba(139,147,167,0.06)"), **kw)
        fig.add_trace(go.Scatter(x=hist.index, y=mid, showlegend=False,
                                 legendgroup="프라이스 채널(20)",
                                 line=dict(color="#8b93a7", width=0.7, dash="dot")), **kw)
    if vwap:
        sv = session_vwap(hist)
        if sv is not None:
            fig.add_trace(go.Scatter(x=hist.index, y=sv, name="VWAP(세션)",
                                     line=dict(color="#f59e0b", width=1.3)), **kw)
    if avwap:
        anchor = None
        if view_days:
            import pandas as pd
            anchor = hist.index[-1] - pd.Timedelta(days=view_days)
        av = anchored_vwap(hist, anchor)
        if av is not None:
            fig.add_trace(go.Scatter(x=av.index, y=av, name="앵커드 VWAP",
                                     line=dict(color="#e879f9", width=1.3, dash="dashdot")), **kw)


def growth_compare(dates, port_pct, qqq_pct):
    """포트 vs QQQ 성장 곡선 (% 정규화 — 첫 기록=0%). 순수."""
    go = _go()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=port_pct, name="내 포트폴리오",
                             hovertemplate="%{y:+.2f}%<extra>내 포트</extra>",
                             line=dict(color=_BLUE, width=2.2)))
    fig.add_trace(go.Scatter(x=dates, y=qqq_pct, name="QQQ",
                             hovertemplate="%{y:+.2f}%<extra>QQQ</extra>",
                             line=dict(color="#f59e0b", width=1.4, dash="dot")))
    fig.add_hline(y=0, line=dict(color=theme.MUTED, dash="dot", width=0.7))
    fig.update_layout(margin=dict(t=8, b=10, l=10, r=12), height=280,
                      hovermode="x unified",
                      legend=dict(orientation="h", x=0.0, y=1.0, yanchor="bottom",
                                  font=dict(size=10), bgcolor="rgba(0,0,0,0)"))
    fig.update_yaxes(ticksuffix="%", tickformat=".1f")
    return _t(fig)


_LEVEL_STYLE = {"support": (_GREEN, "triangle-up"), "resist": (_RED, "triangle-down"),
                "fair": (_BLUE, "diamond")}


def price_levels(price: float, levels: list, zones: list | None = None):
    """가격 레벨 사다리 — levels=[(행라벨, 가격, kind)] · zones=[(행라벨, lo, hi)] 존 밴드.

    kind: support(녹 ▲)·resist(적 ▼)·fair(청 ◆). 존 = 재료 합류 구간(두꺼운 녹 밴드).
    """
    go = _go()
    fig = go.Figure()
    for row_label, lo, hi in (zones or []):
        if hi > lo:
            fig.add_trace(go.Scatter(
                x=[lo, hi], y=[row_label, row_label], mode="lines",
                line=dict(color=_GREEN, width=12), opacity=0.35, showlegend=False,
                hovertemplate=f"합류 존 {lo:,.2f}~{hi:,.2f}<extra></extra>"))
    seen_kind = set()
    for row_label, v, kind in levels:
        col, sym = _LEVEL_STYLE.get(kind, (_GRID, "circle"))
        fig.add_trace(go.Scatter(
            x=[v], y=[row_label], mode="markers+text",
            text=[f"{v:,.0f}"], textposition="middle right",
            textfont=dict(size=10, color=col),
            marker=dict(color=col, size=11, symbol=sym),
            showlegend=False, hovertemplate=f"{row_label}: %{{x:,.2f}}<extra></extra>"))
        seen_kind.add(kind)
    if price:
        fig.add_vline(x=price, line=dict(color=theme.TEXT, dash="dash", width=1.2),
                      annotation_text=f"현재 {price:,.0f}", annotation_position="top")
    fig.update_layout(margin=dict(t=26, b=10, l=10, r=30),
                      height=max(180, 34 * len({r for r, _, _ in levels}) + 60),
                      xaxis_title=None)
    fig.update_yaxes(automargin=True)
    fig.update_xaxes(automargin=True)
    return _t(fig)
