"""yfinance 최신 일봉 OHLC NaN — 캔들 SVG path NaN 으로 마지막 봉이 깨지던 문제 (2026-10-23 실측)."""
import math

import pandas as pd

from ohlc_utils import repair_display_ohlc

NAN = math.nan


def _frame(rows):
    idx = pd.to_datetime(["2026-10-21", "2026-10-22", "2026-10-23"][: len(rows)])
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


def test_close_only_latest_bar_gets_drawable_candle_from_previous_close():
    src = _frame([[529.4, 533.3, 525.0, 532.95, 1], [533.1, 533.6, 520.3, 521.08, 1],
                  [NAN, NAN, NAN, 524.51, 1]])

    out = repair_display_ohlc(src)

    last = out.iloc[-1]
    assert (last["Open"], last["High"], last["Low"], last["Close"]) == (521.08, 524.51, 521.08, 524.51)
    assert out.attrs["repaired_bars"] == 1 and out.attrs["dropped_bars"] == 0
    assert math.isnan(src["Open"].iloc[-1])          # 원본(ML·백테스트 경로)은 그대로


def test_bar_without_close_is_dropped_not_invented():
    src = _frame([[336.98, 337.27, 330.38, 334.76, 1], [330.46, 331.43, 322.0, 324.22, 1],
                  [NAN, NAN, NAN, NAN, 17_506_463]])

    out = repair_display_ohlc(src)

    assert list(out.index.strftime("%Y-%m-%d")) == ["2026-10-21", "2026-10-22"]
    assert not out[["Open", "High", "Low", "Close"]].isna().any().any()
    assert out.attrs["dropped_bars"] == 1


def test_complete_frame_is_returned_unchanged():
    src = _frame([[1.0, 2.0, 0.5, 1.5, 1], [1.5, 2.5, 1.0, 2.0, 1]])
    assert repair_display_ohlc(src) is src
