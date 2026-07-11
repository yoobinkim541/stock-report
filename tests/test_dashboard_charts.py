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
    assert fig.layout.xaxis.rangeslider.visible is True         # 과거 탐색 레인지슬라이더 (내비 개편)


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
    assert tr.parents[idx] == "sec:기술"                                # 종목 parent=섹터 id
    assert tr.ids[idx] == "t:AAPL"                                      # 종목 label=티커(클릭 계약)
    assert tr.marker.cmid == 0 and tr.marker.cmax == 3                  # 발산 색 ±3


def test_market_treemap_tech_subcategories():
    """기술 섹터 3계층 — sub(반도체 등) 중간 노드, 종목 parent=sub id."""
    rows = [
        {"ticker": "NVDA", "name": "NVIDIA", "sector_kr": "기술", "sub": "반도체",
         "market_cap": 3e12, "pct": 2.0},
        {"ticker": "MSFT", "name": "Microsoft", "sector_kr": "기술", "sub": "소프트웨어·클라우드",
         "market_cap": 2.8e12, "pct": 1.0},
        {"ticker": "JPM", "name": "JPMorgan", "sector_kr": "금융", "market_cap": 9e11, "pct": -1.0},
    ]
    tr = charts.market_treemap(rows).data[0]
    ids = list(tr.ids)
    assert "sub:기술/반도체" in ids and "sub:기술/소프트웨어·클라우드" in ids
    assert tr.parents[ids.index("sub:기술/반도체")] == "sec:기술"       # sub → 섹터
    assert tr.parents[ids.index("t:NVDA")] == "sub:기술/반도체"         # 종목 → sub
    assert tr.parents[ids.index("t:JPM")] == "sec:금융"                 # sub 없는 섹터는 2계층 유지
    # branchvalues=total 정합: sub 값 = 자식 시총합
    assert tr.values[ids.index("sub:기술/반도체")] == 3e12


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


def test_price_charts_pannable_navigation():
    """가격 차트 3종 — pan 드래그 + 레인지슬라이더(과거 탐색) 계약."""
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    hist = pd.DataFrame({"Open": [100.0] * 30, "High": [101.0] * 30,
                         "Low": [99.0] * 30, "Close": [100.5] * 30,
                         "Volume": [10.0] * 30}, index=idx)
    for fig in (charts.price_line(hist, "T"), charts.price_candle(hist, "T"),
                charts.intraday_candle(hist, "T")):
        assert fig.layout.dragmode == "pan"
        assert fig.layout.xaxis.rangeslider.visible is True
    assert charts.PAN_CFG["scrollZoom"] is True           # 휠 확대/축소
    assert "select2d" in charts.PAN_CFG["modeBarButtonsToRemove"]  # 마커 클릭과 간섭 제거


def test_analyst_ratings_highlights_dominant_bucket():
    fig = charts.analyst_ratings({"strong_sell": 1, "sell": 2, "hold": 4, "buy": 12, "strong_buy": 8})
    assert _is_fig(fig)
    bar = fig.data[0]
    assert list(bar.x) == ["적극 매도", "매도", "중립", "매수", "적극 매수"]
    assert list(bar.y) == [1, 2, 4, 12, 8]
    assert list(bar.marker.color)[3] == charts._GREEN


def test_analyst_ratings_empty_graceful():
    assert _is_fig(charts.analyst_ratings({}))
    assert len(charts.analyst_ratings({}).data) == 0


def test_target_price_fan_projects_targets_and_handles_empty_close():
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    hist = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=idx)
    fig = charts.target_price_fan(hist, 100.0, 130.0, 120.0, 90.0)
    assert _is_fig(fig)
    assert [tr.name for tr in fig.data] == ["주가", "최고", "평균", "최저"]
    assert len(fig.layout.annotations) == 4       # 목표가 3개 + 현재가 hline

    empty_close = pd.DataFrame({"Close": [None, None]}, index=idx[:2])
    assert _is_fig(charts.target_price_fan(empty_close, 100.0, 120.0, 110.0, 90.0))


def test_target_price_fan_requires_mean_and_price():
    assert len(charts.target_price_fan(None, 100.0, 120.0, None, 90.0).data) == 0
    assert len(charts.target_price_fan(None, None, 120.0, 110.0, 90.0).data) == 0


def test_initial_view_window_full_history():
    """전체 히스토리 로드 + 초기 표시창(view_days) — 과거 드래그용 데이터는 전부 유지."""
    import pandas as pd
    idx = pd.date_range("2016-01-01", periods=2500, freq="D")   # ~7년
    hist = pd.DataFrame({"Open": [100.0] * 2500, "High": [200.0] * 2300 + [110.0] * 200,
                         "Low": [90.0] * 2500, "Close": [100.0] * 2500,
                         "Volume": [1.0] * 2500}, index=idx)
    fig = charts.price_candle(hist, "T", view_days=180)
    assert len(fig.data[0].x) == 2500                            # 데이터는 전체 보존
    x0, x1 = fig.layout.xaxis.range
    assert pd.Timestamp(x0) >= idx[0] and pd.Timestamp(x1) >= idx[-1]
    assert (pd.Timestamp(x1) - pd.Timestamp(x0)).days <= 200     # 초기 창 ≈ 6개월
    assert fig.layout.yaxis.range[1] < 150                       # y 는 창 데이터(110) 기준 — 과거 고점(200) 아님
    # view_days 없으면 전체 표시(범위 미설정) · 창보다 짧은 이력도 전체
    assert charts.price_line(hist, "T").layout.xaxis.range is None
    assert charts.price_line(hist.iloc[:100], "T", view_days=365).layout.xaxis.range is None


def test_analyst_ratings_distribution():
    fig = charts.analyst_ratings({"strong_sell": 0, "sell": 0, "hold": 1, "buy": 23, "strong_buy": 1})
    assert _is_fig(fig)
    bar = fig.data[0]
    assert list(bar.y) == [0, 0, 1, 23, 1]
    assert list(bar.text) == ["0명", "0명", "1명", "23명", "1명"]
    assert bar.marker.color[3] == charts._GREEN          # 최다 카테고리 강조
    assert bar.marker.color[2] != charts._GREEN          # 나머지 딤
    assert len(charts.analyst_ratings({}).data) == 0     # 빈 분포 graceful


