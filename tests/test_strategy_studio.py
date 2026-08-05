import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.strategy_studio import (
    StrategySpec,
    apply_strategy_patch,
    build_strategy_report,
    builtin_strategy_presets,
    compile_strategy,
    diff_strategy_specs,
    run_strategy_backtest,
)


def _prices() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    values = [
        100, 98, 96, 94, 92, 91, 90, 91, 93, 95,
        97, 99, 101, 103, 105, 108, 111, 114, 116, 118,
        120, 119, 117, 115, 113, 111, 109, 107, 105, 103,
        101, 99, 97, 96, 95, 94, 93, 94, 96, 98,
    ]
    return pd.DataFrame({"QQQ": values}, index=idx)


def test_compile_strategy_reads_multi_field_symbol_columns():
    """실제 _load_prices() 산출 형식(SYMBOL__field, 소문자 field) 재현 — price panel is empty 회귀."""
    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    prices = pd.DataFrame({
        "QQQ__open": range(100, 110),
        "QQQ__high": range(101, 111),
        "QQQ__low": range(99, 109),
        "QQQ__close": [100.0 + i for i in range(10)],
        "QQQ__volume": [1000] * 10,
    }, index=idx)
    spec = {"name": "test", "base_symbol": "QQQ", "universe": {"type": "list", "symbols": ["QQQ"]}, "indicators": [], "rules": {}}

    compiled = compile_strategy(spec, prices)

    assert compiled.errors == []
    assert not compiled.prices.empty
    assert list(compiled.prices["QQQ"]) == [100.0 + i for i in range(10)]


def test_builtin_rsi_cash_preset_runs_and_reports_trades():
    spec = builtin_strategy_presets()["rsi_cash"]
    run = run_strategy_backtest(spec, _prices(), benchmark="QQQ")
    report = build_strategy_report(run)

    assert run.ok is True
    assert run.metrics["trade_count"] > 0
    assert run.benchmark["symbol"] == "QQQ"
    assert any(trade["action"] in {"trim_half", "exit_all", "enter_long"} for trade in run.trades)
    assert report["summary"]["name"] == "RSI 현금화 프리셋"
    assert report["summary"]["trade_count"] == run.metrics["trade_count"]


def test_unsupported_indicator_is_rejected():
    spec = {
        "name": "bad",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "x", "kind": "python", "code": "import os"}],
        "rules": {"entry": [], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 0, "slippage_bps": 0, "spread_bps": 0},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass"},
    }

    with pytest.raises(ValueError, match="unsupported"):
        StrategySpec.from_dict(spec).validate()


def test_ema_crossover_strategy_runs_with_benchmark():
    spec = {
        "name": "EMA crossover",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [
            {"name": "ema_fast", "kind": "ema", "period": 3, "source": "close", "output": "ema_fast"},
            {"name": "ema_slow", "kind": "ema", "period": 5, "source": "close", "output": "ema_slow"},
        ],
        "rules": {
            "entry": [{"field": "close", "op": "cross_above", "ref": "ema_fast", "label": "trend_entry"}],
            "exit": [{"field": "close", "op": "cross_below", "ref": "ema_slow", "label": "trend_exit"}],
        },
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 1},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass", "min_trades": 1},
    }
    idx = pd.date_range("2026-01-01", periods=20, freq="D")
    prices = pd.DataFrame({"QQQ": [100, 99, 98, 100, 102, 105, 108, 110, 111, 112, 111, 109, 107, 105, 103, 101, 100, 99, 100, 102]}, index=idx)

    run = run_strategy_backtest(spec, prices, benchmark="QQQ")

    assert run.ok is True
    assert run.metrics["trade_count"] >= 1
    assert run.benchmark["available"] is True
    assert "benchmark_excess_cagr" in run.metrics


def test_strategy_patch_updates_nested_fields_without_clobbering_others():
    before = {
        "name": "EMA trend",
        "market": "us",
        "timeframe": "1d",
        "base_symbol": "QQQ",
        "universe": {"type": "list", "symbols": ["QQQ"]},
        "indicators": [{"name": "ema_fast", "kind": "ema", "period": 20, "source": "close"}],
        "rules": {"entry": [{"field": "close", "op": ">", "ref": "ema_fast"}], "exit": []},
        "sizing": {"type": "fixed_pct", "position_pct": 1.0},
        "costs": {"fees_bps": 5, "slippage_bps": 5, "spread_bps": 1},
        "optimization": {"params": {}, "objective": "sharpe"},
        "validation": {"mode": "single_pass"},
    }
    patch = {
        "indicators": [{"name": "ema_fast", "kind": "ema", "period": 7, "source": "close"}],
        "rules": {"entry": [{"field": "close", "op": ">", "ref": "ema_fast"}], "exit": [{"field": "close", "op": "<", "ref": "ema_fast"}]},
    }

    after = apply_strategy_patch(before, patch)
    diff = diff_strategy_specs(before, after)

    assert after["indicators"][0]["period"] == 7
    assert after["name"] == "EMA trend"
    assert after["sizing"]["position_pct"] == 1.0
    assert any(change["path"] == "indicators[0].period" for change in diff)
    assert any(change["path"] == "rules.exit[0].field" for change in diff)
