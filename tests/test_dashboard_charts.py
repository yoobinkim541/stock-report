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
    anns = " ".join(a.text for a in fig.layout.annotations)
    assert "+" in anns and "-" in anns and "<b>" in anns       # 최고/최저 % + 현재가 칩
    # 거래량만 (RSI off) → 거래량 y2
    fig2 = charts.price_chart(hist, "T", show_volume=True)
    assert {tr.name: tr for tr in fig2.data}["거래량"].yaxis == "y2"
    # Volume 컬럼 없으면 침묵 스킵
    fig3 = charts.price_chart(hist.drop(columns=["Volume"]), "T", show_volume=True)
    assert "거래량" not in [tr.name for tr in fig3.data]
