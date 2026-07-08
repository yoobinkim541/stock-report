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


# ── O2 시장 지표 게이지 (F&G·지수 RSI 반원 게이지) ─────────────────────
def test_fng_gauge_html():
    assert "극공포" in theme.fng_gauge_html(10)            # <25 sub
    assert "극탐욕" in theme.fng_gauge_html(90)            # 상단
    assert "중립" in theme.fng_gauge_html(50)              # 45~55
    h = theme.fng_gauge_html(31.9, prev_week=26.0)
    assert "32" in h and "전주 26" in h                     # 점수·추세
    assert "<svg" in h and "<path" in h                     # 반원 게이지 SVG
    assert theme.fng_gauge_html(None) == ""                 # 잘못된 입력 graceful


def test_index_rsi_gauges_html():
    h = theme.index_rsi_gauges_html("S&P 500", price=6000, chg=1.2, rsi_d=75.0, rsi_w=30.0)
    assert "S&P 500" in h and "과매수" in h and "과매도" in h   # 75→과매수·30→과매도 sub
    assert h.count("<svg") == 2                              # 일봉·주봉 게이지 2개
    assert theme.RED in h and theme.GREEN in h
    assert "—" in theme.index_rsi_gauges_html("나스닥", rsi_d=None)   # 결측 graceful


def test_gauge_svg_zones_and_needle():
    g = theme._gauge_svg(50, 0, 100, [(30, theme.GREEN), (70, theme.MUTED), (100, theme.RED)], big="50")
    assert g.count("<path") == 3 and "<line" in g and "50" in g   # 3존 + 니들
    assert "<line" not in theme._gauge_svg(None, 0, 100, [(100, theme.MUTED)])   # 값 없으면 니들 없음


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


# ── P3 모의 레일 빌더 (순수) ──────────────────────────────────────────────────
def test_paper_rail_html_rows_and_compact_money():
    rows = [{"label": "🇰🇷 국내", "currency": "₩", "nav": 10_500_000.0,
             "cum_ret": 5.0, "day_ret": 0.5, "n_days": 12},
            {"label": "🇺🇸 미국", "currency": "$", "nav": 100_000.0,
             "cum_ret": -1.2, "day_ret": -0.1, "n_days": 3}]
    html = theme.paper_rail_html(rows)
    assert "모의투자" in html and "🇰🇷 국내" in html
    assert "₩1,050만" in html and "$100,000" in html               # 압축 금액
    assert "+5.00%" in html and "-1.20%" in html
    assert theme.GREEN in html and theme.RED in html               # 부호 색
    assert "전일 +0.50%" in html and "기록 12일" in html            # 툴팁


def test_paper_rail_html_eok_and_empty():
    html = theme.paper_rail_html([{"label": "kr", "currency": "₩", "nav": 2.5e8,
                                   "cum_ret": 0.0}])
    assert "₩2.50억" in html
    empty = theme.paper_rail_html([])
    assert empty.startswith('<div class="tn-wl">')                 # 무행도 유효 마크업


def test_orderbook_ladder_html():
    """호가 사다리 — 잔량 바·전일比 등락%·현재가 강조·총잔량·당일/52주 패널."""
    bids = [[23125, 518], [23110, 827]]
    asks = [[23130, 1379], [23135, 6125]]
    h = theme.orderbook_ladder_html(
        bids, asks, prev_close=22130, price=23125,
        day={"open": 20590, "high": 24900, "low": 19900, "volume": 172650238},
        week52={"high": 44385, "low": 18665})
    assert "23,125" in h and "23,135" in h
    assert "+4.50%" in h and "+4.54%" in h            # 전일比 등락 (반올림)
    assert "판매대기" in h and "구매대기" in h          # 총잔량 비율 바
    assert "52주 최고" in h and "44,385" in h
    assert "172,650,238" in h                          # 거래량
    assert h.count("#3182f6") >= 3 and h.count("#f04452") >= 3   # 파랑=ask 잔량·빨강=bid 잔량
    assert "호가 없음" in theme.orderbook_ladder_html([], [])    # graceful


