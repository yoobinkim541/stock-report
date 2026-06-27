#!/usr/bin/env python3
"""test_us_mock_learn.py — US 모의 보상 백필 + 정책 적합 (무네트워크·fake ledger/price)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import us_mock_learn as L


class _FakeLedger:
    def __init__(self, pending):
        self._p = pending
        self.outcomes = []

    def pending(self):
        return self._p

    def log_outcome(self, o):
        self.outcomes.append(o)


def test_backfill_side_aware_correct():
    pending = [
        {"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입"},
        {"id": 2, "ticker": "B", "date": "2026-01-01", "side": "퇴출"},
        {"id": 3, "ticker": "C", "date": "2026-01-01", "side": "편입"},
    ]
    px = {"A": (0.10, 0.04, 0.05, 0.03),   # 편입 초과 +0.06 → 적중
          "B": (0.01, 0.05, 0.04, 0.03),   # 퇴출, 종목 -0.04 미달 → 잘 뺌(적중)
          "C": (0.02, 0.06, 0.05, 0.03)}   # 편입 초과 -0.04 → 오답
    led = _FakeLedger(pending)
    added = L.backfill_outcomes(led, price_fn=lambda t, d, h: px[t])
    assert added == 3
    by = {o["decision_id"]: o for o in led.outcomes}
    assert by[1]["correct"] is True and by[1]["fwd_excess"] == pytest.approx(0.06)
    assert by[2]["correct"] is True            # 퇴출 회피 적중
    assert by[3]["correct"] is False           # 편입 오답


def test_backfill_skips_immature():
    led = _FakeLedger([{"id": 1, "ticker": "A", "date": "2026-06-01", "side": "편입"}])
    assert L.backfill_outcomes(led, price_fn=lambda t, d, h: None) == 0   # 미성숙


def test_backfill_skips_failed_orders():
    """주문 실패(ok=False) 결정은 forward 보상 산출 제외 — 팬텀 트레이드 오염 방지(S6)."""
    led = _FakeLedger([
        {"id": 1, "ticker": "A", "date": "2026-01-01", "side": "편입", "ok": False},
        {"id": 2, "ticker": "B", "date": "2026-01-01", "side": "편입", "ok": True},
    ])
    added = L.backfill_outcomes(led, price_fn=lambda t, d, h: (0.10, 0.04, 0.05, 0.03))
    assert added == 1                                    # 집행건만
    assert [o["decision_id"] for o in led.outcomes] == [2]


def test_fit_policy_positive_correlation_dominates():
    rows = [{"side": "편입", "features": {"value": v}, "fwd_excess": v * 0.1}
            for v in (0.1, 0.3, 0.5, 0.7, 0.9)]
    w = L.fit_policy(rows)
    assert w["w_value"] == max(w.values())     # value↔초과수익 양상관 → 최대 가중


def test_fit_policy_empty_fallback_normalized():
    w = L.fit_policy([])
    assert set(w) == {"w_ranker", "w_value", "w_quality", "w_mom", "w_conf"}
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-3)   # DEFAULT 정규화 폴백


def test_eval_policy_basket_excess():
    from ml import us_policy
    rows = [{"side": "편입", "features": {"ranker": 0.9}, "fwd_excess": 0.05},
            {"side": "편입", "features": {"ranker": 0.5}, "fwd_excess": 0.02},
            {"side": "편입", "features": {"ranker": 0.1}, "fwd_excess": -0.03}]
    out = L.eval_policy(rows, us_policy.DEFAULT_POLICY, max_positions=2)
    assert out["excess"] == pytest.approx(0.035, abs=1e-3)   # 상위 2 평균
    assert out["n"] == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
