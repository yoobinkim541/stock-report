from __future__ import annotations

import math

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from dashboard import chart_transforms as transforms


@pytest.fixture
def ohlc() -> pd.DataFrame:
    index = pd.date_range("2026-01-02", periods=16, freq="D")
    close = pd.Series(
        [100.0, 101.0, 103.0, 99.0, 104.0, 109.0, 102.0, 96.0,
         101.0, 107.0, 112.0, 106.0, 99.0, 103.0, 110.0, 118.0],
        index=index,
    )
    open_ = close.shift(1).fillna(close.iloc[0])
    return pd.DataFrame(
        {
            "Open": open_,
            "High": pd.concat([open_, close], axis=1).max(axis=1) + 1.0,
            "Low": pd.concat([open_, close], axis=1).min(axis=1) - 1.0,
            "Close": close,
            "Volume": range(100, 116),
        },
        index=index,
    )


def test_available_chart_types_match_document_vocabulary():
    assert transforms.available_chart_types() == (
        "line", "area", "baseline", "candlestick", "hollow_candle",
        "heikin_ashi", "bars", "high_low", "renko", "kagi", "line_break", "range",
    )


@pytest.mark.parametrize(
    ("chart_type", "render_kind"),
    [
        ("line", "line"),
        ("area", "line"),
        ("baseline", "line"),
        ("candlestick", "candlestick"),
        ("hollow_candle", "candlestick"),
        ("bars", "candlestick"),
        ("high_low", "candlestick"),
    ],
)
def test_standard_chart_modes_copy_normalized_ohlc_without_synthesis(ohlc, chart_type, render_kind):
    original = ohlc.copy(deep=True)

    out = transforms.transform_chart(ohlc, chart_type)

    assert out.render_kind == render_kind
    assert out.x_mode == "time"
    assert out.synthetic is False
    assert out.metadata["chart_type"] == chart_type
    assert_frame_equal(out.frame, ohlc)
    assert out.frame is not ohlc
    assert_frame_equal(ohlc, original)


def test_heikin_ashi_recalculates_ohlc_without_mutating_source(ohlc):
    original = ohlc.copy(deep=True)

    out = transforms.transform_chart(ohlc, "heikin_ashi")

    assert out.render_kind == "candlestick"
    assert out.synthetic is True
    assert out.x_mode == "time"
    assert out.frame["Close"].iloc[0] == pytest.approx(
        (ohlc.iloc[0].Open + ohlc.iloc[0].High + ohlc.iloc[0].Low + ohlc.iloc[0].Close) / 4.0
    )
    assert out.frame["Open"].iloc[1] == pytest.approx(
        (out.frame["Open"].iloc[0] + out.frame["Close"].iloc[0]) / 2.0
    )
    assert "Volume" in out.frame
    assert_frame_equal(ohlc, original)


def test_renko_emits_fixed_size_bricks_with_source_timestamps(ohlc):
    out = transforms.transform_chart(ohlc, "renko", {"box_size": 2.0})
    assert out.synthetic is True
    assert out.render_kind == "candlestick"
    assert set(out.frame.columns) >= {"Open", "High", "Low", "Close", "SourceTimestamp"}
    assert set((out.frame["Close"] - out.frame["Open"]).abs().round(8)) == {2.0}
    assert out.frame.index.is_monotonic_increasing


@pytest.mark.parametrize(
    ("chart_type", "parameter", "size", "expected"),
    [("renko", "box_size", 2.0, 4), ("range", "range_size", 2.5, 3)],
)
def test_close_crossing_multiple_boundaries_emits_each_synthetic_element(chart_type, parameter, size, expected):
    index = pd.date_range("2026-01-02", periods=2, freq="D")
    hist = pd.DataFrame(
        {"Open": [100.0, 100.0], "High": [100.0, 108.0], "Low": [100.0, 100.0], "Close": [100.0, 108.0]},
        index=index,
    )

    out = transforms.transform_chart(hist, chart_type, {parameter: size})

    assert len(out.frame) == expected
    assert out.frame.index.tolist() == list(range(expected))
    assert out.frame["SourceTimestamp"].tolist() == [index[1]] * expected
    assert out.frame["Close"].iloc[-1] == pytest.approx(100.0 + expected * size)


