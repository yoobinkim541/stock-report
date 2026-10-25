from __future__ import annotations

import math

import pandas as pd
import pytest

from dashboard import chart_studies


@pytest.fixture
def hist():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    close = pd.Series(range(100, 140), index=idx, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": range(1000, 1040),
        },
        index=idx,
    )


def test_study_catalog_is_registered_data_not_runtime_source():
    catalog = chart_studies.study_catalog()
    ids = {definition.id for definition in catalog}

    assert {"sma", "ema", "bollinger", "rsi", "macd", "volume", "vwap"} <= ids
    assert all(callable(definition.compute) for definition in catalog)
    assert all("type" in spec and "default" in spec for definition in catalog for spec in definition.parameters.values())


def test_study_registry_rejects_unknown_and_invalid_parameters(hist):
    with pytest.raises(ValueError, match="unknown study"):
        chart_studies.run_study("python_eval", hist, {})
    with pytest.raises(ValueError, match="period"):
        chart_studies.run_study("sma", hist, {"period": 0})
    with pytest.raises(ValueError, match="unknown parameter"):
        chart_studies.run_study("sma", hist, {"source_code": "lambda x: x"})


def test_registered_studies_return_typed_outputs(hist):
    sma = chart_studies.run_study("sma", hist, {"period": 5})
    rsi = chart_studies.run_study("rsi", hist)

    assert sma.placement == "overlay"
    assert sma.series["SMA 5"].iloc[-1] == pytest.approx(137.0)
    assert rsi.placement == "bottom"
    assert set(rsi.series) == {"RSI 14"}
    assert sma.metadata["study_id"] == "sma"


def test_strategy_preview_accepts_only_safe_plot_event_data():
    preview = {
        "plots": [
            {
                "name": "Signal MA",
                "dates": ["2026-01-01", "2026-01-02"],
                "values": [100.0, 101.5],
                "placement": "overlay",
            },
        ],
        "events": [{"date": "2026-01-02", "kind": "entry", "price": 101.5, "label": "Buy"}],
        "metadata": {"strategy": "safe-preview", "version": 1},
    }

    out = chart_studies.study_output_from_strategy_preview(preview)

    assert out.series["Signal MA"].iloc[-1] == pytest.approx(101.5)
    assert out.events[0]["kind"] == "entry"
    assert out.metadata["strategy"] == "safe-preview"


@pytest.mark.parametrize(
    "preview",
    [
        {"plots": [], "events": [], "metadata": {}, "code": "import os"},
        {"plots": [{"name": "x", "dates": ["2026-01-01"], "values": [1, 2], "placement": "overlay"}]},
        {"plots": [{"name": "x", "dates": ["2026-01-01"], "values": [math.inf], "placement": "overlay"}]},
        {"plots": [], "metadata": {"source": "lambda x: x"}},
    ],
)
def test_strategy_preview_rejects_code_shapes_and_invalid_values(preview):
    with pytest.raises(ValueError):
        chart_studies.study_output_from_strategy_preview(preview)


# --- C-09 / C-10 / C-12: 지표 단일 진실원 회귀 ---------------------------------

def _series(values, freq="h"):
    index = pd.date_range("2026-08-01", periods=len(values), freq=freq, tz="UTC")
    return pd.Series(values, index=index, dtype=float)


def test_rsi_edge_policies_rising_falling_flat_and_short_sample():
    rising = chart_studies.rsi(_series(range(100, 200)))
    falling = chart_studies.rsi(_series(range(200, 100, -1)))
    flat = chart_studies.rsi(_series([50.0] * 30))
    short = chart_studies.rsi(_series(range(100, 110)))

    assert rising.iloc[-1] == pytest.approx(100.0)
    assert falling.iloc[-1] == pytest.approx(0.0)
    assert flat.iloc[-1] == pytest.approx(chart_studies.RSI_FLAT)
    assert short.isna().all(), "표본 부족은 neutral 값이 아니라 계산 불가(NaN)여야 한다"
    assert chart_studies.rsi_zone(rising.iloc[-1]) == "overbought"
    assert chart_studies.rsi_zone(falling.iloc[-1]) == "oversold"
    assert chart_studies.rsi_zone(flat.iloc[-1]) == "neutral"
    assert chart_studies.rsi_zone(short.iloc[-1]) == "unavailable"
    assert chart_studies.rsi_zone(None) == "unavailable"


