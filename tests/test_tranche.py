"""tests/test_tranche.py — 분할매수/분할매도 트란치 순수 로직 (무네트워크).

핵심 감사: ①회당 상한 = full/N ②N회에 진입/청산 수렴 ③N<=1·가격미상 원본유지
④min_hold·band 와 독립 합성.
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from lib.tranche import plan_tranches, tranche_cap_shares


def test_cap_shares_ceil_div():
    assert tranche_cap_shares(3_000_000, 50_000, 3) == 20      # 60주/3
    assert tranche_cap_shares(1_000_000, 30_000, 4) == 9       # 33.3주/4 = 8.3 → ceil 9
    assert tranche_cap_shares(3_000_000, 50_000, 1) == 0       # N<=1 → 상한없음
    assert tranche_cap_shares(3_000_000, 0, 3) == 0            # 가격 0 → 상한없음
    assert tranche_cap_shares(0, 50_000, 3) == 0               # 목표 0 → 상한없음


def test_buy_capped_sell_capped_small_unchanged():
    orders = [{"code": "A", "side": "buy", "qty": 60, "reason": "신규/추가"},
              {"code": "B", "side": "sell", "qty": 40, "reason": "타깃이탈"},
              {"code": "C", "side": "buy", "qty": 5, "reason": "신규/추가"}]
    px = {"A": 50_000, "B": 50_000, "C": 50_000}
    out = {o["code"]: o for o in plan_tranches(orders, 3_000_000, lambda c: px.get(c), 3)}
    assert out["A"]["qty"] == 20 and "3분할" in out["A"]["reason"]
    assert out["B"]["qty"] == 20
    assert out["C"]["qty"] == 5                                 # cap(20) 미만 → 그대로


def test_n1_and_missing_price_passthrough():
    orders = [{"code": "A", "side": "buy", "qty": 60}]
    assert plan_tranches(orders, 3e6, lambda c: 50_000, 1) == orders          # N=1 원본
    assert plan_tranches([{"code": "X", "side": "buy", "qty": 99}], 3e6,
                         lambda c: None, 3) == [{"code": "X", "side": "buy", "qty": 99}]


def test_zero_qty_dropped():
    # cap>0 이나 원 주문 qty 0 → 제외(다음 실행 재개)
    out = plan_tranches([{"code": "A", "side": "buy", "qty": 0}], 3e6, lambda c: 50_000, 3)
    assert out == []


def test_entry_converges_in_n_runs():
    """분할매수: 목표 60주를 3회 실행에 걸쳐 채운다 (매 실행 남은 갭을 상한만큼)."""
    per_pos, price, N = 3_000_000, 50_000, 3       # full=60, cap=20
    target = 60
    cur = 0
    runs = 0
    while cur < target and runs < 10:
        gap = target - cur
        orders = [{"code": "A", "side": "buy", "qty": gap, "reason": "신규/추가"}]
        planned = plan_tranches(orders, per_pos, lambda c: price, N)
        cur += planned[0]["qty"]
        runs += 1
    assert cur == target and runs == N             # 정확히 3회에 수렴


def test_exit_converges_in_n_runs():
    """분할매도: 60주 포지션을 3회 실행에 걸쳐 청산."""
    per_pos, price, N = 3_000_000, 50_000, 3       # cap=20
    cur = 60
    runs = 0
    while cur > 0 and runs < 10:
        orders = [{"code": "A", "side": "sell", "qty": cur, "reason": "타깃이탈"}]
        planned = plan_tranches(orders, per_pos, lambda c: price, N)
        cur -= planned[0]["qty"]
        runs += 1
    assert cur == 0 and runs == N                  # 정확히 3회에 청산