def test_target_price_fan_projection():
    import pandas as pd
    idx = pd.date_range("2025-07-08", periods=250, freq="D")
    hist = pd.DataFrame({"Close": [2_000_000.0 + i * 1000 for i in range(250)]}, index=idx)
    fig = charts.target_price_fan(hist, 2_259_000, 4_300_000, 3_547_916, 1_750_000, "₩")
    assert _is_fig(fig)
    assert len(fig.data) == 4                             # 주가 + 최고/평균/최저 점선
    anns = " ".join(a.text for a in fig.layout.annotations)
    assert "최고" in anns and "평균" in anns and "최저" in anns
    assert "+57.1%" in anns and "+90.3%" in anns and "-22.5%" in anns
    assert "₩3,547,916" in anns                           # KR 통화 포맷
    # 평균 없으면 빈 Figure · 이력 없이도 투영만으로 렌더
    assert len(charts.target_price_fan(hist, 100.0, None, None, None).data) == 0
    assert len(charts.target_price_fan(None, 100.0, 120.0, 110.0, 90.0).data) == 3


def test_price_chart_indicators_composite():
    """기술적 분석 합성 — MA 세트·RSI 하단 패널·볼린저·일목균형표 토글."""
    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    hist = pd.DataFrame({"Open": [100.0 + i * 0.1 for i in range(300)],
                         "High": [101.0 + i * 0.1 for i in range(300)],
                         "Low": [99.0 + i * 0.1 for i in range(300)],
                         "Close": [100.5 + i * 0.1 for i in range(300)],
                         "Volume": [10.0] * 300}, index=idx)
    fig = charts.price_chart(hist, "T", kind="candle", mas=[60, 120, 200],
                             show_rsi=True, bollinger=True, ichimoku=True)
    names = [tr.name for tr in fig.data]
    assert {"MA60", "MA120", "MA200"} <= set(names)
    assert "RSI(14)" in names and "BB상단" in names and "전환선(9)" in names
    rsi_tr = next(tr for tr in fig.data if tr.name == "RSI(14)")
    assert rsi_tr.yaxis == "y2"                                # 하단 서브패널
    assert fig.layout.yaxis2.range == (0, 100)
    assert fig.layout.height >= 540 and fig.layout.margin.b >= 64
    assert fig.layout.yaxis2.domain[1] - fig.layout.yaxis2.domain[0] >= 0.27
    # 전부 끄면 가격 트레이스만
    fig2 = charts.price_chart(hist, "T", kind="line", mas=[], show_rsi=False)
    assert [tr.name for tr in fig2.data] == ["T"]
    assert fig2.layout.xaxis.rangeslider.visible is True       # RSI 없으면 슬라이더 유지
    # 이력 부족 MA 는 침묵 스킵
    fig3 = charts.price_chart(hist.iloc[:50], "T", mas=[200])
    assert "MA200" not in [tr.name for tr in fig3.data]


def test_price_chart_trend_lines_overlay():
    """추세선·채널 오버레이 — 지지/저항 대시 선분·채널 상하단 fill·annotation·드로잉 스타일."""
    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=100, freq="D")
    hist = pd.DataFrame({"Open": [100.0] * 100, "High": [101.0] * 100,
                         "Low": [99.0] * 100, "Close": [100.0] * 100}, index=idx)
    tls = [
        {"kind": "support", "label": "지지선 (3터치)", "x0": idx[10], "x1": idx[-1],
         "y0": 98.0, "y1": 100.0, "upper": None, "lower": None, "path": None,
         "touches": 3, "meta": {"trend": None}},
        {"kind": "channel", "label": "단기 상승채널(60)", "x0": idx[40], "x1": idx[-1],
         "y0": 99.0, "y1": 101.0, "upper": (100.0, 102.0), "lower": (98.0, 100.0),
         "path": None, "touches": 0, "meta": {"trend": "up"}},
    ]
    fig = charts.price_chart(hist, "T", trend_lines=tls)
    names = [tr.name for tr in fig.data]
    assert "지지선 (3터치)" in names and "단기 상승채널(60)" in names
    ch_lower = [tr for tr in fig.data if tr.legendgroup == "단기 상승채널(60)" and tr.fill == "tonexty"]
    assert ch_lower, "채널 fill 없음"
    anns = " ".join(a.text for a in fig.layout.annotations)
    assert "지지선" in anns and "상승채널" in anns
    tl_anns = [a for a in fig.layout.annotations if a.text in ("지지선 (3터치)", "단기 상승채널(60)")]
    assert tl_anns and all(a.bgcolor for a in tl_anns)      # 선과 글씨가 겹치지 않도록 칩 배경
    assert all(a.borderpad >= 4 and a.xshift >= 10 for a in tl_anns)
    assert all(a.yshift > 0 for a in tl_anns)
    assert fig.layout.newshape.line.color == "#f59e0b"      # 수동 드로잉 기본 스타일
    assert "drawline" in charts.PAN_DRAW_CFG["modeBarButtonsToAdd"]
    # 빈 입력 무변화
    fig2 = charts.price_chart(hist, "T", trend_lines=[])
    assert len(fig2.data) < len(fig.data)


def test_price_chart_three_pane_layout():
    """가격/거래량/RSI 3패널 — 축 배치·거래량 방향색·RSI 시그널·최고최저 콜아웃·현재가 칩."""
    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=120, freq="D")
    close = [100.0 + (i % 7) for i in range(120)]
    hist = pd.DataFrame({"Open": [c - 0.5 for c in close],
                         "High": [c + 1 for c in close], "Low": [c - 1 for c in close],
                         "Close": close, "Volume": [10.0 + i for i in range(120)]}, index=idx)
    fig = charts.price_chart(hist, "T", kind="candle", show_rsi=True, show_volume=True)
    by_name = {tr.name: tr for tr in fig.data}
    assert by_name["거래량"].yaxis == "y2" and by_name["거래량 MA20"].yaxis == "y2"
    assert by_name["RSI(14)"].yaxis == "y3" and by_name["RSI 시그널(14)"].yaxis == "y3"
    assert fig.layout.yaxis3.range == (0, 100)
    assert fig.layout.height >= 680 and fig.layout.margin.b >= 64
    assert fig.layout.xaxis.automargin is True and fig.layout.yaxis3.automargin is True
    assert fig.layout.yaxis3.domain[1] - fig.layout.yaxis3.domain[0] >= 0.20
    anns = " ".join(a.text for a in fig.layout.annotations)
    assert "+" in anns and "-" in anns and "<b>" in anns       # 최고/최저 % + 현재가 칩
    # 거래량만 (RSI off) → 거래량 y2
    fig2 = charts.price_chart(hist, "T", show_volume=True)
    assert {tr.name: tr for tr in fig2.data}["거래량"].yaxis == "y2"
    # Volume 컬럼 없으면 침묵 스킵
    fig3 = charts.price_chart(hist.drop(columns=["Volume"]), "T", show_volume=True)
    assert "거래량" not in [tr.name for tr in fig3.data]


