import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest import _execution_phase_series


def test_execution_phase_series_shifts_signals_by_one_day():
    phases = pd.Series([0, 5, "bull_1", 2])

    shifted = _execution_phase_series(phases)

    assert shifted.tolist() == [0, 0, 5, "bull_1"]
