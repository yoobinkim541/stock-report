from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


def _df(closes, volumes=None, tz="Asia/Seoul"):
    idx = pd.date_range(
        datetime(2026, 7, 22, 9, 30, tzinfo=ZoneInfo(tz)),
        periods=len(closes),
        freq="min",
    )
    rows = []
    volumes = volumes or [1000] * len(closes)
    for close, vol in zip(closes, volumes):
        rows.append(
            {
                "Open": close - 1,
                "High": close + 2,
                "Low": close - 2,
                "Close": close,
                "Volume": vol,
            }
        )
    return pd.DataFrame(rows, index=idx)


def test_detects_opening_range_breakout():
    from ml.intraday_sample_factory import detect_setups

    bars = _df([100, 101, 102, 103, 104, 105, 106, 107, 108, 112], [100] * 9 + [5000])
    axes = {"orb": 1.0, "vwap": 0.8, "volspike": 1.0, "_meta": {"close": 112.0, "atr": 1.5}}

    setups = detect_setups(axes, bars, market="KR")

    assert [row["setup_type"] for row in setups] == [
        "opening_range_breakout",
        "vwap_reclaim",
        "volume_shock",
    ]
    assert setups[0]["expected_move"] > 0
    assert setups[0]["confirm_bars"] >= 1


def test_classify_sample_observe_micro_normal_thresholds():
    from ml.intraday_sample_factory import classify_sample

    setup = {"setup_type": "vwap_reclaim", "expected_move": 100.0}

    observe = classify_sample(setup, market="KR", confirm_bars=0, expected_move=100.0, estimated_cost=60.0)
    micro = classify_sample(setup, market="KR", confirm_bars=1, expected_move=100.0, estimated_cost=40.0)
    normal = classify_sample(setup, market="KR", confirm_bars=2, expected_move=100.0, estimated_cost=20.0)

    assert observe["sample_mode"] == "observe_only"
    assert "confirm_bars_lt_1" in observe["blocked_by"]
    assert micro["sample_mode"] == "micro"
    assert normal["sample_mode"] == "normal"
    assert normal["cost_ratio"] == 5.0


def test_candidate_id_is_deterministic_and_market_scoped():
    from ml.intraday_sample_factory import candidate_id

    assert candidate_id("2026-07-22", "KR", "005930", 12345, "vwap_reclaim") == "2026-07-22:KR:005930:12345:vwap_reclaim"
    assert candidate_id("2026-07-22", "us", "TQQQ", 12345, "volume_shock") == "2026-07-22:US:TQQQ:12345:volume_shock"