def test_price_chart_new_top_indicators():
    """슈퍼트렌드·엔벨로프·프랙탈·매물대 — 트레이스 존재·토글 off 시 부재·V자 전환."""
    import numpy as np
    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=160, freq="D")
    close = np.concatenate([np.linspace(100, 70, 80), np.linspace(70, 105, 80)])  # V자
    hist = pd.DataFrame({"Open": close, "High": close + 1.5, "Low": close - 1.5,
                         "Close": close, "Volume": np.full(160, 100.0)}, index=idx)
    fig = charts.price_chart(hist, "T", kind="candle", mas=[],
                             supertrend=True, envelope=True, fractals=True, vol_profile=True)
    names = [tr.name for tr in fig.data]
    assert "슈퍼트렌드" in names and "엔벨로프(20,6%)" in names
    assert "프랙탈" in names and "매물대" in names
    vp = next(tr for tr in fig.data if tr.name == "매물대")
    assert vp.xaxis == "x9" and vp.orientation == "h"          # 오버레이 히스토그램
    assert fig.layout.xaxis9.visible is False
    # 엔벨로프 ±6% 정합
    env = [tr for tr in fig.data if tr.legendgroup == "엔벨로프"]
    ma20 = hist["Close"].rolling(20).mean().iloc[-1]
    ys = sorted(t.y[-1] for t in env)
    assert ys[0] == pytest.approx(ma20 * 0.94) and ys[1] == pytest.approx(ma20 * 1.06)
    # V자 → 슈퍼트렌드 추세 전환 존재
    line, trend = charts.supertrend_series(hist)
    assert (np.diff(trend) != 0).any()
    # 전부 off → 신규 지표 트레이스 없음
    fig2 = charts.price_chart(hist, "T", mas=[])
    assert not ({"슈퍼트렌드", "매물대", "프랙탈"} & set(tr.name for tr in fig2.data))


def test_price_chart_second_wave_indicators():
    """EMA·파라볼릭 SAR·프라이스 채널·세션 VWAP·앵커드 VWAP — 수식·트레이스 계약."""
    import numpy as np
    import pandas as pd
    idx = pd.date_range("2026-07-06 09:00", periods=120, freq="5min", tz="Asia/Seoul")
    close = 100 + np.cumsum(np.random.default_rng(3).normal(0, 0.4, 120))
    hist = pd.DataFrame({"Open": close, "High": close + 0.6, "Low": close - 0.6,
                         "Close": close, "Volume": np.full(120, 50.0)}, index=idx)
    fig = charts.price_chart(hist, "T", mas=[20], emas=[20], psar=True,
                             donchian_on=True, vwap=True, avwap=True, view_days=1)
    names = [tr.name for tr in fig.data]
    for n in ("EMA20", "파라볼릭 SAR", "프라이스 채널(20)", "VWAP(세션)", "앵커드 VWAP"):
        assert n in names, f"{n} 없음"
    # EMA ≠ SMA (다른 수식)
    ema = next(tr for tr in fig.data if tr.name == "EMA20")
    sma = next(tr for tr in fig.data if tr.name == "MA20")
    assert ema.y[-1] != sma.y[-1]
    # SAR 추세 전환 존재 + 돈치안 상단≥하단
    sar, trend = charts.parabolic_sar_series(hist)
    assert (np.diff(trend[2:]) != 0).any()
    up, lo, _ = charts.donchian(hist)
    assert (up.dropna() >= lo.dropna()).all()
    # 세션 VWAP 일자 리셋 — 두 세션 경계에서 누적 초기화
    two_day = pd.date_range("2026-07-06 09:00", periods=80, freq="5min", tz="Asia/Seoul")
    two_day = two_day[:40].append(pd.date_range("2026-07-07 09:00", periods=40,
                                                freq="5min", tz="Asia/Seoul"))
    h2 = hist.iloc[:80].set_axis(two_day)
    sv = charts.session_vwap(h2)
    d2_first = sv.iloc[40]
    tp_first = float((h2["High"].iloc[40] + h2["Low"].iloc[40] + h2["Close"].iloc[40]) / 3)
    assert d2_first == pytest.approx(tp_first)          # 새 세션 첫 봉 = 그 봉 tp (리셋)


# ── I3 종목 비교 오버레이 (% 상대수익) ────────────────────────────────
def test_normalize_pct_anchor_zero():
    """정규화 시작점 = 표시창 시작 봉 0% (앵커 이전 구간도 같은 앵커로 연속)."""
    idx = pd.date_range("2025-01-01", periods=100, freq="D")
    s = pd.Series(range(100, 200), index=idx, dtype=float)
    n = charts.normalize_pct(s, view_days=30)
    anchor_ts = idx[-1] - pd.Timedelta(days=30)
    first_in_win = n[n.index >= anchor_ts].iloc[0]
    assert abs(first_in_win) < 1e-9                    # 창 시작 = 0%
    assert n.iloc[0] < 0                                # 앵커 이전(더 쌈) → 음수 %
    n_all = charts.normalize_pct(s)                     # view_days 없음 → 첫 봉 앵커
    assert abs(n_all.iloc[0]) < 1e-9
    assert abs(n_all.iloc[-1] - 99.0) < 1e-9            # 100→199 = +99%


