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


def allocation_donut(holdings: list[dict]):
    """보유 비중 도넛. holdings: [{ticker, value, ...}]."""
    go = _go()
    items = [(h.get("ticker", "?"), h.get("value", 0) or 0) for h in holdings if (h.get("value", 0) or 0) > 0]
    items.sort(key=lambda x: x[1], reverse=True)
    labels = [t for t, _ in items]
    values = [v for _, v in items]
    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.58,
                           textinfo="label+percent", textposition="outside", sort=False))
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300, showlegend=False)
    return _t(fig)


def price_line(hist, ticker: str = ""):
    """가격 라인 + 20/60일 이동평균. hist: OHLC DataFrame(Close 필요)."""
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
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320,
                      legend=dict(orientation="h", y=1.1), hovermode="x unified")
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
                      title=title or None, xaxis_title=None, yaxis_title=None)
    return _t(fig)


def signed_bars(labels: list[str], values: list[float], title: str = ""):
    """부호 막대 (서프라이즈·팩터β 등). +초록 −빨강."""
    go = _go()
    colors = [_GREEN if (v or 0) >= 0 else _RED for v in values]
    fig = go.Figure(go.Bar(x=labels, y=values, marker_color=colors,
                           text=[f"{v:+.1f}" for v in values], textposition="auto"))
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280, title=title or None)
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
