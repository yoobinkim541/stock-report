"""dashboard/trendlines.py 단위 테스트 — 순수 감지 알고리즘 (plotly/streamlit 불필요).

합성 OHLC(seed 42 고정)로 결정적 검증: 채널 라벨·지지/저항 터치·이탈 기각·봉단위 불변.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("scipy")
from dashboard import trendlines as tl  # noqa: E402


def _ohlc(close, spread=1.0, index=None):
    """종가열 → OHLC (고저 = 종가 ± spread)."""
    close = np.asarray(close, dtype=float)
    idx = index if index is not None else pd.date_range("2024-01-01", periods=len(close), freq="D")
    return pd.DataFrame({"Open": close, "High": close + spread,
                         "Low": close - spread, "Close": close,
                         "Volume": np.ones(len(close))}, index=idx)


def _uptrend(n=300, drift=0.002, noise=0.8, seed=42):
    rng = np.random.default_rng(seed)
    return _ohlc(100 * np.exp(drift * np.arange(n)) + rng.normal(0, noise, n))


def test_uptrend_channel_labeled_up():
    out = tl.detect_trendlines(_uptrend(), lines=False)
    chans = [o for o in out if o["kind"] == "channel"]
    assert chans, "채널 미감지"
    for ch in chans:
        assert ch["meta"]["trend"] == "up"
        assert ch["meta"]["slope_per_bar"] > 0
        assert ch["upper"][0] > ch["lower"][0] and ch["upper"][1] > ch["lower"][1]


def test_flat_series_channel_flat():
    rng = np.random.default_rng(42)
    out = tl.detect_trendlines(_ohlc(100 + rng.normal(0, 1, 300)), lines=False)
    for ch in (o for o in out if o["kind"] == "channel"):
        assert ch["meta"]["trend"] == "flat"


def test_v_reversal_has_rising_support():
    n = 240
    down = np.linspace(100, 80, n // 2)
    up = np.linspace(80, 110, n - n // 2)
    rng = np.random.default_rng(42)
    close = np.concatenate([down, up]) + rng.normal(0, 0.4, n)
    out = tl.detect_trendlines(_ohlc(close), channels=())
    sup = [o for o in out if o["kind"] == "support"]
    assert any(o["meta"]["slope_per_bar"] > 0 for o in sup), "V 반전 후 상승 지지선 미감지"


def _three_touch_frame(slope=0.5, touch_at=(20, 90, 160), n=200):
    """Low 가 y=100+slope·x 선을 지정 봉에서만 터치, 그 외는 위 — 결정적 지지선."""
    x = np.arange(n, dtype=float)
    line = 100 + slope * x
    rng = np.random.default_rng(42)
    low = line + 2.5 + np.abs(rng.normal(0, 0.6, n))     # 평소엔 선 위
    for t in touch_at:
        low[t] = line[t]                                  # 정확 터치 (피벗 저점이 되게 주변보다 낮음)
        low[t - 1] = line[t] + 2.0
        low[t + 1] = line[t] + 2.0
    close = low + 1.2
    high = close + 1.0
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": np.ones(n)},
                        index=pd.date_range("2024-01-01", periods=n, freq="D"))


def test_exact_three_touch_support_detected():
    df = _three_touch_frame()
    out = tl.detect_trendlines(df, channels=())
    sup = [o for o in out if o["kind"] == "support"]
    assert sup, "지지선 미감지"
    best = max(sup, key=lambda o: o["touches"])
    assert best["touches"] >= 3
    assert best["meta"]["slope_per_bar"] == pytest.approx(0.5, abs=0.1)


def test_broken_support_rejected():
    df = _three_touch_frame()
    atr = 2.0
    x = np.arange(len(df), dtype=float)
    line = 100 + 0.5 * x
    df.loc[df.index[-5]:, "Close"] = line[-5:] - 3 * atr    # 마지막 5봉 종가가 선 아래 관통
    df.loc[df.index[-5]:, "Low"] = line[-5:] - 3.5 * atr
    out = tl.detect_trendlines(df, channels=())
    for o in out:
        if o["kind"] == "support":
            assert not (o["meta"]["slope_per_bar"] == pytest.approx(0.5, abs=0.05)
                        and o["touches"] >= 3), "깨진 지지선이 반환됨"


def test_insufficient_data_empty():
    assert tl.detect_trendlines(_uptrend(10)) == []
    assert tl.detect_trendlines(None) == []
    assert tl.detect_trendlines(pd.DataFrame()) == []


def test_timeframe_invariance():
    close = _uptrend(200)["Close"].values
    d1 = tl.detect_trendlines(_ohlc(close, index=pd.date_range(
        "2024-01-01", periods=200, freq="D")))
    m5 = tl.detect_trendlines(_ohlc(close, index=pd.date_range(
        "2024-01-01 09:00", periods=200, freq="5min")))
    assert len(d1) == len(m5)
    for a, b in zip(d1, m5):
        assert a["y0"] == pytest.approx(b["y0"]) and a["y1"] == pytest.approx(b["y1"])
        assert a["touches"] == b["touches"]


def test_tz_aware_index_ok():
    idx = pd.date_range("2024-01-01 09:30", periods=300, freq="D", tz="US/Eastern")
    out = tl.detect_trendlines(_uptrend(300).set_axis(idx))
    assert out
    assert out[0]["x0"].tz is not None                       # tz 보존


def test_output_cap_and_dedup():
    rng = np.random.default_rng(42)
    out = tl.detect_trendlines(_ohlc(100 + np.cumsum(rng.normal(0, 1, 400))))
    assert len(out) <= 6
    sups = [o for o in out if o["kind"] == "support"]
    assert len(sups) <= 2


def test_deterministic():
    df = _uptrend(300)
    a, b = tl.detect_trendlines(df), tl.detect_trendlines(df)
    assert len(a) == len(b)
    for x, y in zip(a, b):
        assert x["y0"] == y["y0"] and x["y1"] == y["y1"] and x["label"] == y["label"]


def test_nan_and_unsorted_index():
    df = _uptrend(120)
    df.iloc[10, df.columns.get_loc("Close")] = np.nan
    shuffled = df.sample(frac=1, random_state=1)             # 비정렬
    out = tl.detect_trendlines(shuffled)
    assert isinstance(out, list)                              # 무예외 + 정렬 처리


def test_long_channel_log_path_fallback():
    """월봉급 장기 복리(총 이동 >25%) → path 폴리라인 폴백."""
    n = 250
    close = 100 * np.exp(0.004 * np.arange(n))               # 총 e^1 ≈ +171%
    out = tl.detect_trendlines(_ohlc(close, spread=0.5), lines=False)
    ch = [o for o in out if o["kind"] == "channel"][0]
    assert ch["path"] is not None
    assert len(ch["path"]["x"]) == len(ch["path"]["upper"]) >= 10