def test_price_chart_compare_mode():
    """비교 모드 — 정규화 라인 2+, 가격절대 오버레이(캔들·평단·MA) 자동 숨김·% 축."""
    import plotly.graph_objects as go
    hist = _ohlc(80)
    cmp_s = pd.Series([50.0 + i * 2 for i in range(80)], index=hist.index)
    fig = charts.price_chart(hist, "MAIN", kind="candle", avg_cost=115.0,
                             mas=(20,), compare={"CMP (X)": cmp_s})
    assert not any(isinstance(tr, go.Candlestick) for tr in fig.data)   # 캔들 강제 해제
    names = [tr.name for tr in fig.data]
    assert "MAIN" in names and "CMP (X)" in names       # 두 시리즈 존재
    assert not any(n and n.startswith("MA") and n != "MAIN" for n in names)  # MA 숨김
    hlines = [s for s in fig.layout.shapes if s.type == "line" and s.y0 == s.y1]
    assert all(s.y0 == 0 for s in hlines)               # 평단 hline 없음·0% 기준선만
    assert fig.layout.yaxis.ticksuffix == "%"
    main_tr = next(tr for tr in fig.data if tr.name == "MAIN")
    assert abs(main_tr.y[0]) < 1e-9                     # 시작 0%
    assert not [a for a in fig.layout.annotations or [] if a.name in ("tn-hi", "tn-lo")]


def test_price_chart_compare_empty_series_ignored():
    """비교 시리즈가 전부 무효(None·짧음)면 일반 모드 유지."""
    import plotly.graph_objects as go
    hist = _ohlc(60)
    short = pd.Series([1.0], index=hist.index[:1])
    fig = charts.price_chart(hist, "M", kind="candle",
                             compare={"a": None, "b": short})
    assert any(isinstance(tr, go.Candlestick) for tr in fig.data)   # 캔들 유지 = 일반 모드


def test_price_chart_legend_decluttered():
    """범례 정리 — 거래량·RSI·Buy/Sell 은 범례 제외, 종가·MA 만 노출 (UI 소음 제거)."""
    hist = _ohlc(80)
    hist["Volume"] = 1000.0
    trades = [{"event_id": "e1", "side": "buy", "qty": 1, "price": 110.0,
               "date": str(hist.index[10].date())}]
    fig = charts.price_chart(hist, "T", mas=(20,), show_rsi=True, show_volume=True,
                             trades=trades)
    shown = {tr.name for tr in fig.data if tr.showlegend is not False}
    assert shown == {"T", "MA20"}                       # 나머지 전부 범례 숨김
    assert fig.layout.legend.xanchor == "left"          # 좌상단 밀착
    assert fig.layout.margin.r >= 40                    # 현재가 칩 잘림 방지 여백


def test_cmp_initial_yrange_pct_frame():
    """비교(%) 모드 초기 y = 정규화 % 프레임 — 달러 가격대(45~55)가 아니어야 (1y 버그 회귀)."""
    idx = pd.date_range("2024-06-01", periods=500, freq="D")
    tr = pd.Series([50.0 * 1.0006 ** i for i in range(500)], index=idx)     # $50대
    pr = pd.Series([50.0 * 1.0001 ** i for i in range(500)], index=idx)
    r = charts.cmp_initial_yrange(tr, {"PR": pr}, 365)
    assert r is not None
    lo, hi = r
    assert lo < 5 and hi < 40                        # % 스케일 (달러 50대 아님)
    assert hi > 10                                    # TR 1y ≈ +24% 포함
    assert charts.cmp_initial_yrange(tr.iloc[:1], {}, 365) is None   # 재료 부족


def test_price_chart_compare_initial_view_pct():
    """compare 차트의 서버측 초기 y-range 가 % 프레임 (panes 1·2 양 경로)."""
    idx = pd.date_range("2024-06-01", periods=500, freq="D")
    close = pd.Series([50.0 * 1.0006 ** i for i in range(500)], index=idx)
    hist = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": [1e6] * 500}, index=idx)
    cmp_s = pd.Series([100.0 * 1.0003 ** i for i in range(500)], index=idx)
    fig1 = charts.price_chart(hist, "T", compare={"C": cmp_s}, view_days=365)   # panes==1
    r1 = fig1.layout.yaxis.range
    assert r1 is not None and r1[1] < 45              # % 프레임 (가격 50~67 아님)
    fig2 = charts.price_chart(hist, "T", compare={"C": cmp_s}, view_days=365,
                              show_rsi=True, show_volume=True)                  # panes>1
    r2 = fig2.layout.yaxis.range
    assert r2 is not None and r2[1] < 45
    main = next(tr for tr in fig1.data if tr.name == "T")
    assert "%{y:+.2f}%" in main.hovertemplate         # hover 포맷


def test_hbar_title_margin_and_range():
    """hbar 제목 잘림 회귀 — 제목 시 t>=40·좌측 앵커·x_range 고정축."""
    fig = charts.hbar(["비용", "성과"], [12.0, 62.0], "구성 점수 (백분위)",
                      pct=False, x_range=(0, 105))
    assert fig.layout.margin.t >= 40
    assert fig.layout.title.xanchor == "left"
    assert list(fig.layout.xaxis.range) == [0, 105]
    assert charts.hbar(["a"], [1.0]).layout.margin.t == 10   # 무제목은 기존 여백


def test_bullet_bands_generic():
    """범용 적정가 불릿 — 밴드·중앙 마커·현재가 vline (멀티플 기준가 인디케이터)."""
    fig = charts.bullet_bands(142.0, [("Fwd EPS×PER (±15%)", 99.0, 117.0, 134.0)])
    assert len(fig.data) == 2                          # 밴드 라인 + mid 마커
    assert fig.data[0].x == (99.0, 134.0)
    assert any("현재 $142" in (s.text or "") for s in fig.layout.annotations)


def test_price_chart_scattergl_for_large_series():
    """대용량(≥1500봉) 라인 = WebGL(Scattergl)·소용량 = SVG Scatter (스타일 동일)."""
    import plotly.graph_objects as go
    idx = pd.date_range("2020-01-01", periods=1600, freq="D")
    close = pd.Series(range(100, 1700), index=idx, dtype=float)
    big = pd.DataFrame({"Close": close}, index=idx)
    fig = charts.price_chart(big, "T")
    assert isinstance(fig.data[0], go.Scattergl)
    small = charts.price_chart(big.iloc[:100], "T")
    assert isinstance(small.data[0], go.Scatter) and not isinstance(small.data[0], go.Scattergl)


