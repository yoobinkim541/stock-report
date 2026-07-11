import json
from datetime import datetime, timedelta

from ml.entry_analyzer import EntryScore, KST, check_alert_signals, format_alert_message


def _entry_score(**overrides):
    data = {
        "ticker": "MSFT",
        "category": "stock",
        "underlying": "MSFT",
        "current_drawdown": -0.08,
        "current_rsi": 42.0,
        "current_vix": 18.0,
        "current_mom_20d": -0.02,
        "current_mom_60d": 0.03,
        "current_price": 500.0,
        "n_similar": 25,
        "win_prob_20d": 0.68,
        "win_prob_60d": 0.72,
        "expected_ret_20d": 0.04,
        "expected_ret_60d": 0.08,
        "downside_p25_20d": -0.03,
        "upside_p75_20d": 0.07,
        "score": 0.66,
        "signal": "enter",
        "reasons": ["승률 68% (강세)", "손익비 1.3× (보통)"],
        "timestamp": "2026-06-23 09:00 KST",
        "currency": "USD",
        "display_name": "MSFT",
    }
    data.update(overrides)
    return EntryScore(**data)


def test_check_alert_signals_realerts_persistent_enter_after_cooldown(tmp_path, monkeypatch):
    state_path = tmp_path / "entry_alert_state.json"
    monkeypatch.setattr("ml.entry_analyzer.ALERT_STATE_PATH", state_path)
    monkeypatch.setattr("ml.entry_analyzer.ALERT_COOLDOWN_H", 12)

    first = check_alert_signals([_entry_score()])
    assert [s.ticker for s in first] == ["MSFT"]

    immediate = check_alert_signals([_entry_score()])
    assert immediate == []

    state = json.loads(state_path.read_text())
    state["MSFT"]["last_alert"] = (datetime.now(KST) - timedelta(hours=13)).isoformat()
    state["MSFT"]["last_signal"] = "enter"
    state_path.write_text(json.dumps(state))

    repeated = check_alert_signals([_entry_score()])
    assert [s.ticker for s in repeated] == ["MSFT"]


def test_check_alert_signals_suppresses_enter_before_cooldown(tmp_path, monkeypatch):
    state_path = tmp_path / "entry_alert_state.json"
    monkeypatch.setattr("ml.entry_analyzer.ALERT_STATE_PATH", state_path)
    monkeypatch.setattr("ml.entry_analyzer.ALERT_COOLDOWN_H", 12)

    state_path.write_text(json.dumps({
        "MSFT": {
            "last_alert": (datetime.now(KST) - timedelta(hours=6)).isoformat(),
            "last_signal": "enter",
        }
    }))

    assert check_alert_signals([_entry_score()]) == []


def test_format_alert_message_is_explanatory_not_prescriptive():
    msg = format_alert_message(_entry_score(
        ticker="000270.KS",
        underlying="000270.KS",
        current_drawdown=-0.221,
        current_rsi=41.0,
        current_vix=16.1,
        current_mom_20d=-0.065,
        current_mom_60d=-0.02,
        current_price=153_700.0,
        n_similar=24,
        win_prob_20d=0.72,
        win_prob_60d=0.57,
        expected_ret_20d=0.03,
        expected_ret_60d=0.014,
        downside_p25_20d=-0.009,
        upside_p75_20d=0.082,
        score=0.75,
        reasons=["승률 72% (강세)", "손익비 3.1× (양호)"],
        currency="KRW",
        display_name="기아",
    ))

    assert "진입 후보(정보형) — 기아 (000270.KS)" in msg
    assert "🟢 기아 (Kia) · 점수 0.75/1.00" in msg
    assert "[ 한줄 판단 ]" in msg
    assert "[ 왜 떴나 ]" in msg
    assert "[ 왜 조심해야 하나 ]" in msg
    assert "[ 참고 레벨 ]" in msg
    assert "[ 매매 가이드 ]" not in msg
    assert "표본:     24건 (보통)" in msg
    assert "보정 1.0× (최소위험 -3.0% 적용)" in msg
    assert "무효화선:" in msg
    assert "하방 P25 -0.9%, 최소 -3.0% 적용" in msg
    assert "목표 참고:" in msg and "+8.2%" in msg
    assert "판단 해석" in msg
    assert "정보형 알림 — 자동 주문 아님" in msg


def test_format_alert_message_flags_tiny_p25_inflated_reward_risk():
    msg = format_alert_message(_entry_score(
        ticker="000660.KS",
        underlying="000660.KS",
        current_drawdown=-0.28,
        current_rsi=37.0,
        current_vix=16.1,
        current_mom_20d=0.027,
        current_mom_60d=-0.08,
        current_price=2_103_000.0,
        n_similar=24,
        win_prob_20d=0.76,
        win_prob_60d=0.68,
        expected_ret_20d=0.077,
        expected_ret_60d=0.134,
        downside_p25_20d=-0.0014,
        upside_p75_20d=0.2038,
        score=0.87,
        reasons=["승률 76% (강세)", "손익비 55.0× (양호)"],
        currency="KRW",
        display_name="SK하이닉스",
    ))

    assert "진입 후보(정보형) — SK하이닉스 (000660.KS)" in msg
    assert "보정 2.6× (원시 55.0×, P25 왜곡 가능)" in msg
    assert "원시 손익비 55.0×는 하방 P25 -0.1%가 너무 작아 과대 표시 가능" in msg
    assert "목표 참고:" in msg and "+20.4%" in msg
    assert "무효화선:" in msg and "-3.0%" in msg
