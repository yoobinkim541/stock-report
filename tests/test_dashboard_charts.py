"""dashboard/charts.py 단위 테스트 — 데이터→plotly Figure (네트워크/streamlit 불필요).

charts 는 순수 함수라 from_root cwd 무관. plotly 미설치면 스킵.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("plotly")
import pandas as pd  # noqa: E402

from dashboard import charts  # noqa: E402


def _is_fig(f):
    import plotly.graph_objects as go
    return isinstance(f, go.Figure)


def test_allocation_donut_sorts_and_filters():
    hold = [{"ticker": "A", "value": 100}, {"ticker": "B", "value": 300},
            {"ticker": "Z", "value": 0}]  # 0 은 제외
    fig = charts.allocation_donut(hold)
    assert _is_fig(fig)
    pie = fig.data[0]
    assert list(pie.labels) == ["B", "A"]      # 큰 값 먼저
    assert list(pie.values) == [300, 100]
    assert "Z" not in pie.labels               # 0 필터


def test_allocation_donut_empty():
    fig = charts.allocation_donut([])
    assert _is_fig(fig)
    assert len(fig.data[0].labels) == 0


def test_allocation_donut_labels_not_clipped():
    """바깥 라벨 잘림 방지 — automargin + 넉넉한 높이/여백 (좁은 컬럼서도 안 잘림)."""
    hold = [{"ticker": t, "value": v} for t, v in
            [("SGOV", 22.6), ("MSFT", 12.3), ("SAP", 1.04), ("QQQI", 21.7)]]
    fig = charts.allocation_donut(hold)
    pie = fig.data[0]
    assert pie.automargin is True                 # 라벨 공간 자동 확보(plotly.js)
    assert pie.textposition == "outside"
    assert fig.layout.height >= 360               # 세로 여유
    assert fig.layout.margin.l >= 40 and fig.layout.margin.b >= 30


def test_allocation_donut_hover_carries_company_names():
    # 웨지 라벨은 티커 유지(공간), 호버 customdata 에 회사명
    hold = [{"ticker": "MU", "value": 100, "name": "Micron Technology"},
            {"ticker": "NVDA", "value": 300, "name": "NVIDIA"}]
    pie = charts.allocation_donut(hold).data[0]
    assert list(pie.labels) == ["NVDA", "MU"]            # 티커 라벨
    cd = [str(x) for x in (pie.customdata or [])]
    assert any("NVIDIA" in x for x in cd) and any("Micron" in x for x in cd)
    assert "customdata" in (pie.hovertemplate or "")


def test_price_line_with_ma():
    idx = pd.date_range("2025-01-01", periods=70, freq="D")
    hist = pd.DataFrame({"Close": range(70)}, index=idx)
    fig = charts.price_line(hist, "TST")
    assert _is_fig(fig)
    names = [tr.name for tr in fig.data]
    assert "TST" in names and "MA20" in names and "MA60" in names


def test_price_line_short_history_no_ma():
    hist = pd.DataFrame({"Close": [1, 2, 3]})
    fig = charts.price_line(hist, "X")
    names = [tr.name for tr in fig.data]
    assert "MA20" not in names and "MA60" not in names


def test_price_line_handles_none_and_empty():
    assert _is_fig(charts.price_line(None))
    assert _is_fig(charts.price_line(pd.DataFrame()))


def test_price_line_avg_cost_hline():
    """보유 시 평단 수평선(add_hline) 오버레이 (J2)."""
    idx = pd.date_range("2025-01-01", periods=30, freq="D")
    hist = pd.DataFrame({"Close": range(100, 130)}, index=idx)
    fig = charts.price_line(hist, "NVDA", avg_cost=115.0)
    shapes = fig.layout.shapes or ()
    assert any(getattr(s, "type", None) == "line" for s in shapes), "평단 hline 없음"
    # avg_cost 없으면 hline 없음
    fig2 = charts.price_line(hist, "NVDA")
    assert not (fig2.layout.shapes or ())


def test_price_line_trade_markers_have_click_data():
    idx = pd.date_range("2025-01-01", periods=30, freq="D")
    hist = pd.DataFrame({"Close": range(100, 130)}, index=idx)
    trades = [
        {"event_id": "e1", "date": "2025-01-05", "timestamp": "2025-01-05T09:30:00",
         "ticker": "NVDA", "side": "buy", "qty": 2, "price": 104, "avg_price": 104,
         "account": "manual", "source": "manual_holding", "currency": "USD", "note": "first"},
        {"event_id": "e2", "date": "2025-01-10", "timestamp": "2025-01-10T09:30:00",
         "ticker": "NVDA", "side": "sell", "qty": 1, "price": 109, "avg_price": 104,
         "account": "manual", "source": "manual_holding", "currency": "USD", "note": "trim"},
    ]
    fig = charts.price_line(hist, "NVDA", trades=trades)
    names = [tr.name for tr in fig.data]
    assert "Buy" in names and "Sell" in names
    buy = next(tr for tr in fig.data if tr.name == "Buy")
    assert buy.marker.symbol == "triangle-up"
    assert buy.customdata[0][0] == "e1"
    assert buy.customdata[0][2] == 2
    assert "customdata" in buy.hovertemplate


# ── L2 캔들 차트 ──────────────────────────────────────────────────────
def _ohlc(n=70, start=100):
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": range(start, start + n), "High": range(start + 1, start + n + 1),
                         "Low": range(start - 1, start + n - 1), "Close": range(start, start + n)},
                        index=idx)


def test_price_candle_ohlc_and_ma():
    import plotly.graph_objects as go
    fig = charts.price_candle(_ohlc(), "TST")
    assert any(isinstance(tr, go.Candlestick) for tr in fig.data), "Candlestick trace 없음"
    names = [tr.name for tr in fig.data]
    assert "MA20" in names and "MA60" in names
    assert fig.layout.xaxis.rangeslider.visible is False        # 레인지슬라이더 off


def test_price_candle_avg_cost_hline():
    fig = charts.price_candle(_ohlc(30), "NVDA", avg_cost=115.0)
    assert any(getattr(s, "type", None) == "line" for s in (fig.layout.shapes or ())), "평단 hline 없음"


def test_price_candle_trade_marker_falls_back_to_close():
    hist = _ohlc(30)
    fig = charts.price_candle(hist, "NVDA", trades=[
        {"event_id": "e3", "date": "2025-01-05", "ticker": "NVDA", "side": "buy",
         "qty": 1, "price": None, "avg_price": 104}
    ])
    buy = next(tr for tr in fig.data if tr.name == "Buy")
    assert list(buy.y)[0] == 104  # close on 2025-01-05 from _ohlc start=100


def test_price_candle_handles_missing_ohlc():
    # OHLC 불완전(Close만)·None·빈 → 빈 Figure(예외 없이)
    assert _is_fig(charts.price_candle(pd.DataFrame({"Close": [1, 2, 3]})))
    assert _is_fig(charts.price_candle(None))
    assert _is_fig(charts.price_candle(pd.DataFrame()))


# ── M2 S&P500 시장 맵 트리맵 ──────────────────────────────────────────
_HEAT_ROWS = [
    {"ticker": "AAPL", "name": "Apple", "sector_kr": "기술", "market_cap": 4e12, "pct": 1.96},
    {"ticker": "MSFT", "name": "Microsoft", "sector_kr": "기술", "market_cap": 2.8e12, "pct": 3.17},
    {"ticker": "JPM", "name": "JPMorgan", "sector_kr": "금융", "market_cap": 9e11, "pct": -2.18},
]


def test_market_treemap_sector_grouping():
    import plotly.graph_objects as go
    fig = charts.market_treemap(_HEAT_ROWS)
    assert _is_fig(fig)
    tr = fig.data[0]
    assert isinstance(tr, go.Treemap)
    assert {"기술", "금융", "AAPL", "MSFT", "JPM"} <= set(tr.labels)   # 섹터 루트 + 종목
    idx = list(tr.labels).index("AAPL")
    assert tr.parents[idx] == "기술"                                    # 종목 parent=섹터
    assert tr.marker.cmid == 0 and tr.marker.cmax == 3                  # 발산 색 ±3


def test_market_treemap_clamps_and_empty():
    rows = [{"ticker": "X", "name": "X", "sector_kr": "기술", "market_cap": 1e9, "pct": 9.9}]
    tr = charts.market_treemap(rows).data[0]
    assert tr.marker.colors[list(tr.labels).index("X")] == 3.0         # +9.9%→+3 클램프
    assert _is_fig(charts.market_treemap([]))                          # 빈 → 빈 Figure
    assert len(charts.market_treemap(
        [{"ticker": "Y", "market_cap": 0, "pct": None, "sector_kr": "기술"}]).data) == 0


def test_hbar_orders_ascending():
    fig = charts.hbar(["x", "y", "z"], [0.3, 0.1, 0.6])
    bar = fig.data[0]
    assert list(bar.y) == ["y", "x", "z"]       # 작은→큰 (큰 값이 위)


def test_signed_bars_color_by_sign():
    fig = charts.signed_bars(["a", "b"], [2.0, -1.0])
    colors = list(fig.data[0].marker.color)
    assert colors[0] == charts._GREEN and colors[1] == charts._RED


def test_bars_no_undefined_title_when_empty():
    """제목 미전달 시 title=None 을 넣지 않음 → plotly.js "undefined" 텍스트 방지."""
    for fig in (charts.hbar(["a"], [0.5]), charts.signed_bars(["시장 β", "금리 β"], [0.6, -0.1])):
        assert not (fig.layout.title.text or "")   # 빈 제목


def test_signed_bars_automargin_no_clip():
    """automargin + 바닥 여백 → x축 카테고리 라벨 안 잘림."""
    fig = charts.signed_bars(["시장 β (QQQ)", "금리 β (TLT)"], [0.6, -0.05])
    assert fig.layout.xaxis.automargin is True
    assert fig.layout.margin.b >= 30


def test_value_bullet_has_price_line():
    fig = charts.value_bullet(100, {"low": 80, "mid": 110, "high": 140}, None)
    assert _is_fig(fig)
    assert len(fig.data) >= 1  # RIM 밴드 + 마커


def test_value_bullet_empty_models():
    assert _is_fig(charts.value_bullet(0, None, None))


def test_equity_curve_dataframe_multi_series():
    df = pd.DataFrame({"ML": [1, 1.1, 1.2], "QQQ": [1, 1.05, 1.1]})
    fig = charts.equity_curve(df)
    names = [tr.name for tr in fig.data]
    assert "ML" in names and "QQQ" in names


def test_equity_curve_series():
    fig = charts.equity_curve([1, 1.1, 1.2])
    assert _is_fig(fig)


def test_learning_curve_traces_and_star():
    series = [{"date": "2026-06-01", "excess": 0.01, "ic": 0.02, "adopted": False},
              {"date": "2026-06-08", "excess": 0.03, "ic": 0.06, "adopted": True}]
    fig = charts.learning_curve(series)
    assert _is_fig(fig)
    names = [tr.name for tr in fig.data]
    assert "OOS 초과수익" in names and "순비용 IC" in names and "채택" in names
    star = next(tr for tr in fig.data if tr.name == "채택")
    assert list(star.x) == ["2026-06-08"]        # 채택 주만 마커
    assert star.marker.symbol == "star"


def test_learning_curve_empty_and_none_excess():
    assert _is_fig(charts.learning_curve([]))
    # excess=None 인 행은 제외 → 데이터 없으면 빈 Figure
    assert len(charts.learning_curve([{"date": "d", "excess": None}]).data) == 0


def test_nav_curve_line_and_baseline():
    pts = [{"date": "2026-06-01", "nav": 10_000_000.0},
           {"date": "2026-06-02", "nav": 10_500_000.0}]
    fig = charts.nav_curve(pts, "₩")
    assert _is_fig(fig)
    tr = next(t for t in fig.data if t.name == "NAV")
    assert list(tr.x) == ["2026-06-01", "2026-06-02"]
    assert list(tr.y) == [10_000_000.0, 10_500_000.0]


def test_nav_curve_empty_graceful():
    assert _is_fig(charts.nav_curve([]))
    assert len(charts.nav_curve([{"date": "d", "nav": None}]).data) == 0


def test_intraday_candle_full_overlay():
    import pandas as pd
    idx = pd.date_range("2026-07-08 09:00", periods=20, freq="min", tz="Asia/Seoul")
    hist = pd.DataFrame({"Open": [100.0] * 20, "High": [101.0] * 20,
                         "Low": [99.0] * 20, "Close": [100.5] * 20,
                         "Volume": [10.0] * 20}, index=idx)
    trades = [{"event_id": "intr-x-in", "side": "buy", "qty": 5, "price": 100.5,
               "avg_price": 100.5, "account": "shadow", "source": "intraday_mock",
               "timestamp": idx[10].isoformat(), "currency": "KRW"}]
    fig = charts.intraday_candle(hist, "005930", trades=trades, vwap=[100.2] * 20,
                                 or_range=(101.0, 99.0, idx[14]),
                                 levels=[{"y": 99.5, "label": "스톱", "color": "#ef4444"}])
    assert _is_fig(fig)
    names = [tr.name for tr in fig.data]
    assert "VWAP" in names and "Buy" in names            # 오버레이 + ▲ 마커
    buy = next(tr for tr in fig.data if tr.name == "Buy")
    assert buy.customdata[0][0] == "intr-x-in"           # 클릭 → event_id 계약
    # OR 박스(rect) 1 + 스톱 hline(line) 1
    assert sum(1 for s in fig.layout.shapes if s.type == "rect") == 1
    assert sum(1 for s in fig.layout.shapes if s.type == "line") == 1


def test_intraday_candle_empty_graceful():
    import pandas as pd
    assert _is_fig(charts.intraday_candle(pd.DataFrame()))
    assert len(charts.intraday_candle(pd.DataFrame()).data) == 0
