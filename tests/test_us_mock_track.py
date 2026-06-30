#!/usr/bin/env python3
"""test_us_mock_track.py — US 모의 리밸런스 순수함수 (무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import us_mock_track as T


def _orders(plan):
    return {(o["symbol"], o["side"]): o["qty"] for o in plan}


def test_plan_buys_top_n_whole_shares():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9},
            {"ticker": "B", "price": 50, "policy_score": 0.8},
            {"ticker": "C", "price": 50, "policy_score": 0.1}]
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=2000, max_positions=2))
    assert o.get(("A", "buy")) == 10 and o.get(("B", "buy")) == 20    # per=1000, 정수주
    assert ("C", "buy") not in o                                       # 컷오프 밖


def test_plan_sells_off_target_first():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {"X": {"shares": 5}}, budget_usd=1000, max_positions=1))
    assert o.get(("X", "sell")) == 5 and o.get(("A", "buy")) == 10


def test_plan_cash_cap():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=10000, max_positions=1, cash_usd=300))
    assert o.get(("A", "buy")) == 3                                    # 현금 $300 한도 (버퍼 기본 1.0)


def test_plan_cash_buffer_leaves_headroom():
    """cash_buffer<1 이면 주문가능금액의 일부만 사용 — '주문가능금액 부족' 거부 방지."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    # 현금 $1000, 버퍼 0.9 → $900 사용 → 9주($900), 풀(10주) 아님 → 실집행 여유 확보
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=10000, max_positions=1,
                                 cash_usd=1000, cash_buffer=0.9))
    assert o.get(("A", "buy")) == 9


def test_plan_budget_zero_no_buys():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    assert T.plan_rebalance(sigs, {}, budget_usd=0, max_positions=1) == []


def test_classify_kind():
    assert T._classify_kind("buy", 3, 0) == "편입"
    assert T._classify_kind("buy", 3, 5) == "증액"
    assert T._classify_kind("sell", 5, 5) == "퇴출"
    assert T._classify_kind("sell", 2, 5) == "감액"


def test_quote_fn_sizes_at_live_ask():
    """라이브 호가(ask) 주입 시 실제 체결가로 사이징 — 신호가보다 비싸면 주수↓."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9},
            {"ticker": "B", "price": 100, "policy_score": 0.8}]
    # A 는 ask $200(신호가의 2배) → per $1000 에 5주. B 는 호가 없음(None) → 신호가 100 → 10주.
    qfn = lambda sym, side: 200.0 if sym == "A" else None
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=2000, max_positions=2, quote_fn=qfn))
    assert o.get(("A", "buy")) == 5 and o.get(("B", "buy")) == 10


def test_quote_fn_none_is_baseline():
    """quote_fn=None 이면 기존 정적 사이징과 동일(회귀 보장)."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    base = _orders(T.plan_rebalance(sigs, {}, budget_usd=1000, max_positions=1))
    none = _orders(T.plan_rebalance(sigs, {}, budget_usd=1000, max_positions=1, quote_fn=None))
    assert base == none == {("A", "buy"): 10}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
