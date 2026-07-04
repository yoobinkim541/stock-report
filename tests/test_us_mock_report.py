#!/usr/bin/env python3
"""test_us_mock_report.py — US 모의 평가 스코어카드 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

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
