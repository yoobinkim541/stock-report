from datetime import datetime

import pandas as pd
import pytest

from dashboard import chart_data_policy as policy


def _bars(index):
    return pd.DataFrame(
        {
            "Open": range(100, 100 + len(index)),
            "High": range(101, 101 + len(index)),
            "Low": range(99, 99 + len(index)),
            "Close": range(100, 100 + len(index)),
            "Volume": [10] * len(index),
        },
        index=index,
    )


def test_us_regular_session_filters_intraday_in_exchange_timezone():
    # The Monday after the 2024 US DST change remains a 09:30--16:00 local session.
    hist = _bars(pd.DatetimeIndex([
        "2024-03-11 09:25:00-04:00", "2024-03-11 09:30:00-04:00",
        "2024-03-11 16:00:00-04:00", "2024-03-11 16:05:00-04:00",
    ]))

    out = policy.apply_session_policy(
        hist, market="us", timeframe="5m", policy="regular", timezone="America/New_York"
    )

    local = out.frame.index.tz_convert("America/New_York")
    assert list(local.strftime("%H:%M")) == ["09:30", "16:00"]
    assert out.metadata["excluded_bars"] == 2
    assert out.metadata["timezone"] == "America/New_York"
    assert out.metadata["timezone_assumption"] is False


def test_kr_regular_session_includes_exchange_boundaries():
    hist = _bars(pd.DatetimeIndex([
        "2024-03-11 08:55:00+09:00", "2024-03-11 09:00:00+09:00",
        "2024-03-11 15:30:00+09:00", "2024-03-11 15:35:00+09:00",
    ]))

    out = policy.apply_session_policy(hist, market="kr", timeframe="5m", policy="regular")

    assert list(out.frame.index.strftime("%H:%M")) == ["09:00", "15:30"]
    assert out.metadata["exchange_session"] == {"open": "09:00", "close": "15:30"}


@pytest.mark.parametrize("policy_name", ["extended", "all"])
def test_non_regular_intraday_policies_retain_provider_bars(policy_name):
    hist = _bars(pd.DatetimeIndex([
        "2024-03-11 09:25:00-04:00", "2024-03-11 09:30:00-04:00",
        "2024-03-11 16:05:00-04:00",
    ]))

    out = policy.apply_session_policy(hist, market="us", timeframe="5m", policy=policy_name)

    assert out.frame.equals(hist)
    assert out.metadata["excluded_bars"] == 0
    assert out.metadata["provider_coverage"] == "may_be_incomplete"


@pytest.mark.parametrize("timeframe", ["1d", "1wk", "1mo"])
def test_daily_or_higher_bypasses_session_time_filtering(timeframe):
    hist = _bars(pd.DatetimeIndex(["2024-03-09", "2024-03-10", "2024-03-11"]))

    out = policy.apply_session_policy(hist, market="us", timeframe=timeframe, policy="regular")

    assert out.frame.equals(hist)
    assert out.metadata["decision"] == "timeframe_bypass"
    assert out.metadata["excluded_bars"] == 0


def test_daily_naive_timestamps_keep_provenance_uncertainty_without_time_filtering():
    hist = _bars(pd.DatetimeIndex(["2024-03-09", "2024-03-10", "2024-03-11"]))

    out = policy.apply_session_policy(hist, market="us", timeframe="1d", policy="regular")

    assert out.frame.equals(hist)
    assert out.metadata["decision"] == "timeframe_bypass"
    assert out.metadata["timezone_assumption"] is True
    assert out.metadata["provider_timezone"] is None


def test_naive_provider_timestamps_are_marked_uncertain_without_timezone_metadata():
    hist = _bars(pd.date_range("2024-03-11 09:25", periods=2, freq="5min"))

    out = policy.apply_session_policy(hist, market="us", timeframe="5m", policy="regular")

    assert out.metadata["timezone_assumption"] is True
    assert list(out.frame.index.strftime("%H:%M")) == ["09:30"]


def test_naive_provider_timestamps_use_declared_provider_timezone():
    hist = _bars(pd.date_range("2024-03-11 13:25", periods=2, freq="5min"))
    hist.attrs["provider_timezone"] = "UTC"

    out = policy.apply_session_policy(hist, market="us", timeframe="5m", policy="regular")

    assert out.metadata["timezone_assumption"] is False
    assert list(out.frame.index.tz_convert("America/New_York").strftime("%H:%M")) == ["09:30"]


def test_chart_data_status_rejects_timeframe_substitution():
    hist = _bars(pd.DatetimeIndex(["2024-01-08 10:30:00-05:00"]))

    with pytest.raises(ValueError, match="requested timeframe"):
        policy.chart_data_status(hist, requested_timeframe="5m", actual_timeframe="1d", source="realtime")


