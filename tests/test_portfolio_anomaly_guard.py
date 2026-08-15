"""tests/test_portfolio_anomaly_guard.py — 포트폴리오 총액 급변 감지 (감사 후속).

2026-06-03~05 사이 미기록 현금 입출금(브로커 동기화 오류로 추정)이 총액을
+237%→-63% 튀게 만들었고, portfolio_flows() 는 주식 매수/매도만 추적해
이 변동을 못 잡아 TWR 그래프가 영구히 왜곡됐다. record_daily() 가 전일 대비
비정상 급변을 감지하면 텔레그램으로 경고하도록 안전장치를 추가한다.
"""
from __future__ import annotations

import pytest

import portfolio_tracker as pt


def test_suspicious_jump_pct_flags_large_swing():
    assert pt._suspicious_jump_pct(8652.80, 29201.81) == pytest.approx(237.48, abs=0.01)
    assert pt._suspicious_jump_pct(28886.74, 10801.83) == pytest.approx(-62.61, abs=0.01)


def test_suspicious_jump_pct_ignores_normal_daily_moves():
    assert pt._suspicious_jump_pct(9411.47, 10218.18) is None   # +8.57%
    assert pt._suspicious_jump_pct(10354.21, 9868.76) is None   # -4.69%


def test_suspicious_jump_pct_handles_missing_prior():
    assert pt._suspicious_jump_pct(None, 10000.0) is None
    assert pt._suspicious_jump_pct(0.0, 10000.0) is None


def test_record_daily_sends_telegram_alert_on_suspicious_jump(monkeypatch):
    pt.save_history([{"date": "2026-06-02", "total_usd": 8652.80, "total_krw": 13116779,
                       "exchange_rate": 1515.9, "sgov_usd": 1004.05, "qqqi_usd": 2030.12,
                       "qqq_price": 743.25, "drawdown_pct": -0.32, "rsi": 74.3, "vix": 16.07,
                       "phase": "bull_1", "market_type": "bull"}])
    monkeypatch.setattr(pt, "fetch_portfolio_value",
                        lambda: {"total_usd": 29201.81, "sgov_usd": 1004.15, "qqqi_usd": 2033.18})
    monkeypatch.setattr(pt, "fetch_exchange_rate", lambda: 1511.3)
    monkeypatch.setattr(pt, "fetch_qqq_data", lambda: {"current": 744.57, "drawdown_pct": -0.54})
    monkeypatch.setattr(pt, "fetch_rsi", lambda t: 71.0)
    monkeypatch.setattr(pt, "fetch_vix", lambda: 16.56)
    monkeypatch.setattr(pt, "classify_market", lambda qqq, rsi, vix: ("bull", "bull_1"))
    alerts = []
    monkeypatch.setattr(pt, "send_telegram", lambda text: alerts.append(text))

    pt.record_daily()

    assert len(alerts) == 1
    assert "급변" in alerts[0]
    assert "237" in alerts[0]


def test_record_daily_does_not_alert_on_normal_day(monkeypatch):
    pt.save_history([{"date": "2026-06-02", "total_usd": 8652.80, "total_krw": 13116779,
                       "exchange_rate": 1515.9, "sgov_usd": 1004.05, "qqqi_usd": 2030.12,
                       "qqq_price": 743.25, "drawdown_pct": -0.32, "rsi": 74.3, "vix": 16.07,
                       "phase": "bull_1", "market_type": "bull"}])
    monkeypatch.setattr(pt, "fetch_portfolio_value",
                        lambda: {"total_usd": 8700.0, "sgov_usd": 1004.2, "qqqi_usd": 2031.0})
    monkeypatch.setattr(pt, "fetch_exchange_rate", lambda: 1516.0)
    monkeypatch.setattr(pt, "fetch_qqq_data", lambda: {"current": 744.0, "drawdown_pct": -0.4})
    monkeypatch.setattr(pt, "fetch_rsi", lambda t: 72.0)
    monkeypatch.setattr(pt, "fetch_vix", lambda: 16.1)
    monkeypatch.setattr(pt, "classify_market", lambda qqq, rsi, vix: ("bull", "bull_1"))
    alerts = []
    monkeypatch.setattr(pt, "send_telegram", lambda text: alerts.append(text))

    pt.record_daily()

    assert alerts == []


def test_record_daily_alert_failure_does_not_block_recording(monkeypatch):
    """텔레그램 발송이 실패해도 기록 자체는 계속돼야 한다 (알림은 부가 기능)."""
    pt.save_history([{"date": "2026-06-02", "total_usd": 8652.80, "total_krw": 13116779,
                       "exchange_rate": 1515.9, "sgov_usd": 1004.05, "qqqi_usd": 2030.12,
                       "qqq_price": 743.25, "drawdown_pct": -0.32, "rsi": 74.3, "vix": 16.07,
                       "phase": "bull_1", "market_type": "bull"}])
    monkeypatch.setattr(pt, "fetch_portfolio_value",
                        lambda: {"total_usd": 29201.81, "sgov_usd": 1004.15, "qqqi_usd": 2033.18})
    monkeypatch.setattr(pt, "fetch_exchange_rate", lambda: 1511.3)
    monkeypatch.setattr(pt, "fetch_qqq_data", lambda: {"current": 744.57, "drawdown_pct": -0.54})
    monkeypatch.setattr(pt, "fetch_rsi", lambda t: 71.0)
    monkeypatch.setattr(pt, "fetch_vix", lambda: 16.56)
    monkeypatch.setattr(pt, "classify_market", lambda qqq, rsi, vix: ("bull", "bull_1"))

    def _boom(text):
        raise RuntimeError("network down")
    monkeypatch.setattr(pt, "send_telegram", _boom)

    entry = pt.record_daily()

    assert entry["total_usd"] == 29201.81
    hist = pt.load_history()
    assert any(r["date"] == entry["date"] for r in hist)
