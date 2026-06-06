import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.backtest import buy_and_hold, compare, portfolio_metrics, rule_baseline


def test_buy_and_hold_metrics():
    idx = pd.date_range("2020-01-01", periods=370, freq="D")
    close = pd.Series([100 + i for i in range(370)], index=idx, name="SPY")
    result = buy_and_hold(close)
    assert result.name == "SPY"
    assert result.cumulative_return > 3.0
    assert result.cagr is not None
    assert result.max_drawdown == 0.0
    assert result.n_days == 370


def test_rule_baseline_shifts_signal_to_avoid_lookahead():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    close = pd.Series([100.0, 200.0, 100.0, 100.0, 100.0], index=idx, name="QQQ")
    features = pd.DataFrame({"risk_on": [0.0, 1.0, 0.0, 0.0, 0.0]}, index=idx)
    result = rule_baseline(features, close, "risk_on", threshold=0.5)
    # If the rule used same-day signal, it would capture day-2's +100% move.
    # With shift(1), it instead enters for day 3 and eats the reversal.
    assert result.cumulative_return < 0
    assert result.turnover is not None


def test_portfolio_metrics_and_compare():
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    close = pd.DataFrame(
        {
            "QQQ": [100.0, 110.0, 121.0, 133.1],
            "SGOV": [100.0, 100.1, 100.2, 100.3],
        },
        index=idx,
    )
    weights = pd.DataFrame(
        {
            "QQQ": [1.0, 1.0, 0.0, 0.0],
            "SGOV": [0.0, 0.0, 1.0, 1.0],
        },
        index=idx,
    )
    result = portfolio_metrics(weights, close, name="mix")
    assert result.name == "mix"
    assert result.turnover is not None
    table = compare([result])
    assert "mix" in table.index
    assert "cum_return" in table.columns