def test_ichimoku_webgl_and_cloud_nan_free():
    """일목·MA·BB 대용량 WebGL 전환 + 구름 fill 쌍은 NaN 없는 유효 구간만 (gl 아티팩트 방지)."""
    import numpy as np
    import plotly.graph_objects as go
    idx = pd.date_range("2020-01-01", periods=1600, freq="D")
    base = pd.Series(range(100, 1700), index=idx, dtype=float)
    hist = pd.DataFrame({"Open": base, "High": base * 1.01, "Low": base * 0.99,
                         "Close": base, "Volume": [1e6] * 1600}, index=idx)
    fig = charts.price_chart(hist, "T", ichimoku=True, bollinger=True, mas=(200,))
    by = {tr.name: tr for tr in fig.data if tr.name}
    for nm in ("선행A", "선행B(구름)", "MA200", "BB상단", "BB하단"):
        assert isinstance(by[nm], go.Scattergl), nm
    assert not np.isnan(np.asarray(by["선행B(구름)"].y, dtype=float)).any()   # 구름 NaN 0
    assert not np.isnan(np.asarray(by["BB하단"].y, dtype=float)).any()


def test_price_levels_chart():
    """가격 레벨 사다리 — kind 별 마커·현재가 vline (순수)."""
    fig = charts.price_levels(100.0, [("기술 지지", 96.0, "support"),
                                      ("기술 저항", 108.0, "resist"),
                                      ("밸류 기준", 117.0, "fair")])
    assert len(fig.data) == 3
    syms = {tr.marker.symbol for tr in fig.data}
    assert {"triangle-up", "triangle-down", "diamond"} <= syms
    assert any("현재 100" in (a.text or "") for a in fig.layout.annotations)


def _ohlcv(n=200):
    import numpy as np
    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    base = np.linspace(100, 200, n) + np.sin(np.arange(n) / 9) * 4
    return pd.DataFrame({"Open": base, "High": base + 2, "Low": base - 2,
                         "Close": base + 1, "Volume": [1e6 + i for i in range(n)]}, index=idx)


def test_price_chart_macd_stoch_panels():
    """MACD·스토캐스틱 하단 패널 — 5패널 축 배치·트레이스·서브패널 선형 유지."""
    hist = _ohlcv()
    fig = charts.price_chart(hist, "T", kind="candle", show_volume=True, show_rsi=True,
                             show_macd=True, show_stoch=True)
    names = [tr.name for tr in fig.data]
    assert any("MACD(12" in (n or "") for n in names)
    assert any(n == "%K(14)" for n in names) and any(n == "%D(3)" for n in names)
    # 패널 5개 → yaxis..yaxis5. 순서: 가격1·거래량2·RSI3·MACD4·스토5
    yaxes = [k for k in vars(fig.layout) if str(k).startswith("yaxis")]
    assert len([k for k in fig.layout if str(k).startswith("yaxis")]) == 5
    by = {tr.name: tr for tr in fig.data}
    assert by["거래량"].yaxis == "y2"
    assert by["RSI(14)"].yaxis == "y3"
    assert by["MACD(12·26)"].yaxis == "y4"
    assert by["%K(14)"].yaxis == "y5"
    assert fig.layout.yaxis5.range == (0, 100)          # 스토 0~100 밴드
    assert fig.layout.height >= 900


def test_price_chart_macd_only():
    """MACD 단독(거래량·RSI 없이) → 2패널·행 배정 정확 (동적 일반화)."""
    fig = charts.price_chart(_ohlcv(), "T", show_macd=True)
    by = {tr.name: tr for tr in fig.data}
    assert by["MACD(12·26)"].yaxis == "y2"              # 가격 다음 첫 서브패널
    n_yaxes = len([k for k in fig.layout if str(k).startswith("yaxis")])
    assert n_yaxes == 2


def test_price_chart_stoch_requires_ohlc():
    """스토캐스틱은 High/Low 필요 — Close만 있으면 침묵 스킵 (패널 미생성)."""
    hist = _ohlc()[["Close"]]
    fig = charts.price_chart(hist, "T", show_stoch=True)
    assert not any((tr.name or "").startswith("%K") for tr in fig.data)


def test_price_chart_log_scale():
    """로그 스케일 — 가격축 type=log·y범위 log10·도형/주석 y log10·서브패널 선형 유지."""
    import math
    hist = _ohlcv()
    fig = charts.price_chart(hist, "T", kind="line", avg_cost=150.0, view_days=90,
                             log_scale=True, show_rsi=True)
    assert fig.layout.yaxis.type == "log"
    assert fig.layout.yaxis2.type in (None, "-", "linear")    # RSI 서브패널 선형
    # 초기 y범위 log10 밴드 (가격 100~200 → log10 2.0~2.3)
    yr = fig.layout.yaxis.range
    assert yr and 1.9 < yr[0] < 2.4 and 1.9 < yr[1] < 2.4
    # 평단선 shape y = log10(150)
    price_shapes = [s.y0 for s in fig.layout.shapes if s.yref == "y" and s.y0 is not None]
    assert any(abs(v - math.log10(150.0)) < 1e-6 for v in price_shapes)
    # 가격축 주석은 log10 위치이되 텍스트는 raw 값 유지
    price_anns = [a for a in fig.layout.annotations if a.yref == "y"]
    assert price_anns and all(1.9 < a.y < 2.4 for a in price_anns)
    assert any("150" in (a.text or "") for a in price_anns)   # raw 텍스트


def test_price_chart_event_markers_and_zones():
    """이벤트 마커(실적·배당·뉴스) + 진입존 밴드 — 배지 트레이스·범위밖 스킵·존 밴드/라벨."""
    hist = _ohlcv()
    events = [
        {"date": "2024-03-15", "marker": "E", "color": "#26a69a", "hover": "실적 beat +9.5%"},
        {"date": "2024-05-01", "marker": "D", "color": "#22d3ee", "hover": "배당 0.25"},
        {"date": "2020-01-01", "marker": "E", "color": "#26a69a", "hover": "범위 밖"},
    ]
    zones = [{"lo": 120.0, "hi": 124.0, "label": "🎯 1차 존 ×3"},
             {"lo": 110.0, "hi": 110.0, "label": "🎯 2차 존"}]     # 점 존 → 얇은 밴드
    fig = charts.price_chart(hist, "T", kind="candle", events=events, zones=zones)
    ev = [tr for tr in fig.data if (tr.name or "").startswith("이벤트")]
    assert ev and sum(len(tr.x) for tr in ev) == 2               # 범위 밖 1건 스킵
    assert all(tr.customdata is not None and "customdata" in tr.hovertemplate for tr in ev)
    # 마커 y = 봉 저가 아래 (1.5% 오프셋)
    for tr in ev:
        for x_, y_ in zip(tr.x, tr.y):
            low = float(hist["Low"].asof(x_))
            assert abs(y_ - low * 0.985) < 1e-9
    rects = [s for s in fig.layout.shapes if s.type == "rect"
             and (s.fillcolor or "").startswith("rgba(41,98,255")]
    assert len(rects) == 2
    thin = min(rects, key=lambda s: s.y1 - s.y0)
    assert thin.y1 > thin.y0                                     # 점 존도 밴드화
    labels = [a.text for a in fig.layout.annotations if "존" in (a.text or "")]
    assert len(labels) == 2
    # 이벤트 없음 → 트레이스 없음
    fig0 = charts.price_chart(hist, "T")
    assert not [tr for tr in fig0.data if (tr.name or "").startswith("이벤트")]


