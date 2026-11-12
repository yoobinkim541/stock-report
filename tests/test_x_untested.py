"""일별 규칙의 진입·청산·룩어헤드. 합성 가격만 쓴다."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ml.x_untested import (
    closest_pairs, connors_weights, meta_decisions, paa_weights,
    simulate_signed, wilder_rsi,
)


def _dates(n, start="2018-01-01"):
    return pd.bdate_range(start, periods=n)


def test_wilder_rsi_extremes():
    idx = _dates(20)
    up = pd.DataFrame({"A": np.linspace(100, 120, 20)}, index=idx)
    down = pd.DataFrame({"A": np.linspace(120, 100, 20)}, index=idx)
    assert wilder_rsi(up, 2).iloc[-1, 0] == 100.0
    assert wilder_rsi(down, 2).iloc[-1, 0] == 0.0


def test_connors_does_not_buy_below_trend():
    idx = _dates(230)
    px = pd.DataFrame({"A": 100 - np.arange(230) * 0.2}, index=idx)
    eligible = pd.DataFrame(True, index=idx, columns=["A"])
    weights = connors_weights(px, eligible, k=5, entry=5.0)
    assert weights.to_numpy().sum() == 0.0


def test_connors_enters_on_dip_and_exits_above_five_day():
    idx = _dates(230)
    px = pd.Series(100 * (1.001 ** np.arange(230)), index=idx)
    px.iloc[209] = px.iloc[208] - 4.0
    for i in range(210, 220):
        px.iloc[i] = px.iloc[i - 1] * 1.03
    frame = pd.DataFrame({"A": px}, index=idx)
    eligible = pd.DataFrame(True, index=idx, columns=["A"])
    weights = connors_weights(frame, eligible, k=5, entry=5.0)
    assert weights.iloc[209, 0] == 1.0
    assert weights.iloc[208, 0] == 0.0
    assert weights.iloc[215:220, 0].sum() == 0.0


def test_connors_signal_ignores_future_price():
    idx = _dates(230)
    px = pd.Series(100 * (1.001 ** np.arange(230)), index=idx)
    px.iloc[209] = px.iloc[208] - 4.0
    frame = pd.DataFrame({"A": px, "B": px * 1.01}, index=idx)
    eligible = pd.DataFrame(True, index=idx, columns=frame.columns)
    before = connors_weights(frame, eligible)
    later = frame.copy()
    later.iloc[-1] = later.iloc[-1] * 3
    after = connors_weights(later, eligible)
    pd.testing.assert_frame_equal(before.iloc[:-1], after.iloc[:-1])


def test_connors_caps_slots():
    idx = _dates(220)
    px = pd.Series(100 * (1.001 ** np.arange(220)), index=idx)
    px.iloc[209] = px.iloc[208] - 4.0
    frame = pd.DataFrame({c: px for c in "ABCDE"}, index=idx)
    eligible = pd.DataFrame(True, index=idx, columns=frame.columns)
    weights = connors_weights(frame, eligible, k=2, entry=5.0)
    held = int((weights.iloc[209] > 0).sum())
    assert held == 2
    assert abs(weights.iloc[209].sum() - 1.0) < 1e-9


def test_identical_series_is_the_closest_pair():
    idx = _dates(40)
    base = pd.Series(np.linspace(100, 130, 40), index=idx)
    prices = pd.DataFrame({
        "A": base,
        "B": base.copy(),
        "C": base * 1.4 + np.linspace(0, 20, 40),
    })
    pairs = closest_pairs(prices, 1)
    assert set(pairs[0][:2]) == {"A", "B"}


def test_paa_hides_in_protection_when_risky_momentum_is_negative():
    mom = pd.Series({"SPY": -0.1, "EFA": -0.2, "TLT": 0.05, "GLD": 0.01})
    weights = paa_weights(mom, ["SPY", "EFA"], ["TLT", "GLD"])
    assert "SPY" not in weights
    assert abs(weights["TLT"] - 1.0) < 1e-9


def test_paa_stays_in_cash_when_protection_is_also_negative():
    mom = pd.Series({"SPY": -0.1, "TLT": -0.05})
    assert paa_weights(mom, ["SPY"], ["TLT"]) == {}


def test_meta_decision_does_not_train_on_its_own_label():
    features = np.array([[0.0], [1.0], [2.0]])
    labels = np.array([1.0, 1.0, 0.0])
    take = meta_decisions(features, labels, min_train=1)
    assert bool(take[-1]) is True


def test_signed_simulation_earns_the_next_day_not_the_signal_day():
    idx = _dates(3, "2020-01-01")
    ret = pd.DataFrame({"A": [0.0, 0.10, 0.0]}, index=idx)
    weights = pd.DataFrame({"A": [1.0, 1.0, 0.0]}, index=idx)
    out = simulate_signed(ret, weights, buy_bps=0, sell_bps=0)
    assert abs(out.iloc[0] - 0.10) < 1e-12
    costly = simulate_signed(ret, weights, buy_bps=100, sell_bps=0)
    assert abs(costly.iloc[0] - 0.09) < 1e-12
