#!/usr/bin/env python3
"""test_index_backtest.py — 생존편향 제거 백테스트 순수 함수 (무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backtest"))   # conftest 규약(바레 import)


def test_portfolio_metrics_basic():
    from index_strategy_backtest import portfolio_metrics
    nav = [1.0, 1.1, 1.0, 1.2]            # +10% → -9.09% → +20%
    m = portfolio_metrics(nav)
    assert m["total_ret"] == 20.0          # 1.2/1.0 - 1
    assert m["mdd"] == 9.1                  # peak 1.1 → trough 1.0
    assert m["n"] == 3


def test_portfolio_metrics_empty():
    from index_strategy_backtest import portfolio_metrics
    assert portfolio_metrics([])["total_ret"] == 0.0
    assert portfolio_metrics([1.0])["n"] == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
