#!/usr/bin/env python3
"""test_us_mock_report.py — US 모의 평가 스코어카드 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import kis_mock
import us_mock_report as R


def test_scorecard_hit_rates_and_ic():
    rows = [
        {"side": "편입", "correct": True, "policy_score": 0.9, "fwd_excess": 0.05},
        {"side": "편입", "correct": False, "policy_score": 0.3, "fwd_excess": -0.02},
        {"side": "편입", "correct": True, "policy_score": 0.7, "fwd_excess": 0.03},
        {"side": "퇴출", "correct": True, "policy_score": 0.1, "fwd_excess": -0.04},
        {"side": "퇴출", "correct": False, "policy_score": 0.2, "fwd_excess": 0.01},
    ]
    sc = R.compute_scorecard(rows)
    assert sc["buy_hit"] == pytest.approx(66.7, abs=0.2) and sc["n_buy"] == 3
    assert sc["sell_hit"] == pytest.approx(50.0) and sc["n_sell"] == 2
    assert sc["ic"] is not None and sc["ic"] > 0          # 정책점수↑ → 초과수익↑


def test_scorecard_empty():
    sc = R.compute_scorecard([])
    assert sc["buy_hit"] is None and sc["sell_hit"] is None and sc["ic"] is None
    assert sc["n_buy"] == 0 and sc["n_sell"] == 0


def test_scorecard_ic_needs_min_pairs():
    rows = [{"side": "편입", "correct": True, "policy_score": 0.9, "fwd_excess": 0.05},
            {"side": "편입", "correct": True, "policy_score": 0.7, "fwd_excess": 0.03}]
    assert R.compute_scorecard(rows)["ic"] is None        # <3 → IC 미산출


def test_build_report_includes_llm_rationale_when_available(monkeypatch):
    monkeypatch.setenv("MOCK_REPORT_LLM_ENABLED", "0")
    monkeypatch.setattr(kis_mock, "get_balance", lambda: {
        "ok": True,
        "positions": {
            "MSFT": {"shares": 10, "avg_price": 400.0, "cur_price": 420.0, "value": 4200.0},
        },
        "pos_value": 4200.0,
        "cash_usd": 800.0,
        "nav": 5000.0,
    })
    monkeypatch.setattr(R, "_snapshots", lambda: [
        {"date": "2026-06-01 09:30", "kind": "snapshot", "nav": 4500.0},
        {"date": "2026-06-25 09:30", "kind": "snapshot", "nav": 4800.0},
    ])
    monkeypatch.setattr(R, "_scorecard_rows", lambda: [
        {"side": "편입", "correct": True, "policy_score": 0.9, "fwd_excess": 0.05},
        {"side": "편입", "correct": True, "policy_score": 0.7, "fwd_excess": 0.03},
        {"side": "편입", "correct": False, "policy_score": 0.3, "fwd_excess": -0.02},
    ])
    monkeypatch.setattr(R, "_recent_decisions", lambda: (
        [{"date": "2026-06-26", "side": "편입", "ticker": "MSFT", "policy_score": 0.82,
          "rationale": {"one_line_reason": "품질 점수와 추세 신호 우위"}}], "2026-06-26"))
    monkeypatch.setattr(R, "_llm_shadow_summary", lambda: (
        {"n": 0, "hit_rate": None, "avg_delta": None, "by_action": {}}, 0))
    from providers import market_data
    monkeypatch.setattr(market_data, "fetch_kospi_stats",
                        lambda since_date=None, symbol=None: {"return_pct": 7.0, "mdd": 0.12})

    seen = {}

    def fake_run(payload):
        seen.update(payload)
        return ({
            "summary": "QQQ 대비 초과수익과 로직 평가를 함께 확인했습니다.",
            "position_notes": ["MSFT 비중은 수익 기여와 집중도 점검을 병행합니다."],
            "decision_notes": ["최근 편입은 정책점수와 추세 신호 근거입니다."],
            "risk_checks": ["벤치마크 대비 MDD 확대 여부를 확인합니다."],
            "confidence": 68,
        }, "ok")

    monkeypatch.setattr(R.llm_rationale, "run", fake_run)
    txt = R.build_report()
    assert seen["market"] == "US"
    assert seen["positions"][0]["ticker"] == "MSFT"
    assert seen["scorecard"]["n_buy"] == 3
    assert "🧠 LLM 판단근거" in txt
    assert "QQQ 대비 초과수익" in txt
    assert "MSFT 비중" in txt
    assert "신뢰도 68/100" in txt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_build_report_shows_sleeve_state(monkeypatch):
    """/paper us — 슬리브 활성 시 게이트 상태·보유 QLD 를 현황에 표시 (봇 가시화)."""
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    import kis_mock
    from crons import us_mock_report as R
    from crons import us_mock_track as T

    monkeypatch.setattr(kis_mock, "get_balance", lambda: {
        "ok": True, "cash_usd": 10_000.0, "pos_value": 90_000.0, "nav": 100_000.0,
        "positions": {"QLD": {"shares": 300, "avg_price": 90.0, "cur_price": 100.0,
                              "value": 30_000.0}}})
    monkeypatch.setattr(R, "_snapshots", lambda: [])
    monkeypatch.setattr(R, "_scorecard_rows", lambda: [])
    monkeypatch.setattr(R, "_recent_decisions", lambda: ([], None))
    monkeypatch.setattr(T, "LEV_SLEEVE_ENABLED", True)
    monkeypatch.setattr(T, "load_lev_shadow", lambda path=None: 1.3)
    try:
        from providers import market_data
        monkeypatch.setattr(market_data, "fetch_kospi_stats",
                            lambda *a, **k: {"return_pct": None, "mdd": None})
    except Exception:
        pass

    text = R.build_report()
    assert "구조레버 슬리브" in text
    assert "×1.30" in text and "목표 30%" in text
    assert "QLD 300주 (30%)" in text


def test_build_report_sleeve_hidden_when_off_and_flat(monkeypatch):
    """슬리브 off + 보유 0 → 섹션 미표시 (기존 출력 불변)."""
    import kis_mock
    from crons import us_mock_report as R
    from crons import us_mock_track as T

    monkeypatch.setattr(kis_mock, "get_balance", lambda: {
        "ok": True, "cash_usd": 100_000.0, "pos_value": 0.0, "nav": 100_000.0, "positions": {}})
    monkeypatch.setattr(R, "_snapshots", lambda: [])
    monkeypatch.setattr(R, "_scorecard_rows", lambda: [])
    monkeypatch.setattr(R, "_recent_decisions", lambda: ([], None))
    monkeypatch.setattr(T, "LEV_SLEEVE_ENABLED", False)
    try:
        from providers import market_data
        monkeypatch.setattr(market_data, "fetch_kospi_stats",
                            lambda *a, **k: {"return_pct": None, "mdd": None})
    except Exception:
        pass
    assert "구조레버 슬리브" not in R.build_report()
