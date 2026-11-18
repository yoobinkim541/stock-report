import pandas as pd

from ml.mock_sweet_spot import apply_kr_signals, etf_orders, kr_sleeve_weights, us_n_weights


def _sig(code, mom12, lowvol, hi52, price=10000):
    return {"code": code, "price": price, "is_buy": False,
            "features": {"mom12": mom12, "lowvol": lowvol, "hi52": hi52}}


def test_kr_sleeves_split_budget_and_add_overlap():
    signals = [
        _sig("A", 1.0, 0.2, 0.1),
        _sig("B", 0.9, 0.2, 0.1),
        _sig("C", 0.1, 1.0, 1.0),
        _sig("D", 0.1, 0.9, 0.9),
        _sig("E", 0.0, 0.0, 0.0),
        _sig("F", 0.05, 0.05, 0.05),
    ]
    weights = kr_sleeve_weights(signals, k=2)
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights["A"] == 0.25
    assert weights["C"] == 0.25
    assert "E" not in weights
    marked, n = apply_kr_signals(signals)
    assert n == 5
    assert next(s for s in marked if s["code"] == "A")["is_buy"] is True
    assert next(s for s in marked if s["code"] == "E")["is_buy"] is False


def test_us_n_weights_sum_to_one_and_cut_risk_in_downtrend():
    idx = pd.bdate_range("2020-01-01", periods=280)
    up = pd.Series(range(100, 380), index=idx, dtype=float)
    down = pd.Series(range(380, 100, -1), index=idx, dtype=float)
    cash = pd.Series(100.0, index=idx)
    up_w = us_n_weights({"QQQ": up, "QLD": up, "SGOV": cash, "^VIX": cash, "^VIX3M": cash})
    down_w = us_n_weights({"QQQ": down, "QLD": down, "SGOV": cash, "^VIX": cash, "^VIX3M": cash})
    assert abs(sum(up_w.values()) - 1.0) < 1e-9
    assert up_w["QLD"] + up_w["QQQ"] > down_w["QLD"] + down_w["QQQ"]


def test_etf_orders_sell_names_outside_the_basket():
    orders = etf_orders(
        {"QLD": 0.5, "SGOV": 0.5},
        {"AAPL": {"shares": 3}, "QLD": {"shares": 1}},
        nav=1000, prices={"QLD": 100, "SGOV": 100}, band=0,
    )
    assert orders[0] == {"symbol": "AAPL", "side": "sell", "qty": 3, "reason": "스위트스팟이탈"}
    assert any(o["symbol"] == "SGOV" and o["side"] == "buy" for o in orders)


def _kr_plan(signals, positions, n):
    import importlib
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))
    kt = importlib.import_module("kiwoom_mock_track")
    return kt.plan_rebalance(signals, positions, 10_000_000, n, exit_buffer=2)


def test_kr_exit_buffer_keeps_held_boundary_name_without_topping_up():
    """상위 k 밖으로 막 밀려난 보유 종목은 유예(매도·증액 없음), 한참 밖이면 판다."""
    signals = [_sig(c, m, v, h) for c, m, v, h in (
        ("A", 1.0, 1.0, 1.0), ("B", 0.9, 0.9, 0.9), ("C", 0.5, 0.5, 0.5),
        ("D", 0.4, 0.4, 0.4), ("E", 0.0, 0.0, 0.0))]
    positions = {"C": {"shares": 100, "cur_price": 10000}, "E": {"shares": 50, "cur_price": 10000}}

    marked, n = apply_kr_signals(signals, k=2, exit_buffer=1)
    by = {s["code"]: s for s in marked}
    orders = {(o["code"], o["side"]) for o in _kr_plan(marked, positions, n)}

    assert n == 2
    assert by["C"]["selection_basis"].startswith("스위트스팟 유예")
    assert ("C", "sell") not in orders and ("C", "buy") not in orders
    assert ("E", "sell") in orders                       # 3위 밖(k+buffer) — 청산
    assert ("A", "buy") in orders and ("B", "buy") in orders
    assert by["A"]["selection_basis"].startswith("스위트스팟 선정")


def test_kr_without_exit_buffer_sells_boundary_name():
    signals = [_sig(c, m, v, h) for c, m, v, h in (
        ("A", 1.0, 1.0, 1.0), ("B", 0.9, 0.9, 0.9), ("C", 0.5, 0.5, 0.5))]
    marked, n = apply_kr_signals(signals, k=2)
    orders = {(o["code"], o["side"]) for o in _kr_plan(marked, {"C": {"shares": 100}}, n)}
    assert ("C", "sell") in orders


def test_etf_orders_scale_buys_to_available_cash():
    weights = {"SGOV": 0.5, "GLD": 0.5}
    prices = {"SGOV": 100.0, "GLD": 400.0}
    orders = etf_orders(weights, {}, 200_000, prices, cash=50_000)
    buys = {o["symbol"]: o["qty"] for o in orders if o["side"] == "buy"}
    assert buys == {"SGOV": 250, "GLD": 62}             # 각 목표의 1/4 — 같은 비율로 축소
    assert sum(q * prices[s] for s, q in buys.items()) <= 50_000


def test_etf_orders_skip_buys_below_min_cash_but_keep_sells():
    orders = etf_orders({"SGOV": 1.0}, {"QQQ": {"shares": 3}}, 100_000, {"SGOV": 100.0},
                        cash=300, min_cash=500)
    assert orders == [{"symbol": "QQQ", "side": "sell", "qty": 3, "reason": "스위트스팟이탈"}]
