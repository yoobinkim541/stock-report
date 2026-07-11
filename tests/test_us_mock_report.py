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
    txt = R.build_report(detail=True)
    assert seen["market"] == "US"
    assert seen["positions"][0]["ticker"] == "MSFT"
    assert seen["scorecard"]["n_buy"] == 3
    assert "현금 $800 · 현금비중 16.0%" in txt
    assert "상태 해석" in txt
    assert "보유 요약" in txt
    assert "평가손익 ▲5.0% (+$200)" in txt
    assert "Microsoft (MSFT)" in txt
    assert "최근 결정 (2026-06-26)" in txt
    assert "근거: 품질 점수와 추세 신호 우위" in txt
    assert "해석: 추세 근거가 편입 조건을 뒷받침합니다." in txt
    assert "🧠 LLM 판단근거" in txt
    assert "QQQ 대비 초과수익" in txt
    assert "MSFT 비중" in txt
    assert "신뢰도 68/100" in txt


def test_build_report_interprets_recent_factor_decisions(monkeypatch):
    monkeypatch.setattr(kis_mock, "get_balance", lambda: {
        "ok": True,
        "positions": {},
        "pos_value": 0.0,
        "cash_usd": 100_000.0,
        "nav": 100_000.0,
    })
    monkeypatch.setattr(R, "_snapshots", lambda: [])
    monkeypatch.setattr(R, "_scorecard_rows", lambda: [])
    monkeypatch.setattr(R, "_recent_decisions", lambda: (
        [{"date": "2026-07-08", "side": "편입", "ticker": "ADBE", "policy_score": 0.62,
          "rationale": {"one_line_reason": "value 0.45·quality 0.02·mom 0.50"}}],
        "2026-07-08",
    ))
    monkeypatch.setattr(R, "_llm_shadow_summary", lambda: (
        {"n": 0, "hit_rate": None, "avg_delta": None, "by_action": {}}, 0))
    monkeypatch.setattr(R.llm_rationale, "run", lambda payload: (None, "disabled"))
    from providers import market_data
    monkeypatch.setattr(market_data, "fetch_kospi_stats",
                        lambda since_date=None, symbol=None: {"return_pct": -3.0, "mdd": 0.04})

    txt = R.build_report(detail=True)
    assert "Adobe (ADBE)" in txt
    assert "근거: value 0.45·quality 0.02·mom 0.50" in txt
    assert "해석: 밸류 보통 · 퀄리티 약함 · 모멘텀 보통 · 퀄리티 보완 필요" in txt
    assert "검증 상태" in txt
    assert "LLM Shadow: 성숙 표본 없음" in txt


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

    text = R.build_report(detail=True)
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


def test_build_report_defaults_to_compact(monkeypatch):
    monkeypatch.setenv("MOCK_REPORT_LLM_ENABLED", "1")
    monkeypatch.setattr(kis_mock, "get_balance", lambda: {
        "ok": True,
        "positions": {
            "CRM": {"shares": 294, "avg_price": 160.22, "cur_price": 166.58, "value": 48_975.0},
            "INTC": {"shares": 330, "avg_price": 123.58, "cur_price": 110.24, "value": 36_379.0},
        },
        "pos_value": 85_354.0,
        "cash_usd": 71_170.0,
        "nav": 156_524.0,
    })
    monkeypatch.setattr(R, "_snapshots", lambda: [
        {"date": "2026-07-01 21:30", "kind": "snapshot", "nav": 158_000.0},
    ])
    monkeypatch.setattr(R, "_scorecard_rows", lambda: [])
    monkeypatch.setattr(R, "_recent_decisions", lambda: (
        [{"date": "2026-07-09", "side": "편입", "ticker": "AMZN",
          "rationale": {"one_line_reason": "value 0.32·quality 0.01·mom 0.46"}}],
        "2026-07-09",
    ))
    monkeypatch.setattr(R, "_llm_shadow_summary", lambda: (
        {"n": 0, "hit_rate": None, "avg_delta": None, "by_action": {}}, 0))
    monkeypatch.setattr(R.llm_rationale, "run", lambda payload: pytest.fail("compact report should not call LLM rationale"))
    from providers import market_data
    monkeypatch.setattr(market_data, "fetch_kospi_stats",
                        lambda since_date=None, symbol=None: {"return_pct": -3.0, "mdd": 0.04})

    txt = R.build_report()
    assert "US 모의" in txt
    assert "판단" in txt
    assert "보유 2종목" in txt
    assert "CRM" in txt and "INTC" in txt
    assert "최근 결정" in txt
    assert "체크" in txt
    assert "상세: /paper us full" in txt
    assert "🧠 LLM 판단근거" not in txt
    assert "검증 상태" not in txt
    assert "123.58→110.24" not in txt