def test_market_tape_html_marquee():
    """하단 마퀴 띠 — 무한 스크롤 keyframes·내용 2벌 복제·고정 위치·색 시맨틱."""
    items = [{"label": "코스피", "value": 7293.43, "chg": -362.88, "pct": -4.73},
             {"label": "VIX", "value": 16.13, "chg": 0.56, "pct": 3.59}]
    h = theme.market_tape_html(items)
    assert "tn-tape-scroll" in h and "infinite" in h
    assert h.count("코스피") == 2 and h.count("VIX") == 2       # 이음새 없는 루프용 복제
    assert "position: fixed" in h and "bottom: 0" in h
    assert "▼" in h and "▲" in h
    assert "animation-play-state: paused" in h                  # hover 정지
    assert theme.market_tape_html([]) == ""                     # graceful


def test_etf_score_html_gauge():
    """ETF 점수 게이지 — 니들·5존·라벨·표본부족·None=데이터 부족 안내."""
    h = theme.etf_score_html(72, "나스닥 100")
    assert "<svg" in h and "<line" in h and h.count("<path") == 5   # 니들 + 5존
    assert "72" in h and "그룹 상위" in h and "나스닥 100" in h
    assert "표시·참고용" in h
    low = theme.etf_score_html(55, "금", low_confidence=True)
    assert "표본 부족" in low
    none_h = theme.etf_score_html(None)
    assert "데이터 부족" in none_h and "<svg" not in none_h
    assert "그룹 최상위" in theme.etf_score_html(90) and "그룹 최하위" in theme.etf_score_html(5)


def test_valuation_gauge_html():
    """가치평가 게이지 — 라벨 체계(고평가/저평가)·타이틀·verdict 존."""
    h = theme.valuation_gauge_html(0.7, sub="PEG 0.9 · 목표가 +25%")
    assert "저평가" in h and "고평가" in h                  # 끝 라벨
    assert "⚖️ 가치평가" in h and "PEG 0.9" in h
    assert "크게 저평가" in h                               # +0.7 → 최상위 존 verdict
    assert "크게 고평가" in theme.valuation_gauge_html(-0.9)
    assert "적정 수준" in theme.valuation_gauge_html(0.0)
    # 기본 rating 게이지 동작 불변 (기술적 분석)
    base = theme.rating_gauge_html(0.8)
    assert "강세" in base and "강력매수" in base


def test_position_band_html():
    """내 포지션 컴팩트 밴드 — 라벨·값·색·빈 입력 graceful."""
    h = theme.position_band_html([("평단", "$190.52", None),
                                  ("평가손익", "+3.4%", theme.GREEN)])
    assert "평단" in h and "$190.52" in h and theme.GREEN in h
    assert theme.position_band_html([]) == ""


def test_analysis_card_html():
    """기업 판단 요약 카드 — verdict 색 액센트·강점/주의 칩·다음확인 풋터·빈 목록 graceful."""
    h = theme.analysis_card_html("주의 우선", ["ROE 32.9%"], ["순마진 악화 -7.8%p"],
                                 ["다음 실적·가이던스 확인"])
    assert theme.RED in h and "주의 우선" in h              # verdict 색 액센트
    assert "ROE 32.9%" in h and "✔" in h
    assert "순마진 악화" in h and "⚠" in h
    assert "다음 확인" in h and "가이던스" in h
    assert "매매신호 아님" in h
    good = theme.analysis_card_html("양호", [], [], None)
    assert theme.GREEN in good and "특이 강점 없음" in good   # 빈 목록·풋터 생략
    assert "다음 확인" not in good


def test_css_render_progress_animations():
    """렌더링 진행감 — 스켈레톤 shimmer·stale 숨쉬기 keyframes 계약."""
    css = theme._CSS
    assert "stSkeleton" in css and "tn-shimmer" in css
    assert 'data-stale="true"' in css and "tn-breathe" in css
    assert css.count("@keyframes tn-shimmer") == 1 and css.count("@keyframes tn-breathe") == 1
