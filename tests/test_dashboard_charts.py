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


def test_hbar_orders_ascending():
    fig = charts.hbar(["x", "y", "z"], [0.3, 0.1, 0.6])
    bar = fig.data[0]
    assert list(bar.y) == ["y", "x", "z"]       # 작은→큰 (큰 값이 위)


def test_signed_bars_color_by_sign():
    fig = charts.signed_bars(["a", "b"], [2.0, -1.0])
    colors = list(fig.data[0].marker.color)
    assert colors[0] == charts._GREEN and colors[1] == charts._RED


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
