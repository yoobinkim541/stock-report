"""가격 전략 원시 규칙. 네트워크·marcap 없음."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ml import kr_policy
from ml.x_strategies import (
    blend_sleeves, clenow_scores, current_weights, minervini_template,
    simulate_stop, simulate_with_cash,
)


def test_current_weights_match_live_policy():
    w = current_weights()
    p = kr_policy.DEFAULT_POLICY
    assert w == {
        "hi52": p["w_hi52"], "vol_inv": p["w_lowvol"],
        "mom12": p["w_mom12"], "rev1": p["w_mom"],
    }


def test_clenow_rewards_a_smooth_exponential():
    t = np.arange(90)
    smooth = np.log(100 * np.exp(0.001 * t))
    noisy = smooth + np.sin(t)
    scores = clenow_scores(np.column_stack([smooth, noisy]))
    assert scores[0] > scores[1] > 0


def test_minervini_template_counts_only_true_rules():
    px = pd.Series([100.0])
    score = minervini_template(
        px, ma50=pd.Series([90.0]), ma150=pd.Series([80.0]), ma200=pd.Series([70.0]),
        ma200_prev=pd.Series([60.0]), low52=pd.Series([50.0]), high52=pd.Series([110.0]),
        rs_pct=pd.Series([0.8]),
    )
    assert int(score.iloc[0]) == 8
    fail = minervini_template(
        px, ma50=pd.Series([110.0]), ma150=pd.Series([120.0]), ma200=pd.Series([130.0]),
        ma200_prev=pd.Series([140.0]), low52=pd.Series([90.0]), high52=pd.Series([200.0]),
        rs_pct=pd.Series([0.1]),
    )
    assert int(fail.iloc[0]) == 0


def test_cash_month_stays_in_the_return_series():
    idx = pd.bdate_range("2020-01-01", periods=40)
    ret = pd.DataFrame({"A": 0.01}, index=idx)
    out = simulate_with_cash(ret, {idx[0]: ["A"], idx[20]: []}, buy_bps=0, sell_bps=0)
    assert len(out) > 25
    assert out.iloc[-1] == 0.0


def test_stop_keeps_the_breach_day_then_goes_flat():
    idx = pd.bdate_range("2020-01-01", periods=6)
    adj = pd.DataFrame({"A": [100, 100, 90, 80, 70, 60]}, index=idx)
    ret = adj.pct_change().fillna(0.0)
    out = simulate_stop(ret, adj, {idx[0]: ["A"]}, stop=0.08, buy_bps=0, sell_bps=0)
    assert out.iloc[0] == 0.0
    assert abs(out.iloc[1] - (90 / 100 - 1)) < 1e-9
    assert out.iloc[2] == 0.0



def test_short_series_does_not_veto_the_walk_forward_year():
    import x_strategy_backtest as xb

    idx = pd.bdate_range("2010-01-04", periods=252 * 8)
    bench = pd.Series(0.0, index=idx)
    current = pd.Series(0.0002, index=idx)
    series = {name: current.copy() for name in xb.STRATEGY_IDS}
    series["current_price_mix"] = current
    series["jt_mom12"] = pd.Series(0.001, index=idx)
    series["han_trend"] = pd.Series(0.05, index=idx[-10:])
    folds = xb.walk_forward(series, bench)["folds"]
    assert folds
    assert {row["winner"] for row in folds} == {"jt_mom12"}


def test_blend_charges_entry_and_ignores_a_future_winner():
    idx = pd.bdate_range("2010-01-04", periods=900)
    bench = pd.Series(0.0, index=idx)
    good = pd.Series(0.002, index=idx)
    late = pd.Series(0.0, index=idx)
    late.iloc[700:] = 0.02
    out, weights = blend_sleeves(
        {"good": good, "late": late}, bench,
        buy_bps=10.0, sell_bps=20.0, lookback=400, min_obs=252,
    )
    scored = weights.loc[(weights.index >= idx[300]) & (weights.index < idx[650])]
    assert not scored.empty
    assert (scored["late"] == 0.0).all()
    assert (scored["good"] == 1.0).all()
    entry = weights.index[weights["good"] == 1.0][0]
    applied = out.index[out.index > entry][0]
    assert abs(out.loc[applied] - (0.002 - 10.0 / 1e4)) < 1e-12
    later = out.loc[out.index > applied].iloc[0]
    assert abs(later - 0.002) < 1e-12


def test_blend_holds_cash_when_no_sleeve_has_an_edge():
    idx = pd.bdate_range("2010-01-04", periods=400)
    bench = pd.Series(0.0, index=idx)
    losers = {
        "a": pd.Series(-0.001, index=idx),
        "b": pd.Series(-0.002, index=idx),
    }
    out, weights = blend_sleeves(losers, bench, buy_bps=2.0, sell_bps=20.0, lookback=300, min_obs=252)
    assert (weights.sum(axis=1) == 0.0).all()
    assert (out == 0.0).all()


def test_blend_goes_to_cash_when_the_market_gate_is_off():
    idx = pd.bdate_range("2010-01-04", periods=400)
    bench = pd.Series(0.0, index=idx)
    sleeve = pd.Series(0.002, index=idx)
    gate = pd.Series(False, index=idx)
    out, weights = blend_sleeves(
        {"a": sleeve}, bench, buy_bps=15.0, sell_bps=15.0,
        lookback=300, min_obs=252, risk_on=gate,
    )
    assert (weights.sum(axis=1) == 0.0).all()
    assert (out == 0.0).all()



def test_owner_adopts_the_measured_slack_and_rejects_a_wider_gap():
    from ml.kr_advantage import owner_adopts
    assert owner_adopts(0.4786, 0.4576, 3.6672, 0.0933, 252, 252)
    assert not owner_adopts(0.50, 0.4576, 3.6672, 0.0933, 252, 252)
    assert not owner_adopts(0.4786, 0.4576, 0.01, 0.0933, 252, 252)


def test_name_weights_leave_an_empty_sleeve_as_cash():
    from ml.kr_advantage import name_weights
    out = name_weights(
        {"a": 0.5, "b": 0.5},
        {"a": ["005930", "000660"], "b": []},
        exposure={"a": 0.5},
    )
    assert abs(out["005930"] - 0.125) < 1e-12
    assert abs(out["000660"] - 0.125) < 1e-12
    assert abs(sum(out.values()) - 0.25) < 1e-12


def test_active_mark_uses_the_last_completed_month():
    from ml.kr_advantage import active_mark
    marks = [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-10-31")]
    assert active_mark(marks, "2026-11-12") == pd.Timestamp("2026-10-31")
    assert active_mark(marks, "2026-10-31") == pd.Timestamp("2026-09-30")
    marks = [pd.Timestamp("2026-10-30"), pd.Timestamp("2026-11-10")]
    assert active_mark(marks, "2026-11-12") == pd.Timestamp("2026-10-30")


def test_book_prices_exclude_future_and_absent_quotes(monkeypatch):
    from ml.kr_advantage import _closes
    from providers import kr_market_data
    year = pd.DataFrame({
        "Code": ["000001", "000002", "000001", "000003", "000001", "000002"],
        "Date": pd.to_datetime(["2025-03-12", "2025-03-12", "2025-03-13", "2025-03-13",
                                "2025-12-30", "2025-12-30"]),
        "Close": [10, 20, 12, np.inf, 100, 200],
    })
    monkeypatch.setattr(kr_market_data, "_marcap_year", lambda y: year)
    assert _closes({"000001", "000002", "000003"}, pd.Timestamp("2025-03-13")) == {"000001": 12}


def test_future_rows_cannot_make_a_stale_book_current(monkeypatch):
    from ml import kr_advantage
    kb, _ = kr_advantage._runners()
    stale = pd.DataFrame({"000001": [0, 1]}, index=pd.to_datetime(["2025-01-02", "2025-12-30"]))
    monkeypatch.setattr(kb, "build_panels", lambda *args: {"ret": stale})
    book = kr_advantage.current_book("2025-03-15")
    assert not book["ok"] and "2025-01-02" in book["error"]
