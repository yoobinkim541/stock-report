import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest_multi import make_synthetic


def test_make_synthetic_preserves_shape_and_starts_at_100():
    base = pd.Series([100.0, 110.0, 121.0], index=pd.date_range("2024-01-01", periods=3))

    synth = make_synthetic(base, mult=2.0, annual_drag=0.04)

    assert synth.index.equals(base.index)
    assert len(synth) == len(base)
    assert synth.iloc[0] == 100.0
    assert synth.notna().all()
    assert (synth > 0).all()
    assert synth.iloc[-1] > synth.iloc[0]