def test_price_chart_events_log_scale_zones_converted():
    """로그축 — 존 밴드(도형)는 log10 변환·이벤트 마커(트레이스)는 raw 유지."""
    import math
    hist = _ohlcv()
    fig = charts.price_chart(hist, "T", log_scale=True,
                             events=[{"date": "2024-03-15", "marker": "E",
                                      "color": "#26a69a", "hover": "h"}],
                             zones=[{"lo": 120.0, "hi": 124.0, "label": "🎯 존"}])
    zr = [s for s in fig.layout.shapes if s.type == "rect"
          and (s.fillcolor or "").startswith("rgba(41,98,255")]
    assert zr and abs(zr[0].y0 - math.log10(120.0)) < 1e-9
    ev = [tr for tr in fig.data if (tr.name or "").startswith("이벤트")][0]
    assert 90 < float(ev.y[0]) < 210                             # 트레이스 raw


def test_price_chart_fund_eps_panel():
    """분기 EPS 서브패널 — beat 초록/miss 빨강 바 + 예상 마커·빈 데이터 무패널·비교 차단."""
    hist = _ohlcv()
    eps = [{"date": "2024-03-15", "eps_est": 2.0, "eps_actual": 2.3, "surprise_pct": 15.0},
           {"date": "2024-06-14", "eps_est": 2.1, "eps_actual": 1.9, "surprise_pct": -9.5},
           {"date": "2024-09-13", "eps_est": None, "eps_actual": 2.5, "surprise_pct": None},
           {"date": None, "eps_est": 1.0, "eps_actual": 1.0, "surprise_pct": 0.0},   # 무발표일 스킵
           {"date": "2024-12-13", "eps_est": 2.2, "eps_actual": None, "surprise_pct": None}]  # 무실적 스킵
    fig = charts.price_chart(hist, "T", show_volume=True, fund_eps=eps)
    bars = [tr for tr in fig.data if getattr(tr, "name", "") == "분기 EPS"]
    assert len(bars) == 1 and len(bars[0].x) == 3                 # 유효 3행만
    cols = list(bars[0].marker.color)
    assert cols[0] == charts._GREEN and cols[1] == charts._RED    # beat/miss 색
    assert cols[2] == charts._GREEN                               # 서프라이즈 None → 중립(≥0) 초록
    est = [tr for tr in fig.data if getattr(tr, "name", "") == "예상 EPS"]
    assert len(est) == 1 and len(est[0].x) == 2                   # est 있는 행만
    assert any("+15.0%" in h for h in bars[0].customdata)
    # 패널 행이 실제로 늘었는지 (가격+거래량+EPS = yaxis3 존재)
    assert fig.layout.yaxis3 is not None
    # 빈/무효 데이터 → 패널 없음
    fig0 = charts.price_chart(hist, "T", show_volume=True, fund_eps=[])
    assert not [tr for tr in fig0.data if getattr(tr, "name", "") == "분기 EPS"]
    # 비교 모드 → 자동 차단
    import pandas as pd
    cmp_s = pd.Series(hist["Close"].values * 1.1, index=hist.index)
    figc = charts.price_chart(hist, "T", compare={"C": cmp_s}, fund_eps=eps)
    assert not [tr for tr in figc.data if getattr(tr, "name", "") == "분기 EPS"]


def test_heikin_ashi_transform():
    """하이킨아시 — 정의 검증(HA종가·재귀 시가·고저 포섭)·Volume 보존·graceful."""
    hist = _ohlcv(50)
    ha = charts.heikin_ashi(hist)
    r0 = hist.iloc[0]
    assert abs(ha["Close"].iloc[0] - (r0.Open + r0.High + r0.Low + r0.Close) / 4) < 1e-9
    assert abs(ha["Open"].iloc[0] - (r0.Open + r0.Close) / 2) < 1e-9
    # 재귀: HA시가[i] = (HA시가[i-1]+HA종가[i-1])/2
    assert abs(ha["Open"].iloc[5] - (ha["Open"].iloc[4] + ha["Close"].iloc[4]) / 2) < 1e-9
    assert (ha["High"] >= ha[["Open", "Close"]].max(axis=1) - 1e-12).all()
    assert (ha["Low"] <= ha[["Open", "Close"]].min(axis=1) + 1e-12).all()
    assert "Volume" in ha.columns and (ha["Volume"].values == hist["Volume"].values).all()
    assert len(ha) == len(hist)
    # OHLC 없으면 원본 그대로 (graceful)
    close_only = hist[["Close"]]
    assert charts.heikin_ashi(close_only) is close_only
    assert charts.heikin_ashi(None) is None


def test_price_chart_no_plotly_spikes():
    """십자선은 embed JS DOM 오버레이 — plotly 스파이크 금지 (마우스무브 재그리기
    스터터 성능 회귀 확정 → 제거. 재도입 방지 회귀)."""
    fig = charts.price_chart(_ohlcv(), "T", show_volume=True, show_rsi=True)
    assert fig.layout.xaxis.showspikes is not True
    assert fig.layout.yaxis.showspikes is not True
    fig1 = charts.price_chart(_ohlcv(), "T")
    assert fig1.layout.yaxis.showspikes is not True


