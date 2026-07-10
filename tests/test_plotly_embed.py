"""dashboard/plotly_embed.py 계약 테스트 — HTML 문자열(순수·iframe 내부는 AppTest 불가 대체)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("plotly")
import pandas as pd  # noqa: E402

from dashboard import charts, plotly_embed  # noqa: E402


def _hist(n=120):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": [100.0] * n, "High": [101.0] * n,
                         "Low": [99.0] * n, "Close": [100.0] * n,
                         "Volume": [1.0] * n}, index=idx)


def test_embed_html_contract():
    hist = _hist()
    fig = charts.price_chart(hist, "T", kind="candle", show_rsi=True)
    html = plotly_embed.pannable_chart_html(fig, hist, view_days=180)
    # 핵심 계약: relayout→y 자동맞춤·초기창·마커 클릭 상세·드로잉 도구·CDN 핀·fig 데이터
    for token in ("plotly_relayout", "function yFit", "plotly_click", "drawline",
                  "cdn.plot.ly/plotly-", "candlestick", "eraseshape"):
        assert token in html, f"누락: {token}"
    assert "${c[1]}" in html          # f-string 중괄호 이스케이프 무결(JS 템플릿 리터럴 보존)


def test_price_bounds_json():
    b = json.loads(plotly_embed.price_bounds_json(_hist(50)))
    # [ms, low, high, vol, open, close] — 6열 (🧲 자석 스냅에 OHLC 필요)
    assert len(b) == 50 and len(b[0]) == 6
    assert b[0][1] == 99.0 and b[0][2] == 101.0                 # low·high
    assert b[0][4] == 100.0 and b[0][5] == 100.0               # open·close
    assert b[1][0] - b[0][0] == 86400000                       # epoch ms 간격
    assert plotly_embed.price_bounds_json(None) == "[]"
    h = _hist(5)
    h.iloc[2, h.columns.get_loc("Low")] = float("nan")
    assert len(json.loads(plotly_embed.price_bounds_json(h))) == 4   # NaN 스킵


def test_embed_drawing_tools_contract():
    """드로잉 도구 — 자석·수평선·피보나치·측정·지우기 툴바 + 스냅/도형 로직 계약."""
    hist = _hist()
    fig = charts.price_chart(hist, "T", kind="candle")
    html = plotly_embed.pannable_chart_html(fig, hist)
    # 툴바 버튼
    for bid in ('id="bt-mag"', 'id="bt-hline"', 'id="bt-fib"', 'id="bt-meas"', 'id="bt-clear"'):
        assert bid in html, f"툴바 버튼 누락: {bid}"
    # 핵심 로직
    for token in ("function snapPoint", "function makeFib", "function makeMeasure",
                  "function makeHline", "function handleShapes", "FIB_LEVELS",
                  "reconcileHlineAnns", "const baseShapes",
                  "idx < baseShapeCount"):       # 서버 도형 보호 가드
        assert token in html, f"드로잉 로직 누락: {token}"


def test_embed_log_and_pct_flags():
    """로그·비교(%) 모드 플래그 임베드 + toY/fromY 변환 계약."""
    hist = _hist()
    fig = charts.price_chart(hist, "T", log_scale=True)
    html = plotly_embed.pannable_chart_html(fig, hist, y_log=True)
    assert "const yLog = true" in html
    assert "Math.log10" in html and "Math.pow(10" in html      # toY/fromY
    # 기본(off)
    norm = plotly_embed.pannable_chart_html(charts.price_chart(hist, "T"), hist)
    assert "const yLog = false" in norm and "const pctMode = false" in norm
    # 비교(%) 모드 플래그
    pct = plotly_embed.pannable_chart_html(fig, hist, pct_mode=True)
    assert "const pctMode = true" in pct


def test_embed_persistence_and_readout_contract():
    """드로잉 영속화(localStorage)·OHLC 데이터창 계약 — store_key 주입·저장/복원/키 규약."""
    hist = _hist()
    fig = charts.price_chart(hist, "T", kind="candle")
    html = plotly_embed.pannable_chart_html(fig, hist, store_key="NVDA:1d:lin")
    for token in ('"NVDA:1d:lin"', "function saveDrawings", "function loadDrawings",
                  "function scheduleSave", '"tndraw:" + storeKey',
                  'id="ohlcbar"', "function ohlcReadout", "plotly_hover", "plotly_unhover",
                  # DOM 크로스헤어 — plotly 스파이크 대신 (재그리기 0·rAF 스로틀)
                  "function xhApply", "requestAnimationFrame(xhApply)", "mouseleave"):
        assert token in html, f"누락: {token}"
    # store_key 미지정 = null → 비영속 (구형 호출 하위호환)
    norm = plotly_embed.pannable_chart_html(fig, hist)
    assert "const storeKey = null" in norm


def test_embed_view_position_contract():
    """뷰 위치 유지 — 60초 신선 + 기간 라디오(vm) 일치 시만 복원 (⚡자동갱신 무점프)."""
    hist = _hist()
    fig = charts.price_chart(hist, "T")
    html = plotly_embed.pannable_chart_html(fig, hist, view_days=90, store_key="NVDA:1d:lin")
    for token in ("function saveView", "function loadFreshView", '"tnview:" + storeKey',
                  "< 60000", "v.vm ===", "const freshView = loadFreshView()"):
        assert token in html, f"누락: {token}"
    # saveView 는 제스처 마무리(finishGesture)에서 **range 원문**으로 호출 —
    # Date 파싱-재직렬화 왕복은 KST 에서 −9h 밀림 (적대 리뷰 확정 버그의 회귀 방어)
    assert html.index("function finishGesture") < html.index("saveView(xr)")
    assert "new Date(freshView[0])" not in html          # 복원도 원문 그대로


def test_embed_no_unreplaced_tokens():
    """@@TOKEN@@ 치환 누락 없음 — 템플릿 전량 치환 (fig JSON 이 토큰 오염 안 함)."""
    hist = _hist()
    fig = charts.price_chart(hist, "T", kind="candle", show_volume=True, show_rsi=True)
    html = plotly_embed.pannable_chart_html(fig, hist, view_days=90, vol_axis="yaxis2")
    assert "@@" not in html


def test_embed_cdn_failure_notice():
    fig = charts.price_chart(_hist(), "T")
    html = plotly_embed.pannable_chart_html(fig, _hist())
    assert "CDN 로드 실패" in html                              # 폴백 안내 문구 포함


def test_embed_reserves_chart_height():
    fig = charts.price_chart(_hist(), "T", show_rsi=True, show_volume=True)
    html = plotly_embed.pannable_chart_html(fig, _hist(), height=612)
    assert "min-height:612px" in html
    assert "fig.layout.height = fitVH ? vhFit() : 612" in html   # 풀뷰 실측 분기


def test_embed_callouts_follow_pan():
    """최고/최저 콜아웃 팬 추종 계약 — name 태그·JS 핸들러·현재가 상수."""
    hist = _hist()
    fig = charts.price_chart(hist, "T", kind="candle", view_days=60)
    names = [a.name for a in fig.layout.annotations if a.name]
    assert "tn-hi" in names and "tn-lo" in names        # 서버측 태그
    html = plotly_embed.pannable_chart_html(fig, hist, view_days=60)
    for token in ("function callouts", 'anns[i].name === "tn-hi"', "const lastClose ="):
        assert token in html, f"누락: {token}"


def test_compare_bounds_json_pct_scale():
    """비교 프레임 — % 스케일·시간 정렬·메인 행만 거래량 탑재."""
    hist = _hist(40)                                   # Close=100 평평 → 메인 pct=0
    idx = hist.index
    cmp_s = pd.Series([50.0 + i for i in range(40)], index=idx)   # +78% 까지 상승
    b = json.loads(plotly_embed.compare_bounds_json(hist, {"C": cmp_s}, None))
    assert len(b) == 80                                 # 메인 40 + 비교 40
    assert all(b[i][0] <= b[i + 1][0] for i in range(len(b) - 1))   # ms 오름차순
    assert max(r[2] for r in b) < 90                    # % 스케일 (가격 100·50 아님)
    assert any(abs(r[2] - 78.0) < 1e-6 for r in b)      # 비교 마지막 = (89/50-1)=+78%
    vols = [r[3] for r in b if r[3] > 0]
    assert len(vols) == 40                              # 거래량은 메인 행에만


def test_pannable_bounds_override():
    """bounds_json 오버라이드가 임베드 JS 프레임을 대체 (비교 모드 % y-fit)."""
    hist = _hist(30)
    fig = charts.price_chart(hist, "T")
    html = plotly_embed.pannable_chart_html(fig, hist, bounds_json="[[1,2.5,2.5,0]]")
    assert "[[1, 2.5, 2.5, 0]]" in html or "[[1,2.5,2.5,0]]" in html
    assert '"99.0"' not in html


def test_embed_drag_ux_contract():
    """드래그 UX — 실시간 y-follow·lerp·y 고정·이진탐색·hover 중지 계약."""
    hist = _hist(60)
    fig = charts.price_chart(hist, "T", show_rsi=True, show_volume=True)
    html = plotly_embed.pannable_chart_html(fig, hist, view_days=30, vol_axis="yaxis2")
    for token in ("plotly_relayouting",          # 드래그 중 이벤트 — 종료 스냅 제거
                  "requestAnimationFrame",       # rAF lerp 루프
                  "fixedrange = true",           # y축 사용자 팬 고정 (y 싸움 제거)
                  "function lowerBound",         # bounds 이진 탐색
                  "hovermode: false",            # 드래그 중 hover 중지
                  "costMs",                      # 리드로우 비용 EMA — 적응형 스로틀
                  "visDelta",                    # 데드밴드 — 무의미한 relayout 생략
                  "performance.now",
                  "const volCap",                # 거래량 q98 캡 — 스파이크 축 지배 방지
                  "function finishGesture",      # 제스처 끝 1회 마무리 (휠 줌 랙 제거)
                  "gestureTimer = setTimeout(finishGesture, 160)",
                  "function animStep"):
        assert token in html, f"누락: {token}"


def test_embed_fit_viewport_contract():
    """풀뷰 — 부모 창 높이 실측 리사이즈(frameElement)·리사이즈 추종 계약."""
    hist = _hist(40)
    fig = charts.price_chart(hist, "T")
    full = plotly_embed.pannable_chart_html(fig, hist, fit_viewport=True)
    for token in ("const fitVH = true", "window.frameElement",
                  "parent.innerHeight", 'addEventListener("resize"'):
        assert token in full, f"누락: {token}"
    norm = plotly_embed.pannable_chart_html(fig, hist)
    assert "const fitVH = false" in norm
