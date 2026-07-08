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


def _pannable(fig, *, rangeslider: bool = True, height: int = 360):
    """가격 차트 공통 내비게이션 — pan 드래그 + 하단 레인지슬라이더(과거 탐색)."""
    fig.update_layout(dragmode="pan", height=height,
                      xaxis=dict(rangeslider=dict(visible=rangeslider, thickness=0.08)))
    return fig


def _initial_view(fig, hist, view_days, *, lo_col="Low", hi_col="High"):
    """전체 히스토리 로드 상태에서 초기 화면만 최근 view_days 로 — 과거는 드래그/미니차트 탐색.

    x·y 초기 범위를 창에 맞춤(plotly 는 x창 추종 y 자동스케일이 없어 창 기준으로 시작 —
    팬/줌으로 조절·더블클릭=전체 복귀). 데이터가 창보다 짧으면 전체 표시.
    """
    if not view_days or hist is None or getattr(hist, "empty", True):
        return fig
    import pandas as pd
    end = hist.index[-1]
    start = end - pd.Timedelta(days=view_days)
    if start <= hist.index[0]:
        return fig                                   # 창보다 짧은 이력 — 전체 그대로
    win = hist[hist.index >= start]
    if len(win) < 2:
        return fig
    cols = set(getattr(hist, "columns", []))
    lo = float(win[lo_col].min()) if lo_col in cols else float(win["Close"].min())
    hi = float(win[hi_col].max()) if hi_col in cols else float(win["Close"].max())
    pad = max((hi - lo) * 0.06, hi * 0.002)
    fig.update_layout(xaxis_range=[start, end + (end - start) * 0.02],
                      yaxis_range=[lo - pad, hi + pad])
    return fig


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


def hbar(labels: list[str], values: list[float], title: str = "", pct: bool = True):
    """가로 막대 (위험기여·비중 등). 큰 값이 위로."""
    go = _go()
    pairs = sorted(zip(labels, values), key=lambda x: x[1])
    fig = go.Figure(go.Bar(
        x=[v * 100 if pct else v for _, v in pairs],
        y=[l for l, _ in pairs], orientation="h", marker_color=_BLUE,
        text=[f"{v*100:.0f}%" if pct else f"{v:.2f}" for _, v in pairs], textposition="auto"))
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=max(180, 36 * len(pairs)),
                      xaxis_title=None, yaxis_title=None)
    if title:                       # None 제목은 plotly.js 가 "undefined" 로 렌더 → 비면 미설정
        fig.update_layout(title=title)
    return _t(fig)


def signed_bars(labels: list[str], values: list[float], title: str = ""):
    """부호 막대 (서프라이즈·팩터β 등). +초록 −빨강."""
    go = _go()
    colors = [_GREEN if (v or 0) >= 0 else _RED for v in values]
    fig = go.Figure(go.Bar(x=labels, y=values, marker_color=colors,
                           text=[f"{v:+.1f}" for v in values], textposition="auto"))
    # automargin + 여백 → x축 카테고리 라벨·바닥 값 안 잘림 (도넛 선례)
    fig.update_layout(margin=dict(t=14, b=44, l=14, r=14), height=300)
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    if title:
        fig.update_layout(title=title)
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