def test_price_chart_log_scale_yref_none_annotations():
    """로그축 yref=None 주석 변환 — 적대 리뷰 확정 버그(B1) 회귀 방어.

    `add_annotation(row/col 없이)` 는 yref=None 으로 남지만 plotly.js 가 첫 y축(가격)으로
    coerce → log10 변환에서 누락되면 raw 가격이 10^가격 위치로 날아간다. 트리거 2종:
    (1) 추세선/채널 라벨 — 모든 패널 구성, (2) panes==1 의 tn-hi/lo·현재가 라벨.
    """
    import math
    hist = _ohlcv()
    tls = [{"kind": "support", "x0": hist.index[0], "x1": hist.index[-1],
            "y0": 100.0, "y1": 150.0, "label": "지지선"}]
    # (2) 하단 지표 전부 off → panes==1 (+ 추세선 라벨 = 트리거 1도 동시 검증)
    fig = charts.price_chart(hist, "T", log_scale=True, trend_lines=tls, view_days=90)
    assert fig.layout.yaxis.type == "log"
    for an in fig.layout.annotations:
        if getattr(an, "yref", None) in (None, "y") and isinstance(an.y, (int, float)):
            assert an.y < 3.0, f"raw 가격 잔존(log10 미변환): {an.text!r} y={an.y}"
    # 지지선 라벨(y1=150)이 log10(150)≈2.176 로 변환됐는지 명시 확인
    tl_ann = [a for a in fig.layout.annotations if (a.text or "") == "지지선"]
    assert tl_ann and abs(tl_ann[0].y - math.log10(150.0)) < 1e-6
    # (1) 멀티패널에서도 추세선 라벨(yref=None 유지) 변환
    fig2 = charts.price_chart(hist, "T", log_scale=True, trend_lines=tls,
                              show_volume=True, show_rsi=True)
    tl2 = [a for a in fig2.layout.annotations if (a.text or "") == "지지선"]
    assert tl2 and abs(tl2[0].y - math.log10(150.0)) < 1e-6
    # 서브패널(y3=RSI) 밴드·라인은 raw 유지 (오변환 없음)
    sub_shapes = [s for s in fig2.layout.shapes if getattr(s, "yref", "") == "y3"]
    assert sub_shapes and all(s.y0 is None or s.y0 >= 0 for s in sub_shapes)
    assert any((s.y0 == 30 or s.y0 == 70) for s in sub_shapes if s.y0 is not None)


def test_price_chart_log_scale_off_is_raw():
    """로그 off(기본) — 축 선형·도형 y 는 raw 값 (회귀 방어)."""
    fig = charts.price_chart(_ohlcv(), "T", avg_cost=150.0)
    assert fig.layout.yaxis.type in (None, "-", "linear")
    price_shapes = [s.y0 for s in fig.layout.shapes if s.yref == "y" and s.y0 is not None]
    assert any(abs(v - 150.0) < 1e-6 for v in price_shapes)


def test_price_chart_log_compare_disabled():
    """비교(%) 모드에선 로그 자동 비활성 (log_scale=True 무시)."""
    import pandas as pd
    hist = _ohlcv()
    cmp_s = pd.Series([50.0 + i * 0.1 for i in range(len(hist))], index=hist.index)
    fig = charts.price_chart(hist, "T", compare={"C": cmp_s}, log_scale=True)
    assert fig.layout.yaxis.type in (None, "-", "linear")


def test_volume_axis_spike_cap():
    """거래량 축 q98 캡 — 역사적 스파이크가 축을 지배하지 않게 (최근 60봉은 보장)."""
    idx = pd.date_range("2024-01-01", periods=400, freq="D")
    close = pd.Series([100.0] * 400, index=idx)
    vol = pd.Series([10_000_000.0] * 400, index=idx)
    vol.iloc[50] = 1_000_000_000.0                    # 1B 스파이크
    hist = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": vol}, index=idx)
    fig = charts.price_chart(hist, "T", show_volume=True)
    vr = fig.layout.yaxis2.range
    assert vr is not None and vr[1] < 100_000_000     # 1B 스파이크에 미지배
    assert vr[1] >= 10_000_000 * 1.1                  # 평상 막대는 안 잘림


def test_view_window_windows_long_history():
    """윈도잉 — 뷰(기간)의 pan_mult배+워밍업만 tail 로 남기고 최근 데이터는 보존."""
    idx = pd.date_range("2016-01-01", periods=3000, freq="D")
    hist = pd.DataFrame({"Close": range(3000)}, index=idx)
    w = charts.view_window(hist, 365)
    view_bars = int((idx >= idx[-1] - pd.Timedelta(days=365)).sum())
    assert len(w) == view_bars * 5 + 250              # 팬버퍼 5× + 워밍업 250
    assert len(w) < len(hist)
    assert w.index[-1] == hist.index[-1]              # 최신 봉 보존 (tail)
    assert int(w["Close"].iloc[-1]) == 2999


def test_view_window_full_and_short_passthrough():
    """'전체'(None)·짧은 데이터·빈 값은 무윈도잉 그대로 (동일 객체)."""
    idx = pd.date_range("2024-01-01", periods=500, freq="D")
    hist = pd.DataFrame({"Close": range(500)}, index=idx)
    assert charts.view_window(hist, None) is hist     # 전체 = 전량
    assert charts.view_window(hist, 90) is hist       # floor(800) > len → 그대로
    assert charts.view_window(None, 90) is None
    empty = pd.DataFrame({"Close": []})
    assert charts.view_window(empty, 90) is empty


def test_view_window_floor_and_series():
    """짧은 뷰는 floor(기본 800봉) 보장 — Series 도 지원 (비교 오버레이용)."""
    idx = pd.date_range("2000-01-01", periods=11000, freq="D")
    s = pd.Series(range(11000), index=idx, dtype=float)
    w = charts.view_window(s, 90)                     # 뷰 91봉×5+250=705 < floor
    assert len(w) == 800
    assert float(w.iloc[-1]) == 10999.0


def test_view_window_serialization_shrinks():
    """윈도잉된 fig 직렬화가 전량 대비 유의미하게 작다 (payload 회귀 방어)."""
    idx = pd.date_range("1995-01-01", periods=11000, freq="D")
    close = pd.Series([100.0 + (i % 500) for i in range(11000)], index=idx)
    hist = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": [1e6] * 11000}, index=idx)
    full = charts.price_chart(hist, "T", view_days=180, show_volume=True)
    win = charts.price_chart(charts.view_window(hist, 180), "T",
                             view_days=180, show_volume=True)
    assert len(win.to_json()) < len(full.to_json()) * 0.25


