#!/usr/bin/env python3
"""test_kr_mock_learning.py — KR 정책 강화(보상 백필·fit·eval·게이트) 무네트워크."""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import kr_mock_learn as L                    # noqa: E402
from ml.adaptive.ledger import Ledger        # noqa: E402


def test_pearson():
    assert L._pearson([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert L._pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert L._pearson([1, 1], [1, 1]) == 0.0     # 표본 부족


def test_fit_policy_weights_sum_to_one_and_favor_correlated():
    # ranker 가 보상과 완전 상관, 나머지 무상관 → ranker 가중 최대
    rows = []
    for i in range(20):
        ex = i / 20.0
        rows.append({"features": {"ranker": ex, "fund": 0.5, "signal": 0.5, "conf": 0.5, "mom": 0.5},
                     "fwd_excess": ex})
    p = L.fit_policy(rows)
    ws = {k: v for k, v in p.items() if k.startswith("w_")}
    assert sum(ws.values()) == pytest.approx(1.0, abs=1e-3)
    assert p["w_ranker"] == max(ws.values())     # 상관 높은 피처가 최대 가중
    assert "score_threshold" not in p             # 죽은 파라미터 제거됨


def test_fit_policy_falls_back_when_no_signal():
    from ml import kr_policy
    # 전 피처 상수 → 양의 상관 합 0 → DEFAULT 가중 폴백(전부-0 붕괴 방지)
    rows = [{"features": {"ranker": 0.5, "fund": 0.5, "signal": 0.5, "conf": 0.5, "mom": 0.5},
             "fwd_excess": (i % 2) * 0.1 - 0.05} for i in range(20)]
    p = L.fit_policy(rows)
    assert p["w_ranker"] == kr_policy.DEFAULT_POLICY["w_ranker"]


def test_eval_policy_uses_top_max_positions_and_real_mdd():
    rows = [{"features": {"ranker": s}, "fwd_excess": e, "fwd_mdd": m}
            for s, e, m in [(0.9, 0.20, 0.05), (0.8, 0.10, 0.10), (0.2, -0.10, 0.30), (0.1, -0.20, 0.40)]]
    r = L.eval_policy(rows, {"w_ranker": 1.0}, max_positions=2)
    # 배치와 동일 상위 2(0.9,0.8) 선택 → 평균 초과수익 0.15, 보유기간 MDD 평균 0.075
    assert r["excess"] == pytest.approx(0.15)
    assert r["mdd"] == pytest.approx(0.075)       # (0.05+0.10)/2 — 실제 낙폭 단위
    assert r["n"] == 4


def test_backfill_matures_entries_only_and_idempotent(tmp_path):
    lg = Ledger("kr_mock", base_dir=tmp_path)
    lg.log_decision({"date": "2026-05-01", "ticker": "005930.KS", "code": "005930", "side": "편입",
                     "features": {"ranker": 0.8}})
    lg.log_decision({"date": "2026-05-01", "ticker": "000660.KS", "code": "000660", "side": "퇴출"})
    lg.log_decision({"date": "2026-05-02", "ticker": "035720.KS", "code": "035720", "side": "편입",
                     "features": {"ranker": 0.3}})

    def fake_price(ticker, date, horizon):
        if ticker == "005930.KS":
            return (0.12, 0.04, 0.06, 0.03)   # (종목수익, 지수수익, 종목MDD, 지수MDD)
        return None                            # 035720 미성숙

    added = L.backfill_outcomes(lg, price_fn=fake_price)
    assert added == 1                                   # 편입·성숙분만 (퇴출 제외, 미성숙 제외)
    from ml.adaptive import costs
    rt = costs.round_trip_frac("KR")
    outs = lg.read_outcomes()
    assert len(outs) == 1 and outs[0]["decision_id"] == "2026-05-01:005930.KS"
    assert outs[0]["gross_excess"] == pytest.approx(0.08)                          # 원 초과
    assert outs[0]["fwd_excess"] == pytest.approx(0.08 - rt) and outs[0]["success"] is True  # net(왕복비용 차감)
    assert outs[0]["fwd_mdd"] == pytest.approx(0.06) and outs[0]["idx_fwd_mdd"] == pytest.approx(0.03)

    # 멱등: 재실행해도 중복 outcome 없음
    assert L.backfill_outcomes(lg, price_fn=fake_price) == 0
    assert len(lg.read_outcomes()) == 1

    # 학습셋 조인(성숙분만)
    ts = lg.training_set()
    assert len(ts) == 1 and ts[0]["fwd_excess"] == pytest.approx(0.08 - rt)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
