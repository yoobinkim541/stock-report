#!/usr/bin/env python3
"""test_kiwoom_mock_report.py — 모의 현황 보고 (무네트워크, 모킹)."""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import kiwoom_mock_report as rpt           # noqa: E402
import kiwoom_mock                          # noqa: E402


def _bal(ok=True, nav=11_000_000, cash=2_000_000):
    return {"ok": ok,
            "positions": {"005930": {"name": "삼성전자", "shares": 100, "avg_price": 70000,
                                     "cur_price": 75000, "value": 7_500_000, "pnl": 500_000, "return_pct": 7.1}},
            "pos_value": 7_500_000, "cash_krw": cash, "nav": nav}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setenv("MOCK_REPORT_LLM_ENABLED", "0")
    monkeypatch.setattr(kiwoom_mock, "get_balance", lambda: _bal())
    monkeypatch.setattr(rpt, "_llm_shadow_summary", lambda: (
        {"n": 0, "hit_rate": None, "avg_delta": None, "by_action": {}}, 0))
    monkeypatch.setattr(rpt, "_snapshots", lambda: [
        {"date": "2026-06-01 09:30", "kind": "snapshot", "nav": 10_000_000},
        {"date": "2026-06-25 09:30", "kind": "snapshot", "nav": 10_800_000},
    ])
    monkeypatch.setattr(rpt, "_recent_decisions", lambda: (
        [{"date": "2026-06-26", "side": "편입", "code": "005930", "action": "강한 매수후보",
          "qty": 3, "name": "삼성전자",
          "rationale": {"one_line_reason": "기관 매집 + 일일신호 긍정"}}], "2026-06-26"))
    from providers import market_data
    monkeypatch.setattr(market_data, "fetch_kospi_stats",
                        lambda since_date=None: {"return_pct": 6.0, "mdd": 0.20})


def test_build_report_shows_objective_metrics(patched):
    txt = rpt.build_report()
    assert "[모의]" in txt
    assert "NAV" in txt and "11,000,000" in txt
    assert "현금비중" in txt
    assert "누적" in txt and "KOSPI 대비" in txt and "%p" in txt  # 아웃퍼폼 가시화(F3: KOSPI 대비 %p)
    assert "MDD" in txt and "대비 방어" in txt                    # MDD vs KOSPI
    assert "보유 1종목" in txt and "삼성전자" in txt              # 보유 표
    assert "최근 결정" in txt
    assert "편입" in txt and "삼성전자 (005930)" in txt and "3주" in txt
    assert "근거: 기관 매집" in txt and "해석:" in txt            # 편입 사유/해석


def test_build_report_excess_positive(patched):
    # nav 11M / inception 10M = +10%, KOSPI +6% → 초과 +4%p (헤드라인 KOSPI대비)
    txt = rpt.build_report()
    assert "KOSPI 대비 +4.0%p" in txt


def test_build_report_mdd_within_index_ok(patched):
    # NAV 시계열 10M→10.8M→11M 단조증가 → 전략 MDD 0% ≤ 지수 20% → ✅
    txt = rpt.build_report()
    assert "✅" in txt


def test_build_report_includes_llm_rationale_when_available(patched, monkeypatch):
    seen = {}

    def fake_run(payload):
        seen.update(payload)
        return ({
            "summary": "KOSPI 대비 초과수익과 MDD를 함께 확인했습니다.",
            "position_notes": ["삼성전자 비중은 수익 기여가 있지만 집중도 점검 대상입니다."],
            "decision_notes": ["최근 편입은 기관 매집과 일일신호 근거입니다."],
            "risk_checks": ["회전율 상승 시 비용차감 성과를 확인합니다."],
            "confidence": 73,
        }, "ok")

    monkeypatch.setattr(rpt.llm_rationale, "run", fake_run)
    txt = rpt.build_report()
    assert seen["market"] == "KR"
    assert seen["positions"][0]["code"] == "005930"
    assert "🧠 LLM 판단근거" in txt
    assert "KOSPI 대비 초과수익" in txt
    assert "삼성전자 비중" in txt
    assert "신뢰도 73/100" in txt


def test_build_report_balance_failure(monkeypatch):
    monkeypatch.setattr(kiwoom_mock, "get_balance", lambda: {"ok": False, "positions": {},
                        "pos_value": 0, "cash_krw": None, "nav": None})
    txt = rpt.build_report()
    assert "잔고 조회 실패" in txt


def test_main_skips_when_disabled(monkeypatch):
    monkeypatch.delenv("KIWOOM_MOCK_ENABLED", raising=False)
    monkeypatch.setattr(kiwoom_mock, "is_enabled", lambda: False)
    sent = {"n": 0}
    import notify
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1))
    assert rpt.main() == 0
    assert sent["n"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