def test_kama_series_adapts():
    """KAMA — 워밍업 후 유한 + 강추세에선 가격 근접·마지막 값이 SMA30 보다 가깝다."""
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    c = pd.Series([100.0 + i for i in range(200)], index=idx)   # 완전 추세(ER≈1)
    k = charts.kama_series(c)
    assert k.iloc[:10].isna().all()                 # 워밍업 NaN
    assert k.iloc[-1] == k.iloc[-1]
    sma30 = float(c.rolling(30).mean().iloc[-1])
    assert abs(float(k.iloc[-1]) - float(c.iloc[-1])) < abs(sma30 - float(c.iloc[-1]))


def _ohlcv_v(n=300):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = pd.Series([100 + i * 0.1 + (i % 13) for i in range(n)], index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.02, "Low": c * 0.98, "Close": c,
                         "Volume": [1e6 + (i % 5) * 1e5 for i in range(n)]}, index=idx)


def test_price_chart_new_top_overlays():
    """켈트너·KAMA·샹들리에 — 트레이스 존재 + 켈트너 상단>하단·샹들리에 롱<고가."""
    hist = _ohlcv_v()
    fig = charts.price_chart(hist, "T", keltner=True, kama=True, chandelier=True)
    names = [t.name or "" for t in fig.data]
    for want in ("켈트너 상단", "켈트너 하단", "KAMA(10·2·30)", "샹들리에 롱스탑", "샹들리에 숏스탑"):
        assert any(want in n for n in names), f"누락: {want}"
    up = next(t for t in fig.data if (t.name or "") == "켈트너 상단")
    dn = next(t for t in fig.data if (t.name or "") == "켈트너 하단")
    assert float(up.y[-1]) > float(dn.y[-1])
    ls = next(t for t in fig.data if (t.name or "") == "샹들리에 롱스탑")
    assert float(ls.y[-1]) < float(hist["High"].iloc[-1])


def test_price_chart_new_bottom_panels_and_8panes():
    """Aroon·%b·PVT 서브패널 — 8패널 동적 배정·yaxis8 존재·높이 확장."""
    hist = _ohlcv_v()
    fig = charts.price_chart(hist, "T", show_volume=True, show_rsi=True, show_macd=True,
                             show_stoch=True, show_aroon=True, show_bbpct=True,
                             show_pvt=True)
    assert fig.layout.yaxis8 is not None            # 가격 + 서브 7 = 8행
    assert (fig.layout.height or 0) > 1000          # 다패널 높이 확장
    names = [t.name or "" for t in fig.data]
    for want in ("Aroon Up", "Aroon Down", "%b(20·2σ)", "PVT"):
        assert any(want in n for n in names), f"누락: {want}"
    ar = next(t for t in fig.data if (t.name or "") == "Aroon Up")
    vals = [v for v in ar.y if v == v]
    assert vals and all(0 <= v <= 100 for v in vals)


def test_price_chart_new_panels_graceful_without_data():
    """OHLC 없으면 Aroon 자동 비활성·Volume 없으면 PVT 비활성 (무예외·행 미배정)."""
    idx = pd.date_range("2024-01-01", periods=100, freq="D")
    hist = pd.DataFrame({"Close": [100.0 + i for i in range(100)]}, index=idx)
    fig = charts.price_chart(hist, "T", show_aroon=True, show_pvt=True, show_bbpct=True)
    # aroon(OHLC)·pvt(Volume) 스킵 → %b 만 = 2패널
    assert fig.layout.yaxis2 is not None and getattr(fig.layout, "yaxis3", None) is None


def test_price_chart_compare_disables_new_overlays():
    """비교(%) 모드 — 켈트너·KAMA·샹들리에 자동 비활성 (절대가격 오버레이 규약)."""
    hist = _ohlcv_v()
    cmp_s = pd.Series([50.0 + i * 0.1 for i in range(len(hist))], index=hist.index)
    fig = charts.price_chart(hist, "T", compare={"C": cmp_s},
                             keltner=True, kama=True, chandelier=True)
    names = [t.name or "" for t in fig.data]
    assert not any("켈트너" in n or "KAMA" in n or "샹들리에" in n for n in names)


def test_fmt_big():
    assert charts.fmt_big(2.85e12) == "2.9T"
    assert charts.fmt_big(6.6e10) == "66.0B"
    assert charts.fmt_big(-2.0e9) == "-2.0B"
    assert charts.fmt_big(1.5e7) == "15.0M"
    assert charts.fmt_big(999) == "999"
    assert charts.fmt_big(None) == "—"


def test_price_chart_fundamentals_panel():
    """펀더멘털 서브패널 — 매출 바+순이익 라인·마진 hover·빈 rows 는 패널 생략."""
    hist = _ohlcv_v(200)
    rows = [{"date": "2024-03-31", "revenue": 5.0e10, "net_income": 1.2e10, "margin": 0.24},
            {"date": "2024-06-30", "revenue": 5.5e10, "net_income": -1.0e9, "margin": -0.02},
            {"date": "2024-09-30", "revenue": 6.1e10, "net_income": 1.7e10, "margin": 0.28}]
    fig = charts.price_chart(hist, "T", fundamentals=rows)
    names = [t.name or "" for t in fig.data]
    assert "매출" in names and "순이익" in names
    assert fig.layout.yaxis2 is not None            # 가격+펀더멘털 2행
    rev = next(t for t in fig.data if (t.name or "") == "매출")
    assert "순마진" in rev.customdata[0] and "50.0B" in rev.customdata[0]
    # 빈/무효 rows → 패널 없음
    fig2 = charts.price_chart(hist, "T", fundamentals=[{"date": "2024-01-01"}])
    assert getattr(fig2.layout, "yaxis2", None) is None


def test_price_chart_fundamentals_unsorted_rows():
    """비정렬 rows 방어 — 바 폭(min 간격)이 음수가 되던 실측 버그 회귀."""
    hist = _ohlcv_v(200)
    rows = [{"date": "2024-12-31", "revenue": 6.6e10, "net_income": 1.9e10, "margin": 0.29},
            {"date": "2024-03-31", "revenue": 5.0e10, "net_income": 1.2e10, "margin": 0.24},
            {"date": "2024-09-30", "revenue": 6.1e10, "net_income": 1.7e10, "margin": 0.28},
            {"date": "2024-06-30", "revenue": 5.5e10, "net_income": 1.5e10, "margin": 0.27}]
    fig = charts.price_chart(hist, "T", fundamentals=rows)      # 예외 없이 빌드
    bar = next(t for t in fig.data if (t.name or "") == "매출")
    assert bar.width and float(bar.width) > 0
