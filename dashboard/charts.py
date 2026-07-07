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


def price_line(hist, ticker: str = "", avg_cost=None, trades=None):
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
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320,
                      legend=dict(orientation="h", y=1.1), hovermode="x unified")
    return _t(fig)


def price_candle(hist, ticker: str = "", avg_cost=None, trades=None):
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
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320,
                      legend=dict(orientation="h", y=1.1),
                      hovermode="x unified",
                      xaxis_rangeslider_visible=False)
    return _t(fig)


def market_treemap(rows: list[dict], height: int = 560):
    """S&P500 섹터 시장 맵 (Finviz 풍). rows:[{ticker,name,sector_kr,market_cap,pct}].

    섹터→종목 2계층 트리맵. 타일 크기=시총, 색=당일 등락%(적→흑→녹·±3 클램프).
    """
    go = _go()
    rows = [r for r in (rows or []) if (r.get("market_cap") or 0) > 0 and r.get("pct") is not None]
    if not rows:
        return _t(go.Figure())
    from collections import defaultdict
    sec_sum: dict[str, float] = defaultdict(float)
    for r in rows:
        sec_sum[r["sector_kr"]] += float(r["market_cap"])
    labels, parents, values, colors, texts, custom = [], [], [], [], [], []
    for s in sorted(sec_sum):                       # 섹터 루트노드 (값=자식 시총합)
        labels.append(s); parents.append(""); values.append(sec_sum[s])
        colors.append(0.0); texts.append(f"<b>{s}</b>"); custom.append("")
    for r in rows:
        labels.append(r["ticker"]); parents.append(r["sector_kr"])
        values.append(float(r["market_cap"]))
        colors.append(max(-3.0, min(3.0, float(r["pct"]))))   # ±3 클램프(대비)
        texts.append(f'{r["ticker"]}<br>{r["pct"]:+.2f}%')
        custom.append(r.get("name") or r["ticker"])
    fig = go.Figure(go.Treemap(
        labels=labels, parents=parents, values=values, branchvalues="total",
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
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=360,
                      legend=dict(orientation="h", y=1.1), hovermode="x unified",
                      xaxis_rangeslider_visible=False)
    return _t(fig)
