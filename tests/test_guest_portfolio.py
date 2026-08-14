"""tests/test_guest_portfolio.py — 게스트 본인 포트폴리오 입력값 검증 (감사 #25)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import guest_portfolio as gp


def test_add_holding_rejects_negative_shares():
    with pytest.raises(ValueError):
        gp.add_holding("guest-neg-shares", "QQQ", -10, 500.0)
    assert "QQQ" not in gp.list_holdings("guest-neg-shares")


def test_add_holding_rejects_negative_price():
    with pytest.raises(ValueError):
        gp.add_holding("guest-neg-price", "QQQ", 10, -500.0)
    assert "QQQ" not in gp.list_holdings("guest-neg-price")


def test_add_holding_rejects_zero_shares_and_price():
    with pytest.raises(ValueError):
        gp.add_holding("guest-zero", "QQQ", 0, 500.0)
    with pytest.raises(ValueError):
        gp.add_holding("guest-zero", "QQQ", 10, 0)
    assert "QQQ" not in gp.list_holdings("guest-zero")


def test_add_holding_accepts_positive_values():
    h = gp.add_holding("guest-positive", "QQQ", 10, 500.0)
    assert h["shares"] == 10 and h["avg_price"] == 500.0