def test_kagi_reverses_only_after_configured_amount(ohlc):
    out = transforms.transform_chart(ohlc, "kagi", {"reversal": 3.0})
    assert out.render_kind == "line"
    assert out.synthetic is True
    assert out.metadata["reversal"] == 3.0
    assert len(out.frame) < len(ohlc)


def test_line_break_uses_previous_three_lines(ohlc):
    out = transforms.transform_chart(ohlc, "line_break", {"lines": 3})
    assert out.metadata["lines"] == 3
    assert out.render_kind == "candlestick"
    assert not out.frame.index.duplicated().any()


def test_range_bars_have_exact_range_and_no_time_claim(ohlc):
    out = transforms.transform_chart(ohlc, "range", {"range_size": 2.5})
    assert out.x_mode == "sequence"
    assert (out.frame["High"] - out.frame["Low"] >= 2.5 - 1e-9).all()
    assert out.metadata["source_precision"] == "ohlcv_close_path"


@pytest.mark.parametrize("chart_type", transforms.available_chart_types())
def test_empty_frames_are_supported(chart_type):
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    out = transforms.transform_chart(empty, chart_type)

    assert out.frame.empty
    assert out.frame is not empty


def test_unsorted_duplicate_input_is_normalized_without_mutating_caller():
    index = pd.to_datetime(["2026-01-03", "2026-01-02", "2026-01-03"])
    hist = pd.DataFrame(
        {
            "Open": [103.0, 100.0, 102.0],
            "High": [104.0, 102.0, 105.0],
            "Low": [101.0, 99.0, 100.0],
            "Close": [102.0, 101.0, 104.0],
            "Volume": [5, 10, 7],
        },
        index=index,
    )
    original = hist.copy(deep=True)

    out = transforms.transform_chart(hist, "candlestick")

    assert out.frame.index.is_monotonic_increasing
    assert out.frame.index.is_unique
    assert out.frame.loc[pd.Timestamp("2026-01-03"), "Open"] == 103.0
    assert out.frame.loc[pd.Timestamp("2026-01-03"), "Close"] == 104.0
    assert out.frame.loc[pd.Timestamp("2026-01-03"), "Volume"] == 12
    assert_frame_equal(hist, original)


def test_atr_derived_defaults_use_latest_finite_atr(ohlc):
    prev_close = ohlc["Close"].shift(1)
    true_range = pd.concat(
        [ohlc["High"] - ohlc["Low"], (ohlc["High"] - prev_close).abs(), (ohlc["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    expected = float(true_range.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])

    assert transforms.transform_chart(ohlc, "renko").metadata["box_size"] == pytest.approx(expected)
    assert transforms.transform_chart(ohlc, "kagi").metadata["reversal"] == pytest.approx(expected)
    assert transforms.transform_chart(ohlc, "range").metadata["range_size"] == pytest.approx(expected)


def test_atr_default_falls_back_to_one_percent_of_latest_close():
    hist = pd.DataFrame(
        {"Open": [200.0], "High": [200.0], "Low": [200.0], "Close": [200.0]},
        index=pd.DatetimeIndex(["2026-01-02"]),
    )

    assert transforms.transform_chart(hist, "renko").metadata["box_size"] == pytest.approx(2.0)
    assert transforms.transform_chart(hist, "kagi").metadata["reversal"] == pytest.approx(2.0)
    assert transforms.transform_chart(hist, "range").metadata["range_size"] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("chart_type", "params"),
    [
        ("renko", {"box_size": 0}),
        ("renko", {"box_size": math.inf}),
        ("kagi", {"reversal": -1}),
        ("range", {"range_size": 0}),
        ("line_break", {"lines": 0}),
        ("line_break", {"lines": 1.5}),
    ],
)
def test_nonpositive_or_invalid_parameters_are_rejected(ohlc, chart_type, params):
    with pytest.raises(ValueError):
        transforms.transform_chart(ohlc, chart_type, params)


def test_unknown_chart_type_is_rejected(ohlc):
    with pytest.raises(ValueError, match="unsupported chart type"):
        transforms.transform_chart(ohlc, "footprint")
