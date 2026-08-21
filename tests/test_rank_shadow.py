"""tests/test_rank_shadow.py — 랭킹 섀도 원장 (순수·무네트워크).

콜드스타트 정체의 근본 원인: 매일 20종목을 점수화해놓고 실제 주문된 ~3건만 원장에 남겨
17건을 버린다. 게다가 상위 3개만 기록하면 policy_score 분산이 잘려(구간 제한) IC 가
0 쪽으로 감쇠 — "엣지 없음"이 아니라 "측정 불가"가 된다. 전 후보를 섀도로 남겨
선택편향 없는 전 구간 IC 를 얻는다(라이브 회전율·비용 현실성은 그대로 유지).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import rank_shadow


def _sigs():
    return [
        {"ticker": "A.KS", "code": "A", "policy_score": 0.9, "price": 100, "is_buy": True,
         "features": {"ranker": 0.8}, "action": "매수"},
        {"ticker": "B.KS", "code": "B", "policy_score": 0.5, "price": 200, "is_buy": True,
         "features": {"ranker": 0.4}, "action": "매수"},
        {"ticker": "C.KS", "code": "C", "policy_score": 0.2, "price": 300, "is_buy": False,
         "features": {"ranker": 0.1}, "action": "관망"},
    ]


class _FakeLedger:
    def __init__(self):
        self.rows = []

    def log_decision(self, rec):
        self.rows.append(rec)


def test_logs_every_candidate_not_just_traded():
    led = _FakeLedger()
    n = rank_shadow.log_ranked_candidates(led, _sigs(), today="2026-08-21", market="KR")
    assert n == 3
    assert [r["ticker"] for r in led.rows] == ["A.KS", "B.KS", "C.KS"]


def test_records_rank_and_universe_size():
    led = _FakeLedger()
    rank_shadow.log_ranked_candidates(led, _sigs(), today="2026-08-21", market="KR")
    assert [r["rank"] for r in led.rows] == [1, 2, 3]
    assert all(r["universe"] == 3 for r in led.rows)


def test_side_is_observation_so_live_stats_unaffected():
    """섀도는 실제 주문이 아니므로 '관측' — 라이브 편입/증액 통계와 섞이면 안 됨."""
    led = _FakeLedger()
    rank_shadow.log_ranked_candidates(led, _sigs(), today="2026-08-21", market="KR")
    assert {r["side"] for r in led.rows} == {"관측"}
    assert all(r["shadow"] is True for r in led.rows)


def test_preserves_policy_score_and_features_for_learning():
    led = _FakeLedger()
    rank_shadow.log_ranked_candidates(led, _sigs(), today="2026-08-21", market="KR")
    assert led.rows[0]["policy_score"] == 0.9
    assert led.rows[0]["features"] == {"ranker": 0.8}
    assert led.rows[0]["ok"] is True          # 섀도는 항상 '집행됨' 취급(주문 실패 개념 없음)


def test_skips_rows_without_score():
    led = _FakeLedger()
    n = rank_shadow.log_ranked_candidates(
        led, [{"ticker": "X.KS", "code": "X", "policy_score": None}], today="2026-08-21", market="KR")
    assert n == 0 and led.rows == []


def test_score_spread_full_universe_vs_traded_top_n():
    """핵심 근거: 상위 N 만 남기면 점수 분산이 잘려 IC 가 구조적으로 감쇠한다."""
    import statistics as st
    sigs = [{"ticker": f"T{i}.KS", "code": f"T{i}", "policy_score": i / 20.0} for i in range(20)]
    full = st.pstdev([s["policy_score"] for s in sigs])
    top3 = st.pstdev([s["policy_score"] for s in sorted(
        sigs, key=lambda s: -s["policy_score"])[:3]])
    assert full > top3 * 3     # 전 구간 분산이 상위3 대비 압도적으로 큼


def test_empty_signals_graceful():
    led = _FakeLedger()
    assert rank_shadow.log_ranked_candidates(led, [], today="2026-08-21", market="KR") == 0
    assert rank_shadow.log_ranked_candidates(led, None, today="2026-08-21", market="KR") == 0


def test_never_raises_on_ledger_failure():
    """원장 장애가 라이브 주문 경로를 막으면 안 됨(섀도는 부가 기능)."""
    class _Boom:
        def log_decision(self, rec):
            raise RuntimeError("disk full")
    assert rank_shadow.log_ranked_candidates(_Boom(), _sigs(), today="2026-08-21", market="KR") == 0
