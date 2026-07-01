"""ml/adaptive/costs.py 단위 테스트 (순수·무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml.adaptive import costs


def test_order_cost_kr_sell_includes_tax():
    # KR 매도 20bps: 1,000,000 × 0.002 = 2,000 (수수료+증권거래세)
    assert abs(costs.order_cost(1_000_000, "sell", "KR") - 2000.0) < 1e-6
    # KR 매수 2bps: 1,000,000 × 0.0002 = 200
    assert abs(costs.order_cost(1_000_000, "buy", "KR") - 200.0) < 1e-6
    # 매도가 매수보다 비쌈 (거래세) → 회전율 민감
    assert costs.order_cost(1e6, "sell", "KR") > costs.order_cost(1e6, "buy", "KR")


def test_order_cost_us_symmetric():
    assert abs(costs.order_cost(10_000, "buy", "US") - 15.0) < 1e-6
    assert abs(costs.order_cost(10_000, "sell", "US") - 15.0) < 1e-6


def test_round_trip_frac():
    assert abs(costs.round_trip_frac("KR") - 0.0022) < 1e-9   # (2+20)/1e4
    assert abs(costs.round_trip_frac("US") - 0.0030) < 1e-9   # (15+15)/1e4


def test_order_cost_none_and_unknown_market():
    assert costs.order_cost(None, "buy", "KR") == 0.0
    assert costs.order_cost(1000, "buy", "XX") == 0.0        # 미지 시장 → 0
    assert costs.round_trip_frac("XX") == 0.0


def test_env_override(monkeypatch):
    monkeypatch.setenv("KR_MOCK_SELL_BPS", "30")
    assert abs(costs.order_cost(1_000_000, "sell", "KR") - 3000.0) < 1e-6   # 30bps 반영
