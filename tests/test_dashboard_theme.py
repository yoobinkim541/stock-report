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


def _needle_y(svg):
    return float(re.search(r'<line[^>]*y2="([0-9.]+)"', svg).group(1))


def test_rating_gauge_needle_side():
    """+score → 바늘 우측(매수), −score → 좌측(매도). 중심 cx=100."""
    assert _needle_x(theme.rating_gauge_html(0.8)) > 100
    assert _needle_x(theme.rating_gauge_html(-0.8)) < 100


def test_rating_gauge_neutral_top():
    # score 0 → 바늘 ~중앙(100)·상단(y<중심)
    assert abs(_needle_x(theme.rating_gauge_html(0.0)) - 100) < 1.0
    assert _needle_y(theme.rating_gauge_html(0.0)) < 100


def test_rating_gauge_arcs_on_top_half():
    # L3 재작성: 5존 아크·니들이 상단 반원(y ≤ 중심). 이전 버그=하단 반원(viewBox 밖 잘림) 회귀차단.
    svg = theme.rating_gauge_html(0.0)
    ys = [float(y) for y in re.findall(r'A 78 78 0 [01] [01] [0-9.]+ ([0-9.]+)', svg)]
    assert len(ys) == 5 and all(y <= 101 for y in ys)   # 아크 끝점 전부 상단(중심+ε 이내)


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


# ── O2 시장 지표 카드 (F&G·지수 RSI) ──────────────────────────────────
def test_fng_badge_html():
    assert "극공포" in theme.fng_badge_html(10)            # <25
    assert "극탐욕" in theme.fng_badge_html(90)            # 상단
    assert "중립" in theme.fng_badge_html(50)              # 45~55
    h = theme.fng_badge_html(31.9, prev_week=26.0)
    assert "32" in h and "전주 26" in h                     # 점수·추세
    assert theme.fng_badge_html(None) == ""                 # 잘못된 입력 graceful


def test_index_rsi_html_zones():
    h = theme.index_rsi_html("S&P 500", price=6000, chg=1.2, rsi_d=75.0, rsi_w=30.0)
    assert "S&P 500" in h and "과매수" in h and "과매도" in h   # 75→과매수·30→과매도
    assert theme.RED in h and theme.GREEN in h
    assert "—" in theme.index_rsi_html("나스닥", rsi_d=None)    # 결측 graceful


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


def test_css_has_mobile_and_contrast_polish():
    """H4: 반응형 미디어쿼리 + 밝아진 MUTED + 컴포넌트 라운드 통일."""
    css = theme._CSS
    assert "@media (max-width: 600px)" in css          # 모바일 반응형
    assert ".tn-wl-spark" in css and "display: none" in css  # 모바일 스파크 숨김
    # MUTED 대비 상향(기존 흐린 #787b86 폐기)
    assert theme.MUTED != "#787b86"
    assert theme.MUTED in css


def test_watchlist_3col_no_sparkline():
    """사이드바 워치리스트 3열(종목·값·등락%) — 스파크 제거로 등락%열 잘림 방지."""
    row = theme.watchlist_row_html("NVDA", last=6000, chg_pct=1.2, name="NVIDIA")
    assert "tn-wl-spark" not in row
    assert "tn-wl-sym" in row and "tn-wl-last" in row and "tn-wl-chg" in row


def test_css_restores_material_icon_font():
    """광역 span override 로부터 Streamlit 머티리얼 아이콘 폰트 복원 (_arrow_right 방지)."""
    css = theme._CSS
    assert "stIconMaterial" in css and "Material Symbols" in css
