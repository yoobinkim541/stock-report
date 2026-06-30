"""dashboard/theme.py 순수 빌더 단위 테스트 (streamlit/네트워크 불필요).

색·부호·게이지 바늘 방향·스파크라인·plotly 테마. theme 는 import 시 streamlit 미로드.
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard import theme  # noqa: E402


def test_ticker_hero_up_color():
    h = theme.ticker_hero_html("AAPL", "Apple", 100.0, 1.5, 1.52)
    assert theme.GREEN in h and "▲" in h and "tn-hero" in h


def test_ticker_hero_down_color():
    h = theme.ticker_hero_html("AAPL", "Apple", 100.0, -1.5, -1.52)
    assert theme.RED in h and "▼" in h


def test_ticker_hero_none_price_graceful():
    h = theme.ticker_hero_html("X")
    assert "tn-hero" in h and "—" in h


def test_ticker_hero_strips_ks_suffix_in_badge():
    h = theme.ticker_hero_html("005930.KS", "삼성전자", 70000, 100, 0.14)
    assert ">0059<" in h  # .KS 제거 후 4자


def _needle_x(svg):
    return float(re.search(r'<line[^>]*x2="([0-9.]+)"', svg).group(1))


def test_rating_gauge_needle_side():
    """+score → 바늘 우측(매수), −score → 좌측(매도). 중심 cx=110."""
    assert _needle_x(theme.rating_gauge_html(0.8)) > 110
    assert _needle_x(theme.rating_gauge_html(-0.8)) < 110


def test_rating_gauge_neutral_top():
    # score 0 → 바늘 ~중앙(110)
    assert abs(_needle_x(theme.rating_gauge_html(0.0)) - 110) < 1.0


def test_rating_gauge_clamps_verdict():
    assert "강력매수" in theme.rating_gauge_html(5.0)    # +1 로 클램프
    assert "강력매도" in theme.rating_gauge_html(-5.0)
    assert "중립" in theme.rating_gauge_html(0.0)


def test_rating_gauge_five_zones():
    g = theme.rating_gauge_html(0.3)
    assert g.count("<path") == 5


def test_sparkline_up_down_empty():
    assert theme.GREEN in theme.sparkline_svg([1, 2, 3])
    assert theme.RED in theme.sparkline_svg([3, 2, 1])
    assert "polyline" not in theme.sparkline_svg([1])     # 부족 → 빈 svg


def test_watchlist_html():
    w = theme.watchlist_html([{"symbol": "MSFT", "last": 430.1, "chg_pct": 1.2}])
    assert "MSFT" in w and "tn-wl-row" in w and theme.GREEN in w


def test_section_label():
    assert "리스크" in theme.section_label_html("리스크")


def test_apply_plotly_theme():
    pytest.importorskip("plotly")
    import plotly.graph_objects as go
    fig = theme.apply_plotly_theme(go.Figure())
    assert fig.layout.paper_bgcolor == "rgba(0,0,0,0)"
    assert "JetBrains" in fig.layout.font.family
    assert fig.layout.xaxis.gridcolor == theme.GRID


def test_theme_import_no_streamlit():
    """theme import 가 streamlit 을 끌어오지 않는다(charts 순수성 보장).

    서브프로세스에서 검증 — 현재 프로세스의 sys.modules 를 건드리면 같은 세션의
    AppTest(streamlit 런타임)가 깨지므로 절대 in-process 로 reload 하지 않는다.
    """
    import subprocess
    code = ("import sys; import dashboard.theme; "
            "bad=[m for m in sys.modules if m=='streamlit' or m.startswith('streamlit.')]; "
            "assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
