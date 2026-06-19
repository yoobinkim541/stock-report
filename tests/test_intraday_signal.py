import json
from pathlib import Path

import pandas as pd

from ml.intraday_signal import (
    IntradaySignal,
    _format_bar_timestamp,
    _normalize_intraday_df,
    mark_intraday_signal_emitted,
    should_emit_intraday_signal,
)


def test_normalize_intraday_df_flattens_single_ticker_multiindex():
    df = pd.DataFrame(
        [[100, 101, 99, 100, 1000]],
        columns=pd.MultiIndex.from_tuples(
            [
                ("Close", "000660.KS"),
                ("High", "000660.KS"),
                ("Low", "000660.KS"),
                ("Open", "000660.KS"),
                ("Volume", "000660.KS"),
            ],
            names=["Price", "Ticker"],
        ),
    )

    normalized = _normalize_intraday_df(df)

    assert list(normalized.columns) == ["Close", "High", "Low", "Open", "Volume"]
    assert float(normalized["Close"].iloc[0]) == 100.0


def test_format_bar_timestamp_uses_bar_time_not_current_time():
    assert _format_bar_timestamp(pd.Timestamp("2026-06-19 00:20:00")) == "09:20 KST"


def test_should_emit_intraday_signal_suppresses_same_bar_same_alerts(tmp_path):
    state_path = tmp_path / "intraday_sent_signals.json"
    sig = IntradaySignal(
        ticker="000660.KS",
        interval="5m",
        currency="KRW",
        price=2732000,
        change_pct=0.022,
        vwap_dev=0.0644,
        rsi=76,
        vol_ratio=1.7,
        ema_cross_up=False,
        alerts=["💥 BB 상방 돌파 (스퀴즈 해소)", "🚀 5m 모멘텀 +2.2%"],
        score=0.40,
        timestamp="09:20 KST",
    )

    assert should_emit_intraday_signal(sig, state_path=state_path) is True
    assert not state_path.exists()

    mark_intraday_signal_emitted(sig, state_path=state_path)
    assert should_emit_intraday_signal(sig, state_path=state_path) is False

    saved = json.loads(state_path.read_text())
    assert "000660.KS|5m" in saved

    sig.timestamp = "09:25 KST"
    assert should_emit_intraday_signal(sig, state_path=state_path) is True


def test_intraday_signal_dedup_state_is_interval_scoped(tmp_path):
    state_path = tmp_path / "intraday_sent_signals.json"
    base = IntradaySignal(
        ticker="000660.KS",
        interval="5m",
        currency="KRW",
        price=2732000,
        change_pct=0.022,
        vwap_dev=0.0644,
        rsi=76,
        vol_ratio=1.7,
        ema_cross_up=False,
        alerts=["🚀 5m 모멘텀 +2.2%"],
        score=0.40,
        timestamp="09:20 KST",
    )
    other_interval = IntradaySignal(**{**base.__dict__, "interval": "1m", "alerts": ["🚀 1m 모멘텀 +2.2%"]})

    mark_intraday_signal_emitted(base, state_path=state_path)
    mark_intraday_signal_emitted(other_interval, state_path=state_path)

    assert should_emit_intraday_signal(base, state_path=state_path) is False
    assert should_emit_intraday_signal(other_interval, state_path=state_path) is False
