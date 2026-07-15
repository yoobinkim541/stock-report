import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ml.intraday_signal import (
    IntradaySignal,
    _format_bar_timestamp,
    _normalize_intraday_df,
    is_high_confidence_intraday_signal,
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


def test_et_timezone_follows_dst():
    """미 동부시간은 서머타임을 반영해야 — 겨울 -5h(EST), 여름 -4h(EDT).
    고정 오프셋으로 회귀하면 겨울철 장 운영시간 판별이 1시간 어긋난다."""
    from ml.intraday_signal import ET

    winter = datetime(2026, 1, 15, 12, 0, tzinfo=ET).utcoffset()
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=ET).utcoffset()
    assert winter == timedelta(hours=-5)
    assert summer == timedelta(hours=-4)


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


def _intraday_sig(**overrides):
    data = {
        "ticker": "000660.KS",
        "interval": "5m",
        "currency": "KRW",
        "price": 2732000,
        "change_pct": 0.022,
        "vwap_dev": 0.006,
        "rsi": 62,
        "vol_ratio": 3.4,
        "ema_cross_up": True,
        "alerts": ["📈 거래량 증가 3×", "⚡ EMA 9/21 상향 돌파", "🚀 5m 모멘텀 +1.8%"],
        "score": 0.65,
        "timestamp": "09:20 KST",
    }
    data.update(overrides)
    return IntradaySignal(**data)


def test_high_confidence_intraday_signal_requires_multiple_confirmations():
    assert is_high_confidence_intraday_signal(_intraday_sig()) is True

    assert is_high_confidence_intraday_signal(_intraday_sig(score=0.60)) is False
    assert is_high_confidence_intraday_signal(_intraday_sig(alerts=["⚡ EMA 9/21 상향 돌파"], score=0.70)) is False
    assert is_high_confidence_intraday_signal(_intraday_sig(vol_ratio=1.2, alerts=["⚡ EMA 9/21 상향 돌파", "🚀 5m 모멘텀 +1.8%", "🎯 VWAP 상향 돌파 (+0.20%)"])) is False
    assert is_high_confidence_intraday_signal(_intraday_sig(vwap_dev=-0.01, ema_cross_up=False, alerts=["📈 거래량 증가 3×", "🚀 5m 모멘텀 +1.8%", "💥 BB 상방 돌파 (스퀴즈 해소)"])) is False
    assert is_high_confidence_intraday_signal(_intraday_sig(rsi=72)) is False
    assert is_high_confidence_intraday_signal(_intraday_sig(alerts=["📈 거래량 증가 3×", "⚡ EMA 9/21 상향 돌파", "🔻 5m 급락 -1.8%"], score=0.70)) is False


def test_lmt_style_bb_volume_alert_is_not_high_confidence():
    sig = IntradaySignal(
        ticker="LMT",
        interval="5m",
        currency="USD",
        price=511.00,
        change_pct=0.009,
        vwap_dev=-0.0215,
        rsi=72,
        vol_ratio=5.5,
        ema_cross_up=False,
        alerts=["🔥 거래량 급등 5×", "💥 BB 상방 돌파 (스퀴즈 해소)"],
        score=0.60,
        timestamp="22:57 KST",
    )

    assert is_high_confidence_intraday_signal(sig) is False


def test_bullish_divergence_counts_toward_confidence_confirmation():
    """↕️ RSI 강세 다이버전스도 EMA·거래량과 동급으로 롱 확신 조건에 포함돼야 한다."""
    sig = _intraday_sig(alerts=["📈 거래량 증가 3×", "🎯 VWAP 상향 돌파 (+0.20%)", "↕️ RSI 강세 다이버전스"])
    assert is_high_confidence_intraday_signal(sig) is True


def test_bearish_divergence_alone_is_not_bullish_confirmation():
    sig = _intraday_sig(alerts=["📈 거래량 증가 3×", "🚀 5m 모멘텀 +1.8%", "↕️ RSI 약세 다이버전스"])
    assert is_high_confidence_intraday_signal(sig) is False


def _synthetic_intraday_df(n=40, base=100.0):
    idx = pd.date_range("2026-07-14 09:30", periods=n, freq="5min")
    close = pd.Series([base + i * 0.05 for i in range(n)], index=idx)
    return pd.DataFrame({"Open": close - 0.02, "High": close + 0.05, "Low": close - 0.05,
                         "Close": close, "Volume": [1000.0] * n}, index=idx)


def test_analyze_intraday_emits_divergence_alert(monkeypatch):
    """analyze_intraday 가 최근 확정된 다이버전스를 알림·점수에 반영한다."""
    import ml.features as mlf
    import ml.intraday_signal as intraday_signal

    df = _synthetic_intraday_df()
    monkeypatch.setattr(intraday_signal, "fetch_intraday", lambda *a, **k: df)
    recent_date = df.index[-3]                      # "최근 6봉 이내" 조건 충족
    monkeypatch.setattr(mlf, "rsi_divergence_events", lambda *a, **k: [
        {"type": "bullish", "date": recent_date, "prior_date": df.index[-10],
         "price": 105.0, "rsi": 45.0, "prior_price": 103.0, "prior_rsi": 35.0},
    ])

    sig = intraday_signal.analyze_intraday("TEST", interval="5m")
    assert sig is not None
    assert any("강세 다이버전스" in a for a in sig.alerts)


def test_analyze_intraday_ignores_stale_divergence(monkeypatch):
    """6봉보다 오래된 다이버전스는 알림에 반영하지 않는다(더 이상 '현재' 상태 아님)."""
    import ml.features as mlf
    import ml.intraday_signal as intraday_signal

    df = _synthetic_intraday_df()
    monkeypatch.setattr(intraday_signal, "fetch_intraday", lambda *a, **k: df)
    stale_date = df.index[-20]
    monkeypatch.setattr(mlf, "rsi_divergence_events", lambda *a, **k: [
        {"type": "bullish", "date": stale_date, "prior_date": df.index[-30],
         "price": 105.0, "rsi": 45.0, "prior_price": 103.0, "prior_rsi": 35.0},
    ])

    sig = intraday_signal.analyze_intraday("TEST", interval="5m")
    assert sig is not None
    assert not any("다이버전스" in a for a in sig.alerts)
