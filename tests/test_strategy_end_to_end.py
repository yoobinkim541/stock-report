from __future__ import annotations

import numpy as np
import pandas as pd

from ml.strategy_studio import (
    build_strategy_report,
    builtin_strategy_presets,
    run_strategy_backtest,
)


def make_synthetic_ohlcv_panel(symbols: list[str], periods: int) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=periods, freq="D")
    return pd.DataFrame(
        {
            symbol: 100.0 + np.arange(periods, dtype=float) + offset
            for offset, symbol in enumerate(symbols)
        },
        index=index,
    )


def test_synthetic_strategy_runs_from_preset_to_promotion_report_without_network():
    preset = builtin_strategy_presets()["momentum_rank"]
    spec = {
        **preset,
        "base_symbol": "A",
        "benchmark": "A",
        "universe": {"type": "list", "symbols": ["A", "B"]},
    }
    prices = make_synthetic_ohlcv_panel(["A", "B"], periods=260)

    result = run_strategy_backtest(spec, prices, benchmark="A")
    report = build_strategy_report(result)

    assert result.ok is True
    assert result.spec["name"] == preset["name"]
    assert result.trades
    assert result.signals["execution"]["fills"]
    assert result.validation["validation_mode"] == "purged_walk_forward"
    assert result.validation["promotion_eligible"] is False
    assert set(result.promotion) >= {"accepted", "failed_checks", "activation_safe"}
    assert set(report) >= {"spec", "validation", "promotion", "trades"}
    assert report["promotion"]["accepted"] is False
    assert report["promotion"]["activation_safe"] is False
    assert all("strategy_version" in trade or "date" in trade for trade in result.trades)