def test_rsi_matches_hand_computed_wilder_values():
    # deltas +1, -1, +2 with length=2 (alpha=0.5): avg gain 0.5 → 1.25, avg loss 0.5 → 0.25
    values = chart_studies.rsi(_series([10.0, 11.0, 10.0, 12.0]), length=2)

    assert math.isnan(values.iloc[1])
    assert values.iloc[2] == pytest.approx(50.0)
    assert values.iloc[3] == pytest.approx(100 - 100 / (1 + 1.25 / 0.25))


def test_rsi_study_uses_shared_definition(hist):
    study = chart_studies.run_study("rsi", hist).series["RSI 14"]

    pd.testing.assert_series_equal(study, chart_studies.rsi(hist["Close"], 14), check_names=False)
    assert study.iloc[-1] == pytest.approx(100.0)


def _vwap_frame(index, closes, volumes):
    return pd.DataFrame(
        {"high": closes, "low": closes, "close": closes, "volume": volumes},
        index=pd.DatetimeIndex(index),
    )


def test_session_vwap_resets_each_exchange_day():
    frame = _vwap_frame(
        ["2026-08-03 14:00", "2026-08-03 15:00", "2026-08-04 14:00", "2026-08-04 15:00"],
        [10.0, 10.0, 100.0, 100.0], [100, 100, 100, 100],
    )
    frame.index = frame.index.tz_localize("UTC")

    values = chart_studies.session_vwap(frame, "America/New_York")

    assert list(values) == pytest.approx([10.0, 10.0, 100.0, 100.0])
    assert values.index.equals(frame.index)


def test_session_vwap_uses_exchange_timezone_not_utc_date():
    # 15:00 ET 와 20:30 ET(다음 UTC 날짜) 는 같은 뉴욕 거래일 세션이다.
    frame = _vwap_frame(
        ["2026-08-03 19:00", "2026-08-04 00:30", "2026-08-04 13:30"],
        [10.0, 20.0, 50.0], [100, 100, 100],
    )
    frame.index = frame.index.tz_localize("UTC")

    values = chart_studies.session_vwap(frame, "America/New_York")

    assert list(values) == pytest.approx([10.0, 15.0, 50.0])


def test_session_vwap_zero_volume_session_is_unavailable_and_study_matches():
    frame = _vwap_frame(
        ["2026-08-03", "2026-08-04", "2026-08-04 01:00"], [10.0, 100.0, 100.0], [100, 0, 0],
    )
    values = chart_studies.session_vwap(frame, "Asia/Seoul")
    assert values.iloc[0] == pytest.approx(10.0)
    assert values.iloc[1:].isna().all()

    titled = frame.rename(columns=str.title)
    titled["Open"] = titled["Close"]
    study = chart_studies.run_study("vwap", titled).series["VWAP"]
    assert study.iloc[0] == pytest.approx(10.0)
    assert study.iloc[1:].isna().all()


def test_relative_performance_aligns_by_trading_day_not_timestamp():
    days = pd.bdate_range("2026-01-01", periods=100)
    asset = pd.Series([100.0 * 1.01 ** i for i in range(100)], index=days + pd.Timedelta(hours=14))
    asset.index = asset.index.tz_localize("UTC")
    benchmark = pd.Series([100.0] * 100, index=days)

    table = chart_studies.relative_performance(asset, benchmark)

    assert len(table) == 100
    assert table["relative_return_20d"].iloc[-1] == pytest.approx(1.01 ** 20 - 1)
    assert table["relative_return_60d"].iloc[-1] == pytest.approx(1.01 ** 60 - 1)
    assert table["relative_momentum_20d"].iloc[-1] == pytest.approx((1.01 ** 20) - (1.01 ** 60))