def test_chart_data_status_classifies_intraday_freshness_and_market_close():
    hist = _bars(pd.DatetimeIndex(["2024-01-08 10:30:00-05:00"]))
    open_now = datetime.fromisoformat("2024-01-08T15:35:00+00:00")

    assert policy.chart_data_status(
        hist, requested_timeframe="5m", actual_timeframe="5m", source="realtime", now=open_now
    )["freshness"] == "realtime"
    assert policy.chart_data_status(
        hist, requested_timeframe="5m", actual_timeframe="5m", source="yfinance", now=open_now
    )["freshness"] == "delayed"
    assert policy.chart_data_status(
        hist, requested_timeframe="5m", actual_timeframe="5m", source="yfinance",
        now=datetime.fromisoformat("2024-01-08T19:00:00+00:00"),
    )["freshness"] == "stale"

    closed = policy.chart_data_status(
        hist, requested_timeframe="5m", actual_timeframe="5m", source="realtime",
        now=datetime.fromisoformat("2024-01-08T23:00:00+00:00"),
    )
    assert closed["freshness"] == "realtime"
    assert closed["market_closed"] is True


def test_chart_data_status_recognizes_direct_kst_fixed_offset_frames_as_korean():
    hist = _bars(pd.DatetimeIndex(["2024-01-08 13:05:00+09:00"]))

    status = policy.chart_data_status(
        hist, requested_timeframe="5m", actual_timeframe="5m", source="yfinance",
        now=datetime.fromisoformat("2024-01-08T13:10:00+09:00"),
    )

    assert status["market"] == "kr"
    assert status["timezone"] == "Asia/Seoul"
    assert status["market_closed"] is False
    assert status["freshness"] == "delayed"


def test_chart_data_status_marks_naive_timestamps_unknown_and_daily_is_stale_after_four_days():
    naive = _bars(pd.DatetimeIndex(["2024-01-08 10:30:00"]))
    unknown = policy.chart_data_status(
        naive, requested_timeframe="5m", actual_timeframe="5m", source="realtime",
        now=datetime.fromisoformat("2024-01-08T15:35:00+00:00"),
    )
    assert unknown["freshness"] == "unknown"
    assert unknown["timezone_assumption"] is True

    daily = _bars(pd.DatetimeIndex(["2024-01-08 00:00:00+00:00"]))
    stale = policy.chart_data_status(
        daily, requested_timeframe="1d", actual_timeframe="1d", source="yfinance",
        now=datetime.fromisoformat("2024-01-13T00:00:01+00:00"),
    )
    assert stale["freshness"] == "stale"


def test_exportable_bars_copies_normalized_bars_and_attaches_provenance():
    hist = _bars(pd.DatetimeIndex(["2024-03-11 09:30:00-04:00"]))
    hist.index.name = "Date"
    metadata = {"source": "yfinance", "session": {"policy": "regular"}}

    out = policy.exportable_bars(hist, metadata)

    assert "Timestamp" in out.columns
    assert out.loc[0, "Timestamp"] == "2024-03-11T09:30:00-04:00"
    assert out.attrs["chart_data"] == metadata
    out.loc[0, "Close"] = 0
    assert hist.iloc[0]["Close"] == 100


def test_exportable_bars_preserves_an_existing_source_timestamp_column():
    hist = _bars(pd.DatetimeIndex(["2024-03-11 09:30:00-04:00"]))
    hist["Timestamp"] = ["synthetic-source"]

    out = policy.exportable_bars(hist, {})

    assert out.loc[0, "Timestamp"] == "2024-03-11T09:30:00-04:00"
    assert out.loc[0, "SourceTimestamp"] == "synthetic-source"


@pytest.mark.parametrize("market, labels, expected_local", [
    # W-FRI 주 마감 라벨과 월요일 시작 라벨 모두 그 주 금요일 정규장 마감.
    ("us", ["2026-08-07", "2026-08-03"], ["2026-08-07 16:00", "2026-08-07 16:00"]),
    ("kr", ["2026-08-07", "2026-08-03"], ["2026-08-07 15:30", "2026-08-07 15:30"]),
])
def test_C_fix2_weekly_bar_closes_on_its_fridays_session_close(market, labels, expected_local):
    zone = "America/New_York" if market == "us" else "Asia/Seoul"

    closes = policy.bar_close_times(pd.DatetimeIndex(labels), market=market, timeframe="1wk")

    assert [ts.tz_convert(zone).strftime("%Y-%m-%d %H:%M") for ts in closes] == expected_local


def test_C_fix2_monthly_daily_and_intraday_bar_close_times():
    monthly = policy.bar_close_times(pd.DatetimeIndex(["2026-07-31", "2026-07-01"]), market="us", timeframe="1mo")
    daily = policy.bar_close_time("2026-03-08", market="us", timeframe="1d")  # DST 전환일에도 벽시계 16:00
    intraday = policy.bar_close_time(pd.Timestamp("2026-08-03T13:30:00Z"), market="us", timeframe="5m")

    assert [ts.tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M") for ts in monthly] == [
        "2026-07-31 16:00", "2026-07-31 16:00",
    ]
    assert daily.tz_convert("America/New_York").strftime("%H:%M") == "16:00"
    assert intraday == pd.Timestamp("2026-08-03T13:35:00Z")
