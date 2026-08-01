from __future__ import annotations

from typing import Any

import pandas as pd

from .engine import StrategyRun
from .spec import StrategySpec


def build_strategy_report(run: StrategyRun, *, spec: dict[str, Any] | StrategySpec | None = None) -> dict[str, Any]:
    strategy = StrategySpec.from_dict(spec or run.spec)
    summary = {
        "name": strategy.name,
        "market": strategy.market,
        "timeframe": strategy.timeframe,
        "base_symbol": strategy.base_symbol,
        "trade_count": run.metrics.get("trade_count", 0),
        "cagr": run.metrics.get("cagr"),
        "max_drawdown": run.metrics.get("max_drawdown"),
        "sharpe": run.metrics.get("sharpe"),
        "turnover": run.metrics.get("turnover"),
        "benchmark_excess_cagr": run.metrics.get("benchmark_excess_cagr"),
    }
    trade_table = pd.DataFrame(run.trades or [])
    if not trade_table.empty and "date" in trade_table.columns:
        trade_table = trade_table.sort_values(["date", "symbol"], ascending=[False, True]).reset_index(drop=True)

    warnings = list(run.warnings or [])
    if not run.ok and run.errors:
        warnings.extend(run.errors)

    return {
        "spec": strategy.to_dict(),
        "summary": summary,
        "metrics": dict(run.metrics or {}),
        "benchmark": dict(run.benchmark or {}),
        "warnings": warnings,
        "trades": trade_table.to_dict("records"),
        "equity": run.equity.copy() if isinstance(run.equity, pd.DataFrame) else pd.DataFrame(run.equity),
        "weights": run.weights.copy() if isinstance(run.weights, pd.DataFrame) else pd.DataFrame(run.weights),
        "signals": dict(run.signals or {}),
        "ok": bool(run.ok),
    }
