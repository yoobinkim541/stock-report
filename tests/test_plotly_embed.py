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
    assert len(b) == 50 and b[0][1] == 99.0 and b[0][2] == 101.0
    assert b[1][0] - b[0][0] == 86400000                       # epoch ms 간격
    assert plotly_embed.price_bounds_json(None) == "[]"
    h = _hist(5)
    h.iloc[2, h.columns.get_loc("Low")] = float("nan")
    assert len(json.loads(plotly_embed.price_bounds_json(h))) == 4   # NaN 스킵


def test_embed_cdn_failure_notice():
    fig = charts.price_chart(_hist(), "T")
    html = plotly_embed.pannable_chart_html(fig, _hist())
    assert "CDN 로드 실패" in html                              # 폴백 안내 문구 포함


def test_embed_callouts_follow_pan():
    """최고/최저 콜아웃 팬 추종 계약 — name 태그·JS 핸들러·현재가 상수."""
    hist = _hist()
    fig = charts.price_chart(hist, "T", kind="candle", view_days=60)
    names = [a.name for a in fig.layout.annotations if a.name]
    assert "tn-hi" in names and "tn-lo" in names        # 서버측 태그
    html = plotly_embed.pannable_chart_html(fig, hist, view_days=60)
    for token in ("function callouts", 'anns[i].name === "tn-hi"', "const lastClose ="):
        assert token in html, f"누락: {token}"
