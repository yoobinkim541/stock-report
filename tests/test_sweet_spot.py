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