def price_chart(hist, ticker: str = "", *, kind: str = "line", avg_cost=None,
                trades=None, view_days=None, mas=(60, 120, 200),
                show_rsi: bool = False, bollinger: bool = False, ichimoku: bool = False):
    """가격 차트 + 기술적 분석 도구 — MA 세트·볼린저밴드·일목균형표·RSI 하단 패널.

    show_rsi=True 면 2행 서브플롯(가격 75%·RSI 25%, x 공유 — 레인지슬라이더는 비활성:
    plotly 서브플롯 제약, 팬/줌은 유지). 지표는 범례 클릭으로 개별 토글 가능.
    """
    go = _go()
    cols = set(getattr(hist, "columns", []))
    if hist is None or getattr(hist, "empty", True) or "Close" not in cols:
        return _t(go.Figure())
    close = hist["Close"]
    has_ohlc = {"Open", "High", "Low"} <= cols

    if show_rsi:
        from plotly.subplots import make_subplots
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.76, 0.24], vertical_spacing=0.04)
    else:
        fig = go.Figure()

    # ── 메인: 가격 ──
    if kind == "candle" and has_ohlc:
        fig.add_trace(go.Candlestick(
            x=hist.index, open=hist["Open"], high=hist["High"], low=hist["Low"],
            close=close, name=ticker or "OHLC",
            increasing_line_color=_GREEN, decreasing_line_color=_RED,
            increasing_fillcolor=_GREEN, decreasing_fillcolor=_RED, line=dict(width=1)))
    else:
        fig.add_trace(go.Scatter(x=hist.index, y=close, name=ticker or "종가",
                                 line=dict(color=_BLUE, width=2)))

    # ── 일목균형표 (구름은 MA 아래 깔리게 먼저) ──
    if ichimoku and has_ohlc and len(close) >= 52:
        h9 = (hist["High"].rolling(9).max() + hist["Low"].rolling(9).min()) / 2      # 전환선
        h26 = (hist["High"].rolling(26).max() + hist["Low"].rolling(26).min()) / 2   # 기준선
        spa = ((h9 + h26) / 2).shift(26)                                             # 선행스팬A
        spb = ((hist["High"].rolling(52).max()
                + hist["Low"].rolling(52).min()) / 2).shift(26)                      # 선행스팬B
        fig.add_trace(go.Scatter(x=hist.index, y=spa, name="선행A",
                                 line=dict(color="#26a69a", width=0.8), opacity=0.6))
        fig.add_trace(go.Scatter(x=hist.index, y=spb, name="선행B(구름)",
                                 line=dict(color="#ef5350", width=0.8), opacity=0.6,
                                 fill="tonexty", fillcolor="rgba(120,140,180,0.12)"))
        fig.add_trace(go.Scatter(x=hist.index, y=h9, name="전환선(9)",
                                 line=dict(color="#22d3ee", width=1)))
        fig.add_trace(go.Scatter(x=hist.index, y=h26, name="기준선(26)",
                                 line=dict(color="#f59e0b", width=1)))
        fig.add_trace(go.Scatter(x=hist.index, y=close.shift(-26), name="후행스팬",
                                 line=dict(color="#e879f9", width=0.8, dash="dot")))

    # ── 이동평균 세트 ──
    for win in sorted(set(int(w) for w in (mas or []))):
        if len(close) >= win:
            fig.add_trace(go.Scatter(
                x=hist.index, y=close.rolling(win).mean(), name=f"MA{win}",
                line=dict(width=1.1, color=_MA_COLORS.get(win))))

    # ── 볼린저밴드 (20, ±2σ) ──
    if bollinger and len(close) >= 20:
        ma20 = close.rolling(20).mean()
        sd = close.rolling(20).std()
        fig.add_trace(go.Scatter(x=hist.index, y=ma20 + 2 * sd, name="BB상단",
                                 line=dict(color="#8b93a7", width=0.8, dash="dot")))
        fig.add_trace(go.Scatter(x=hist.index, y=ma20 - 2 * sd, name="BB하단",
                                 line=dict(color="#8b93a7", width=0.8, dash="dot"),
                                 fill="tonexty", fillcolor="rgba(139,147,167,0.08)"))

    if avg_cost and avg_cost > 0:
        fig.add_hline(y=avg_cost, line=dict(color=theme.MUTED, dash="dash", width=1.2),
                      annotation_text=f"평단 {avg_cost:,.2f}", annotation_position="top left",
                      annotation_font=dict(color=theme.MUTED, size=11),
                      row=1 if show_rsi else None, col=1 if show_rsi else None)
    _add_trade_markers(fig, hist, trades or [])

    # ── RSI 하단 패널 ──
    if show_rsi:
        rsi = _rsi_series(close)
        fig.add_trace(go.Scatter(x=hist.index, y=rsi, name="RSI(14)",
                                 line=dict(color="#f59e0b", width=1.3)), row=2, col=1)
        for lv, c in ((70, _RED), (30, _GREEN)):
            fig.add_hline(y=lv, line=dict(color=c, dash="dot", width=0.8), row=2, col=1)
        fig.update_yaxes(range=[0, 100], row=2, col=1, tickvals=[30, 50, 70])

    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), dragmode="pan",
                      legend=dict(orientation="h", y=1.08, font=dict(size=10)),
                      hovermode="x unified", height=470 if show_rsi else 380)
    _t(fig)
    if show_rsi:
        fig.update_xaxes(rangeslider_visible=False)   # 서브플롯 제약 — 팬/줌으로 탐색
    else:
        fig.update_layout(xaxis=dict(rangeslider=dict(visible=True, thickness=0.08)))
    return _initial_view(fig, hist, view_days) if not show_rsi else _initial_view_sub(fig, hist, view_days)


def _initial_view_sub(fig, hist, view_days):
    """RSI 서브플롯용 초기 표시창 — x 공유축 범위만 (y 는 가격 창 기준)."""
    if not view_days or hist is None or getattr(hist, "empty", True):
        return fig
    import pandas as pd
    end = hist.index[-1]
    start = end - pd.Timedelta(days=view_days)
    if start <= hist.index[0]:
        return fig
    win = hist[hist.index >= start]
    if len(win) < 2:
        return fig
    cols = set(hist.columns)
    lo = float(win["Low"].min()) if "Low" in cols else float(win["Close"].min())
    hi = float(win["High"].max()) if "High" in cols else float(win["Close"].max())
    pad = max((hi - lo) * 0.06, hi * 0.002)
    fig.update_xaxes(range=[start, end + (end - start) * 0.02])
    fig.update_yaxes(range=[lo - pad, hi + pad], row=1, col=1)
    return fig
