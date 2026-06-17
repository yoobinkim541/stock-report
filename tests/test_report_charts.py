"""tests/test_report_charts.py — 포트폴리오 대시보드 PNG 생성 무네트워크 테스트.

matplotlib(Agg) 렌더링만 검증 — yfinance 호출 없이 합성 clean_data + price_history 주입.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import report_charts as rc


def _synth_prices(n=40, start=100.0, drift=0.003, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(start * np.cumprod(1 + rng.normal(drift, 0.012, n)), index=idx)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": pd.Series(rng.uniform(1e6, 5e6, n), index=idx),
    })


def _clean_data():
    return {
        "date": "2026-06-17",
        "portfolio_summary": [
            {"ticker": "MSFT", "company": "Microsoft", "score": 82, "grade": "A",
             "change_1d_pct": -1.2, "change_1mo_pct": 3.4},
            {"ticker": "NVDA", "company": "NVIDIA", "score": 71, "grade": "B",
             "change_1d_pct": 0.8, "change_1mo_pct": -5.1},
            {"ticker": "GOOGL", "company": "Alphabet", "score": 60, "grade": "B",
             "change_1d_pct": 0.2, "change_1mo_pct": 1.0},
        ],
        "institutional_accumulation": [
            {"ticker": "AMD", "company": "Advanced Micro Devices", "accum_score": 80, "verdict": "강한 매집"},
            {"ticker": "005930.KS", "company": "삼성전자", "accum_score": 72, "verdict": "매집"},
        ],
    }


def _price_history(tickers):
    return {t: _synth_prices(seed=i) for i, t in enumerate(tickers)}


def test_build_dashboard_creates_valid_png(tmp_path):
    from PIL import Image
    cd = _clean_data()
    ph = _price_history(["MSFT", "NVDA", "GOOGL", "SPY", "QQQ"])
    out = str(tmp_path / "dash.png")

    result = rc.build_portfolio_dashboard(
        cd, {"spy_change": 0.1, "qqq_change": 0.2}, out,
        price_history=ph, date_str="2026-06-17")

    assert result == out
    assert os.path.exists(out)
    assert os.path.getsize(out) > 5000           # 빈 이미지가 아님
    with Image.open(out) as im:                    # 유효한 PNG
        assert im.format == "PNG"
        assert im.size[0] > 400 and im.size[1] > 300


def test_build_dashboard_survives_missing_price_history(tmp_path):
    # 가격 히스토리 비어도(가격 패널 실패) 등락률·점수 패널로 PNG 는 생성돼야 함
    cd = _clean_data()
    out = str(tmp_path / "dash2.png")

    result = rc.build_portfolio_dashboard(
        cd, {}, out, price_history={}, date_str="2026-06-17")

    assert result == out
    assert os.path.exists(out)


def test_build_dashboard_returns_none_on_empty_portfolio(tmp_path):
    out = str(tmp_path / "dash3.png")
    result = rc.build_portfolio_dashboard(
        {"portfolio_summary": []}, {}, out, price_history={})
    assert result is None
    assert not os.path.exists(out)


def test_rsi_helper_bounds():
    # 단조 상승 → RSI 높음, 단조 하락 → RSI 낮음, 범위 0~100
    up = pd.Series(np.linspace(100, 130, 40))
    down = pd.Series(np.linspace(130, 100, 40))
    r_up, r_down = rc._rsi(up), rc._rsi(down)
    assert r_up is not None and r_down is not None
    assert 0 <= r_down < r_up <= 100
    assert r_up > 70 and r_down < 30
