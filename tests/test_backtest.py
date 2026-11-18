import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest import _execution_phase_series


def test_execution_phase_series_shifts_signals_by_one_day():
    phases = pd.Series([0, 5, "bull_1", 2])

    shifted = _execution_phase_series(phases)

    assert shifted.tolist() == [0, 0, 5, "bull_1"]


def test_run_simulation_charges_transaction_costs():
    """바벨 시뮬레이터 — 리밸런싱 회전액·DCA 매수액에 cost_bps 를 차감한다 (종전 비용 0 →
    공통 엔진(engine.py)과 성과 비교 불가). cost_bps=0 이면 종전과 동일."""
    import numpy as np
    import pandas as pd
    import backtest as bt
    idx = pd.date_range("2020-01-01", periods=120, freq="B")
    q = 100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, 120)))
    df = pd.DataFrame({"QQQ": q, "QLD": q, "TQQQ": q, "SGOV": 100.0,
                       "phase": np.where(np.arange(120) < 60, 0, 2),
                       "drawdown": 0.0, "rsi": 50.0}, index=idx)
    free = bt.run_simulation(df, "2020-01-01", cost_bps=0.0)
    paid = bt.run_simulation(df, "2020-01-01", cost_bps=10.0)
    assert free["total_cost"] == 0.0 and paid["total_cost"] > 0
    assert paid["portfolio"][-1]["value"] < free["portfolio"][-1]["value"]
    assert paid["benchmark"][-1]["value"] < free["benchmark"][-1]["value"]   # 벤치마크도 같은 비용
