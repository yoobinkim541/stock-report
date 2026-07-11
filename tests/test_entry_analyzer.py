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
    assert "표본 24건(보통)" in msg
    assert "신뢰도" in msg and "95% CI" in msg          # V2 — CI·신뢰도 병기
    assert "레벨 손익비" in msg and "기대(중앙값)" in msg  # V2 — RR 기준 명시
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


def test_risk_floor_vol_adaptive():
    """무효화선 최소폭 — 저변동 3% 유지·고변동 2×일σ·상한 20% (V2 핵심)."""
    from ml.entry_analyzer import risk_floor
    assert risk_floor(0.010) == 0.03                 # 저변동 — 종전과 동일
    assert risk_floor(0.015) == 0.03
    assert abs(risk_floor(0.072) - 0.144) < 1e-9     # 일σ 7.2%(하이닉스 실측) → -14.4%
    assert risk_floor(0.30) == 0.20                  # 상한
    assert risk_floor(float("nan")) == 0.03          # 결측 graceful


def test_trade_levels_vol_floor_and_krx_tick():
    """고변동 종목 — 무효화선이 -3% 대신 변동성 비례 + KRW 레벨 호가단위 반올림."""
    from ml.entry_analyzer import trade_level_values
    s = _entry_score(
        ticker="000660.KS", underlying="000660.KS", currency="KRW",
        current_price=2_180_000.0, current_vol20=0.072,
        downside_p25_20d=-0.012, upside_p75_20d=0.183,
        expected_ret_20d=0.026, display_name="SK하이닉스")
    buy_lo, target, stop = trade_level_values(s)
    assert stop <= 2_180_000 * (1 - 0.144) + 1_000   # -3% 가 아닌 -14.4% 부근
    from ml.intraday_axes import kr_tick
    for v in (buy_lo, target, stop):                  # KRX 호가단위 정합
        t = kr_tick(v)
        assert abs(v / t - round(v / t)) < 1e-6, f"호가단위 위반: {v}"
    # 관찰/분할 구간도 floor/2 하한 — 얕은 P25(-1.2%)의 0.6% 존이 아닌 7.2% 존
    assert buy_lo <= 2_180_000 * (1 - 0.07)

    msg = format_alert_message(s)
    assert "-14.4%" in msg and "변동성 보정" in msg
    assert "일변동성 7.2%" in msg
    assert "글로벌 프록시" in msg                      # KR 에 VIX 라벨


def test_alert_v2_conditional_60d_and_confidence():
    """60d 문구 조건 분기(무조건 '약하면' 보일러플레이트 버그 수정) + 충돌 신뢰도 강등."""
    base = dict(ticker="000660.KS", underlying="000660.KS", currency="KRW",
                current_price=2_180_000.0, current_vol20=0.072,
                downside_p25_20d=-0.012, upside_p75_20d=0.183,
                win_prob_20d=0.74, expected_ret_20d=0.026, display_name="SK하이닉스")
    # 혼재 (승률↓·기대↑ — 실제 하이닉스 케이스)
    m1 = format_alert_message(_entry_score(**base, win_prob_60d=0.71, expected_ret_60d=0.138))
    assert "방향 불일치" in m1 and "짧게 관리" not in m1
    # 둘 다 약함
    m2 = format_alert_message(_entry_score(**base, win_prob_60d=0.55, expected_ret_60d=0.01))
    assert "짧게 관리" in m2
    # 둘 다 강함
    m3 = format_alert_message(_entry_score(**base, win_prob_60d=0.80, expected_ret_60d=0.15))
    assert "중기에도 우위 관찰" in m3
    # 신뢰도 강등 — RR<1 + 기술 중립 + 피벗 하회 = 충돌 3 → 🟡 + 낮음
    weak = format_alert_message(_entry_score(
        **base, win_prob_60d=0.71, expected_ret_60d=0.138,
        technical_rating="중립", pivot_position="below_p"))
    head = weak.splitlines()[1]
    assert "신뢰도 낮음" in head and head.startswith("🟡")
    assert "통계 신호와 충돌" in weak


def test_alert_v2_stop_first_and_parabolic():
    """무효화선 선행 터치 병기 + 파라볼릭 레짐(60d 급등 후 낙폭) 경고."""
    s = _entry_score(
        ticker="000660.KS", underlying="000660.KS", currency="KRW",
        current_price=2_180_000.0, current_vol20=0.072,
        current_mom_60d=1.097, current_drawdown=-0.253,
        downside_p25_20d=-0.012, upside_p75_20d=0.183,
        n_similar=25, stop_first_frac_20d=0.2, display_name="SK하이닉스")
    msg = format_alert_message(s)
    assert "25건 중 5건은 목표 前 손절 선행" in msg
    assert "파라볼릭 조정 레짐" in msg
    assert "승률·기대는 무손절 보유 기준" in msg
