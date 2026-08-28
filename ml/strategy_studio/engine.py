from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Any

import numpy as np
import pandas as pd

from ml.backtest import buy_and_hold, cagr as _cagr, max_drawdown as _max_drawdown, sharpe_ratio as _sharpe_ratio
from ml.backtest import portfolio_turnover as _portfolio_turnover

from .spec import StrategySpec


_CLOSE_LIKE_FIELDS = {"close", "price", "adj close", "adjclose", "adj_close"}
_PRICE_FIELD_SUFFIXES = {"OPEN", "HIGH", "LOW", "CLOSE", "ADJ CLOSE", "ADJCLOSE", "ADJ_CLOSE", "VOLUME"}
_SUPPORTED_ACTIONS = {
    "enter_long",
    "enter",
    "buy",
    "exit_all",
    "exit",
    "sell",
    "trim_half",
}


@dataclass(slots=True)
class CompiledStrategy:
    spec: StrategySpec
    prices: pd.DataFrame
    store: pd.DataFrame
    contexts: dict[str, dict[str, pd.Series]]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StrategyRun:
    ok: bool
    spec: dict[str, Any]
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    equity: pd.DataFrame
    benchmark: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    weights: pd.DataFrame = field(default_factory=pd.DataFrame)
    signals: dict[str, Any] = field(default_factory=dict)
    allocation_diagnostics: list[dict[str, object]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    promotion: dict[str, Any] = field(default_factory=dict)


def compile_strategy(spec: dict[str, Any] | StrategySpec, prices: pd.DataFrame) -> CompiledStrategy:
    strategy = StrategySpec.from_dict(spec)
    warnings = list(strategy.validate())
    price_store = _normalize_price_store(prices)
    _copy_data_provenance_attrs(price_store, prices)
    close_panel = _close_panel_from_store(price_store)
    _copy_data_provenance_attrs(close_panel, price_store)
    if close_panel.empty:
        return CompiledStrategy(strategy, close_panel, price_store, {}, warnings=warnings, errors=["price panel is empty"])

    tradeable = _resolve_universe_symbols(strategy, close_panel)
    if not tradeable:
        return CompiledStrategy(strategy, close_panel, price_store, {}, warnings=warnings, errors=["no tradable symbols found"])

    contexts: dict[str, dict[str, pd.Series]] = {}
    for symbol in tradeable:
        series = close_panel[symbol].astype(float).ffill()
        ctx: dict[str, pd.Series] = {"close": series, "price": series}
        _apply_indicators(strategy, symbol, ctx, price_store, warnings)
        contexts[symbol] = ctx

    _validate_rule_fields(strategy, contexts, warnings)
    return CompiledStrategy(strategy, close_panel, price_store, contexts, warnings=warnings, errors=[])


def build_signal_panel(strategy: StrategySpec, compiled: CompiledStrategy) -> Any:
    """Build a declared signal without changing the legacy rule simulator."""

    from .signals import build_signal_panel as _build_signal_panel

    return _build_signal_panel(strategy, compiled)


def run_strategy_backtest(
    spec: dict[str, Any] | StrategySpec,
    prices: pd.DataFrame,
    *,
    benchmark: str | None = None,
) -> StrategyRun:
    compiled = compile_strategy(spec, prices)
    strategy = compiled.spec

    if compiled.errors:
        return _attach_validation(StrategyRun(
            ok=False,
            spec=strategy.to_dict(),
            metrics={},
            trades=[],
            equity=pd.DataFrame(),
            benchmark={"symbol": benchmark or strategy.base_symbol or "", "available": False},
            warnings=compiled.warnings,
            errors=compiled.errors,
        ), strategy)

    close_panel = compiled.prices
    allocation_diagnostics: list[dict[str, object]] = []
    if strategy.uses_allocation_path:
        from .execution import to_jsonable

        signal_panel = build_signal_panel(strategy, compiled)
        signal_diagnostics = list(signal_panel.diagnostics)
        compiled.warnings.extend(signal_diagnostics)
        if not signal_panel.has_valid_signals:
            errors = [f"signal provider failed: {diagnostic}" for diagnostic in signal_diagnostics]
            if not errors:
                errors = ["signal provider returned no valid scores"]
            return _attach_validation(StrategyRun(
                ok=False,
                spec=strategy.to_dict(),
                metrics={},
                trades=[],
                equity=pd.DataFrame(),
                benchmark={"symbol": benchmark or strategy.base_symbol or "", "available": False},
                warnings=compiled.warnings,
                errors=errors,
                signals={"panel": to_jsonable(signal_panel.to_dict())},
            ), strategy)

        from .allocation import allocate_targets

        allocation = allocate_targets(
            signal_panel,
            close_panel.pct_change(),
            _allocation_config(strategy),
            strategy.costs,
        )
        compiled.warnings.extend(allocation.warnings)
        weights = allocation.weights
        allocation_diagnostics = list(allocation.diagnostics)
        signal_trace = to_jsonable({
            "panel": to_jsonable(signal_panel.to_dict()),
            "targets": to_jsonable(weights),
            "allocation": to_jsonable(allocation.to_dict()),
            "allocation_diagnostics": allocation_diagnostics,
            "allocation_warnings": list(allocation.warnings),
        })
        trade_rows: list[dict[str, Any]] = []
    else:
        weights, trade_rows, signal_trace = _simulate_strategy(strategy, compiled.contexts, close_panel, compiled.warnings)
    if weights.empty:
        return _attach_validation(StrategyRun(
            ok=False,
            spec=strategy.to_dict(),
            metrics={},
            trades=trade_rows,
            equity=pd.DataFrame(),
            benchmark={"symbol": benchmark or strategy.base_symbol or "", "available": False},
            warnings=compiled.warnings,
            errors=["no weights were produced"],
            weights=weights,
            signals=signal_trace,
        ), strategy)

    if strategy.uses_allocation_path:
        from .execution import run_execution_backtest

        execution = run_execution_backtest(
            weights,
            _execution_bars(compiled, weights.columns),
            _execution_config(strategy),
        )
        compiled.warnings.extend(execution.warnings)
        signal_trace["execution"] = execution.to_dict()
        return _build_execution_run(
            strategy,
            close_panel,
            weights,
            execution,
            signal_trace,
            benchmark=benchmark,
            warnings=compiled.warnings,
            allocation_diagnostics=allocation_diagnostics,
        )

    return _build_run(
        strategy,
        close_panel,
        weights,
        trade_rows,
        signal_trace,
        benchmark=benchmark,
        warnings=compiled.warnings,
        allocation_diagnostics=allocation_diagnostics,
    )


def _execution_config(strategy: StrategySpec):
    """Merge profile defaults, explicit execution fields, and legacy costs."""

    from dataclasses import asdict

    from .execution import ExecutionConfig, execution_defaults

    execution_payload = dict(strategy.execution or {})
    profile = str(execution_payload.get("profile") or strategy.execution_profile or "bar").strip().lower()
    session = str(execution_payload.get("session") or "regular").strip().lower()
    defaults = execution_defaults(profile, session=session)
    payload = asdict(defaults)
    if "latency_bars" not in execution_payload and "latency_ms" in execution_payload:
        try:
            payload["latency_bars"] = 0 if float(execution_payload["latency_ms"]) == 0 else 1
        except (TypeError, ValueError):
            payload["latency_bars"] = defaults.latency_bars
    payload.update(execution_payload)
    costs = strategy.costs or {}
    for key in ("fees_bps", "slippage_bps", "spread_bps"):
        if key not in execution_payload and key in costs:
            payload[key] = costs[key]
    if not any(key in execution_payload or key in costs for key in ("fees_bps", "slippage_bps", "spread_bps")):
        payload["fees_bps"] = _strategy_cost_bps(costs)
    if "allow_short" not in execution_payload and "allow_short" in (strategy.portfolio or {}):
        payload["allow_short"] = bool(strategy.portfolio["allow_short"])
    payload["profile"] = profile
    payload["session"] = session
    payload["run_id"] = f"strategy-{strategy.id or strategy.name}"
    return ExecutionConfig.from_dict(payload)


def _execution_bars(compiled: CompiledStrategy, symbols: Any) -> dict[str, pd.DataFrame]:
    """Build per-symbol OHLCV frames from the normalized strategy price store."""

    bars: dict[str, pd.DataFrame] = {}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).upper().strip()
        close = _field_series(compiled.store, symbol, "close")
        if close is None and symbol in compiled.prices.columns:
            close = compiled.prices[symbol]
        if close is None:
            continue
        frame = pd.DataFrame(index=close.index)
        for field_name in ("open", "high", "low", "close"):
            series = _field_series(compiled.store, symbol, field_name)
            frame[field_name] = pd.to_numeric(series if series is not None else close, errors="coerce")
        volume = _field_series(compiled.store, symbol, "volume")
        frame["volume"] = pd.to_numeric(volume, errors="coerce") if volume is not None else np.inf
        bars[symbol] = frame
    return bars


def _build_execution_run(
    strategy: StrategySpec,
    close_panel: pd.DataFrame,
    weights: pd.DataFrame,
    execution: Any,
    signal_trace: dict[str, Any],
    *,
    benchmark: str | None,
    warnings: list[str],
    allocation_diagnostics: list[dict[str, object]] | None = None,
) -> StrategyRun:
    equity = execution.equity.copy()
    if isinstance(equity.index, pd.DatetimeIndex) and equity.index.tz is not None:
        equity.index = equity.index.tz_convert(None)
    aligned = weights.reindex(close_panel.index).fillna(0.0)
    net_ret = equity["net_return"] if "net_return" in equity else pd.Series(0.0, index=equity.index)
    cost_drag = equity["cost_drag"] if "cost_drag" in equity else pd.Series(0.0, index=equity.index)
    metrics = _metrics_from_series(
        equity["nav"] if "nav" in equity else pd.Series(dtype="float64"),
        net_ret,
        aligned,
        cost_drag,
        execution.trades,
        equity.index,
    )
    metrics.update(execution.summary)
    bench = _benchmark_summary(close_panel, benchmark or strategy.base_symbol or "", warnings)
    if bench.get("available"):
        bench_nav = bench.get("equity")
        if isinstance(bench_nav, pd.Series):
            equity["benchmark_nav"] = bench_nav.reindex(equity.index).ffill()
        metrics["benchmark_excess_cagr"] = _excess_cagr(metrics.get("cagr"), bench.get("cagr"))
    else:
        metrics["benchmark_excess_cagr"] = None
    if strategy.validation.get("mode") == "single_pass" and len(execution.trades) < int(strategy.validation.get("min_trades") or 0):
        warnings.append(f"trade count below validation minimum: {len(execution.trades)} < {int(strategy.validation.get('min_trades') or 0)}")
    errors = compiled_errors(metrics, bench)
    run = StrategyRun(
        ok=not errors,
        spec=strategy.to_dict(),
        metrics=metrics,
        trades=execution.trades,
        equity=equity,
        benchmark=bench,
        warnings=warnings,
        errors=errors,
        weights=aligned,
        signals=signal_trace,
        allocation_diagnostics=list(allocation_diagnostics or []),
    )
    return _attach_validation(run, strategy)


def _build_run(
    strategy: StrategySpec,
    close_panel: pd.DataFrame,
    weights: pd.DataFrame,
    trade_rows: list[dict[str, Any]],
    signal_trace: dict[str, Any],
    *,
    benchmark: str | None,
    warnings: list[str],
    allocation_diagnostics: list[dict[str, object]] | None = None,
) -> StrategyRun:
    params = strategy.costs or {}
    cost_bps = _strategy_cost_bps(params)

    aligned = weights.reindex(close_panel.index).fillna(0.0)
    rets = close_panel.pct_change().fillna(0.0)
    applied = aligned.shift(1).fillna(0.0)
    gross_ret = (applied * rets[applied.columns.intersection(rets.columns)]).sum(axis=1)
    turnover = aligned.diff().abs().sum(axis=1).fillna(aligned.iloc[0].abs().sum() if len(aligned) else 0.0)
    cost_drag = turnover * (cost_bps / 10000.0)
    net_ret = gross_ret - cost_drag
    gross_nav = (1 + gross_ret).cumprod() * 100.0
    nav = (1 + net_ret).cumprod() * 100.0

    equity = pd.DataFrame(
        {
            "nav": nav,
            "gross_nav": gross_nav,
            "gross_return": gross_ret,
            "net_return": net_ret,
            "cost_drag": cost_drag,
            "turnover": turnover,
            "exposure": aligned.abs().sum(axis=1),
        },
        index=close_panel.index,
    )

    metrics = _metrics_from_series(nav, net_ret, aligned, cost_drag, trade_rows, close_panel.index)
    bench = _benchmark_summary(close_panel, benchmark or strategy.base_symbol or "", warnings)

    if bench.get("available"):
        bench_nav = bench.get("equity")
        if isinstance(bench_nav, pd.Series):
            equity["benchmark_nav"] = bench_nav.reindex(equity.index).ffill()
        metrics["benchmark_excess_cagr"] = _excess_cagr(metrics.get("cagr"), bench.get("cagr"))
    else:
        metrics["benchmark_excess_cagr"] = None

    if strategy.validation.get("mode") == "single_pass" and len(trade_rows) < int(strategy.validation.get("min_trades") or 0):
        warnings.append(f"trade count below validation minimum: {len(trade_rows)} < {int(strategy.validation.get('min_trades') or 0)}")

    ok = not bool(compiled_errors(metrics, bench))
    errors = compiled_errors(metrics, bench)
    run = StrategyRun(
        ok=ok,
        spec=strategy.to_dict(),
        metrics=metrics,
        trades=trade_rows,
        equity=equity,
        benchmark=bench,
        warnings=warnings,
        errors=errors,
        weights=aligned,
        signals=signal_trace,
        allocation_diagnostics=list(allocation_diagnostics or []),
    )
    return _attach_validation(run, strategy)


def _attach_validation(run: StrategyRun, strategy: StrategySpec) -> StrategyRun:
    """Attach a validation preview without changing how the strategy was run."""

    from .validation import evaluate_validation_folds, promotion_gate

    mode = str((strategy.validation or {}).get("mode") or "single_pass").strip().lower()
    benchmark_symbol = str(run.benchmark.get("symbol") or "benchmark") if isinstance(run.benchmark, dict) else "benchmark"
    benchmarks = {benchmark_symbol: run.benchmark} if run.benchmark else {}
    validation_report = evaluate_validation_folds([run], benchmarks)
    if mode == "single_pass":
        validation_report.warnings.append("single_pass is preview-only; no promotion decision is activation-safe")
    else:
        validation_report.warnings.append(
            f"{mode} requested, but this backtest result contains one pass; out-of-sample folds are required for activation"
        )
    validation_report.validation_mode = mode
    validation_report.promotion_eligible = False

    gate_config = dict(strategy.validation or {})
    gate_config.update(strategy.promotion or {})
    gate_config["mode"] = mode
    decision = promotion_gate(validation_report, gate_config)
    run.validation = validation_report.to_dict()
    run.promotion = decision.to_dict()
    for warning in validation_report.warnings:
        if warning not in run.warnings:
            run.warnings.append(warning)
    return run


def _allocation_config(strategy: StrategySpec) -> dict[str, object]:
    """Merge legacy sizing defaults into an explicitly opted-in portfolio."""

    config: dict[str, object] = dict(strategy.sizing or {})
    config.update(strategy.portfolio or {})
    sizing_type = str((strategy.sizing or {}).get("type") or "fixed_pct").strip().lower()
    if "optimizer" not in config and sizing_type in {"equal_weight", "risk_budget"}:
        config["optimizer"] = sizing_type
    return config


def _simulate_strategy(
    strategy: StrategySpec,
    contexts: dict[str, dict[str, pd.Series]],
    close_panel: pd.DataFrame,
    warnings: list[str],
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    symbols = list(contexts.keys())
    index = close_panel.index
    weights = pd.DataFrame(0.0, index=index, columns=symbols, dtype="float64")
    trade_rows: list[dict[str, Any]] = []
    signal_trace: dict[str, Any] = {"entry": {}, "exit": {}, "trim": {}, "targets": {}}

    sizing_type = str((strategy.sizing or {}).get("type") or "fixed_pct").strip().lower()
    max_gross = float((strategy.sizing or {}).get("max_gross_exposure") or (strategy.validation or {}).get("max_gross_exposure") or 1.0)
    max_gross = min(10.0, max(0.0, max_gross)) or 1.0

    current = {symbol: 0.0 for symbol in symbols}
    hold_age = {symbol: 0 for symbol in symbols}
    prev_row = pd.Series(current, dtype="float64")
    for dt in index:
        row: dict[str, float] = {}
        for symbol in symbols:
            ctx = contexts[symbol]
            base_target = _target_weight_for_symbol(strategy, symbol, ctx, dt, sizing_type, warnings)
            signal_trace["targets"].setdefault(symbol, []).append(base_target)

            entry_rule = _first_matching_rule(strategy.rules.get("entry") or [], contexts, symbol, dt)
            exit_rule = _first_matching_rule(strategy.rules.get("exit") or [], contexts, symbol, dt)
            trim_rule = _first_matching_rule(strategy.rules.get("trim") or [], contexts, symbol, dt)
            signal_trace["entry"].setdefault(symbol, []).append(entry_rule.get("label") if entry_rule else None)
            signal_trace["exit"].setdefault(symbol, []).append(exit_rule.get("label") if exit_rule else None)
            signal_trace["trim"].setdefault(symbol, []).append(trim_rule.get("label") if trim_rule else None)

            prev = current[symbol]
            nxt = prev
            action = None
            reason = None

            max_hold_bars = int((strategy.validation or {}).get("max_hold_bars") or 0)
            if prev > 0:
                hold_age[symbol] += 1
            else:
                hold_age[symbol] = 0

            if exit_rule is not None and prev > 0:
                nxt = 0.0
                action = _rule_action(exit_rule, default="exit_all")
                reason = _rule_label(exit_rule, "exit")
                hold_age[symbol] = 0
            elif max_hold_bars > 0 and prev > 0 and hold_age[symbol] >= max_hold_bars:
                nxt = 0.0
                action = "time_stop_exit"
                reason = f"hold>{max_hold_bars}"
                hold_age[symbol] = 0
            elif trim_rule is not None and prev > 0:
                factor = _trim_factor(trim_rule)
                nxt = max(0.0, prev * factor)
                action = _rule_action(trim_rule, default="trim_half")
                reason = _rule_label(trim_rule, "trim")
            elif entry_rule is not None and prev <= 0:
                nxt = base_target
                action = _rule_action(entry_rule, default="enter_long")
                reason = _rule_label(entry_rule, "entry")
                hold_age[symbol] = 0

            row[symbol] = nxt
            if action and abs(nxt - prev) > 1e-12:
                trade_rows.append(
                    {
                        "date": pd.Timestamp(dt).date().isoformat(),
                        "symbol": symbol,
                        "action": action,
                        "from_weight": round(float(prev), 6),
                        "to_weight": round(float(nxt), 6),
                        "change": round(float(nxt - prev), 6),
                        "reason": reason or action,
                    }
                )
            current[symbol] = nxt

        row_series = pd.Series(row, dtype="float64")
        gross = float(row_series.abs().sum())
        if gross > max_gross > 0:
            row_series = row_series * (max_gross / gross)
            for symbol in symbols:
                current[symbol] = float(row_series.get(symbol, 0.0))
        weights.loc[dt, symbols] = row_series[symbols]

    return weights.fillna(0.0), trade_rows, signal_trace


def _target_weight_for_symbol(
    strategy: StrategySpec,
    symbol: str,
    ctx: dict[str, pd.Series],
    dt: pd.Timestamp,
    sizing_type: str,
    warnings: list[str],
) -> float:
    sizing = strategy.sizing or {}
    max_position = float(sizing.get("max_position_pct") or sizing.get("position_pct") or 1.0)
    if sizing_type == "equal_weight":
        active_symbols = max(1, len(strategy.universe.get("symbols") or [symbol]))
        return min(max_position, 1.0 / active_symbols)
    if sizing_type == "risk_budget":
        risk_per_trade = float(sizing.get("risk_per_trade") or 0.01)
        stop_pct = sizing.get("stop_loss_pct")
        if stop_pct is None:
            atr = ctx.get("atr")
            atr_multiple = float(sizing.get("atr_multiple") or 1.0)
            close = ctx.get("close")
            if atr is not None and close is not None:
                atr_value = float(atr.loc[dt]) if dt in atr.index and pd.notna(atr.loc[dt]) else None
                close_value = float(close.loc[dt]) if dt in close.index and pd.notna(close.loc[dt]) else None
                if atr_value is not None and close_value and close_value > 0:
                    stop_pct = max(1e-6, atr_multiple * atr_value / close_value)
        if stop_pct is None:
            stop_pct = float(sizing.get("fallback_stop_loss_pct") or 0.05)
        weight = risk_per_trade / max(float(stop_pct), 1e-6)
        return float(min(max_position, max(0.0, weight)))
    return float(min(max_position, max(0.0, float(sizing.get("position_pct") or 1.0))))


def _benchmark_summary(close_panel: pd.DataFrame, benchmark: str, warnings: list[str]) -> dict[str, Any]:
    symbol = str(benchmark or "").upper().strip()
    if not symbol:
        return {"symbol": "", "available": False, "warnings": warnings}
    if symbol not in close_panel.columns:
        warnings.append(f"benchmark symbol missing: {symbol}")
        return {"symbol": symbol, "available": False, "warnings": warnings}
    close = close_panel[symbol].dropna()
    if len(close) < 2:
        warnings.append(f"benchmark data too short: {symbol}")
        return {"symbol": symbol, "available": False, "warnings": warnings}
    result = buy_and_hold(close, name=symbol)
    return {
        "symbol": symbol,
        "available": True,
        "equity": close.reindex(close_panel.index).ffill(),
        "cumulative_return": result.cumulative_return,
        "cagr": result.cagr,
        "max_drawdown": result.max_drawdown,
        "sharpe": result.sharpe,
        "n_days": result.n_days,
    }


def _metrics_from_series(
    nav: pd.Series,
    net_ret: pd.Series,
    weights: pd.DataFrame,
    cost_drag: pd.Series,
    trades: list[dict[str, Any]],
    index: pd.Index,
) -> dict[str, Any]:
    nav = nav.dropna()
    if nav.empty:
        return {
            "cumulative_return": 0.0,
            "cagr": None,
            "max_drawdown": 0.0,
            "sharpe": None,
            "turnover": 0.0,
            "trade_count": 0,
            "cost_drag": 0.0,
            "net_return": 0.0,
            "gross_return": 0.0,
            "n_days": 0,
        }
    cumulative_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    returns = net_ret.dropna()
    benchmark_like = nav / nav.iloc[0]
    metrics = {
        "cumulative_return": cumulative_return,
        "cagr": _cagr(benchmark_like),
        "max_drawdown": _max_drawdown(benchmark_like),
        "sharpe": _sharpe_ratio(returns) if len(returns) > 1 else None,
        "turnover": _portfolio_turnover(weights) if len(weights) > 1 else 0.0,
        "trade_count": len(trades),
        "cost_drag": float(cost_drag.sum()),
        "net_return": float((1 + net_ret.fillna(0.0)).prod() - 1.0),
        "gross_return": float((1 + (net_ret + cost_drag).fillna(0.0)).prod() - 1.0),
        "n_days": len(index),
        "exposure_mean": float(weights.abs().sum(axis=1).mean()) if len(weights) else 0.0,
    }
    return metrics


def _excess_cagr(strategy_cagr: float | None, benchmark_cagr: float | None) -> float | None:
    if strategy_cagr is None or benchmark_cagr is None:
        return None
    return float(strategy_cagr - benchmark_cagr)


def compiled_errors(metrics: dict[str, Any], bench: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if metrics.get("n_days", 0) < 2:
        errors.append("insufficient bars")
    if bench and bench.get("symbol") and not bench.get("available") and "benchmark symbol missing" in " ".join(bench.get("warnings") or []):
        return errors
    return errors


def _normalize_price_store(prices: pd.DataFrame) -> pd.DataFrame:
    if prices is None or getattr(prices, "empty", True):
        return pd.DataFrame()
    frame = prices.copy()
    frame.index = pd.to_datetime(frame.index)
    if isinstance(frame.columns, pd.MultiIndex):
        flat: dict[str, pd.Series] = {}
        for col in frame.columns:
            if len(col) < 2:
                continue
            symbol = str(col[0]).upper().strip()
            field = str(col[1]).lower().strip().replace(" ", "_")
            if not symbol or not field:
                continue
            flat[f"{symbol}__{field}"] = pd.to_numeric(frame[col], errors="coerce")
        if not flat:
            return pd.DataFrame(index=frame.index)
        return pd.DataFrame(flat).sort_index().ffill().dropna(how="all")
    new_columns = []
    for col in frame.columns:
        name = str(col).strip()
        if "__" in name:
            symbol, field = name.split("__", 1)
            name = f"{symbol.upper().strip()}__{field.lower().strip().replace(' ', '_')}"
        elif "_" in name and name.split("_", 1)[1].upper().strip() in _PRICE_FIELD_SUFFIXES:
            symbol, field = name.split("_", 1)
            name = f"{symbol.upper().strip()}_{field.lower().strip().replace(' ', '_')}"
        else:
            name = name.upper()
        new_columns.append(name)
    frame.columns = new_columns
    return frame.sort_index().ffill().dropna(how="all")


def _copy_data_provenance_attrs(target: pd.DataFrame, source: object) -> None:
    """Keep data snapshot attrs across price-store normalization copies."""

    if not isinstance(target, pd.DataFrame) or not isinstance(getattr(source, "attrs", None), dict):
        return
    for key in ("data_snapshot", "data_snapshots", "source_coverage", "provenance"):
        if key in source.attrs:
            target.attrs[key] = source.attrs[key]


def _close_panel_from_store(store: pd.DataFrame) -> pd.DataFrame:
    if store is None or getattr(store, "empty", True):
        return pd.DataFrame()
    symbols: list[str] = []
    for raw in store.columns:
        name = str(raw).upper().strip()
        if "__" in name:
            sym = name.split("__", 1)[0]
        elif "." in name:
            sym = name.split(".", 1)[0]
        elif "_" in name and name.split("_", 1)[1] in _PRICE_FIELD_SUFFIXES:
            sym = name.split("_", 1)[0]
        else:
            sym = name
        if sym and sym not in symbols:
            symbols.append(sym)
    close_cols: dict[str, pd.Series] = {}
    for symbol in symbols:
        series = _field_series(store, symbol, "close")
        if series is None and symbol in store.columns:
            series = pd.to_numeric(store[symbol], errors="coerce")
        if series is not None:
            close_cols[symbol] = pd.to_numeric(series, errors="coerce")
    if not close_cols:
        return pd.DataFrame(index=store.index)
    return pd.DataFrame(close_cols).sort_index().ffill().dropna(how="all")


def _field_series(store: pd.DataFrame, symbol: str, field: str) -> pd.Series | None:
    symbol = str(symbol or "").upper().strip()
    field = str(field or "close").lower().strip()
    if symbol and symbol in store.columns and field in _CLOSE_LIKE_FIELDS:
        return pd.to_numeric(store[symbol], errors="coerce")
    if symbol and f"{symbol}_{field}" in store.columns:
        return pd.to_numeric(store[f"{symbol}_{field}"], errors="coerce")
    if symbol and f"{symbol}.{field}" in store.columns:
        return pd.to_numeric(store[f"{symbol}.{field}"], errors="coerce")
    if symbol and f"{symbol}__{field}" in store.columns:
        return pd.to_numeric(store[f"{symbol}__{field}"], errors="coerce")
    if len(store.columns) == 1 and field in _CLOSE_LIKE_FIELDS:
        return pd.to_numeric(store.iloc[:, 0], errors="coerce")
    return None


def _resolve_universe_symbols(strategy: StrategySpec, close_panel: pd.DataFrame) -> list[str]:
    requested = []
    universe = strategy.universe or {}
    if isinstance(universe, dict):
        requested = [str(x).upper().strip() for x in universe.get("symbols") or [] if str(x).strip()]
    if not requested and strategy.base_symbol:
        requested = [strategy.base_symbol]
    if not requested:
        requested = [str(col).upper().strip() for col in close_panel.columns]
    available = [symbol for symbol in requested if symbol in close_panel.columns]
    if not available and len(close_panel.columns) == 1:
        return [str(close_panel.columns[0]).upper().strip()]
    return available


def _apply_indicators(
    strategy: StrategySpec,
    symbol: str,
    ctx: dict[str, pd.Series],
    store: pd.DataFrame,
    warnings: list[str],
) -> None:
    for indicator in strategy.indicators:
        if not isinstance(indicator, dict):
            continue
        kind = str(indicator.get("kind") or indicator.get("name") or "").lower().strip()
        output = str(indicator.get("output") or indicator.get("outputField") or indicator.get("as") or indicator.get("name") or kind).strip()
        source_field = str(indicator.get("source") or indicator.get("field") or "close").strip()
        ind_symbol = str(indicator.get("symbol") or symbol).upper().strip()
        period = max(1, int(_safe_float(indicator.get("period") or indicator.get("window") or indicator.get("length"), 14)))
        series = _field_series(store, ind_symbol, source_field) if source_field else None
        if series is None:
            if source_field.lower() in _CLOSE_LIKE_FIELDS:
                series = ctx.get("close")
            else:
                warnings.append(f"indicator {kind} on {ind_symbol} missing field {source_field}")
                continue
        series = pd.to_numeric(series, errors="coerce").ffill()

        if kind == "rsi":
            ctx[output] = _rsi(series, period)
        elif kind == "ema":
            ctx[output] = series.ewm(span=period, adjust=False, min_periods=period).mean()
        elif kind == "sma":
            ctx[output] = series.rolling(period, min_periods=period).mean()
        elif kind == "rolling":
            method = str(indicator.get("method") or "mean").lower().strip()
            ctx[output] = _rolling(series, period, method)
        elif kind == "drawdown":
            ctx[output] = series / series.cummax() - 1.0
        elif kind == "macd":
            fast = max(1, int(_safe_float(indicator.get("fast") or 12, 12)))
            slow = max(1, int(_safe_float(indicator.get("slow") or 26, 26)))
            signal_period = max(1, int(_safe_float(indicator.get("signal") or 9, 9)))
            fast_ema = series.ewm(span=fast, adjust=False, min_periods=fast).mean()
            slow_ema = series.ewm(span=slow, adjust=False, min_periods=slow).mean()
            macd_line = fast_ema - slow_ema
            signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
            ctx[output] = macd_line
            ctx[f"{output}_signal"] = signal_line
            ctx[f"{output}_hist"] = macd_line - signal_line
        elif kind == "bollinger":
            std_mult = float(indicator.get("std_mult") or indicator.get("stdMult") or 2.0)
            mid = series.rolling(period, min_periods=period).mean()
            std = series.rolling(period, min_periods=period).std()
            ctx[output] = mid
            ctx[f"{output}_upper"] = mid + std_mult * std
            ctx[f"{output}_lower"] = mid - std_mult * std
        elif kind == "atr":
            high = _field_series(store, ind_symbol, "high")
            low = _field_series(store, ind_symbol, "low")
            close = _field_series(store, ind_symbol, "close")
            if high is None or low is None or close is None:
                warnings.append(f"indicator atr on {ind_symbol} requires high/low/close")
                continue
            prev_close = close.shift(1)
            tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
            ctx[output] = tr.rolling(period, min_periods=period).mean()
        elif kind == "vwap":
            high = _field_series(store, ind_symbol, "high")
            low = _field_series(store, ind_symbol, "low")
            close = _field_series(store, ind_symbol, "close")
            volume = _field_series(store, ind_symbol, "volume")
            if high is None or low is None or close is None or volume is None:
                warnings.append(f"indicator vwap on {ind_symbol} requires high/low/close/volume")
                continue
            tp = (high + low + close) / 3.0
            ctx[output] = (tp * volume).cumsum() / volume.cumsum().replace(0, np.nan)
        elif kind == "volume_zscore":
            volume = _field_series(store, ind_symbol, "volume")
            if volume is None:
                warnings.append(f"indicator volume_zscore on {ind_symbol} requires volume")
                continue
            rolling_mean = volume.rolling(period, min_periods=period).mean()
            rolling_std = volume.rolling(period, min_periods=period).std().replace(0, np.nan)
            ctx[output] = (volume - rolling_mean) / rolling_std
        else:
            warnings.append(f"unsupported indicator kind ignored at compile time: {kind}")


def _validate_rule_fields(strategy: StrategySpec, contexts: dict[str, dict[str, pd.Series]], warnings: list[str]) -> None:
    for bucket in ("entry", "exit", "trim"):
        for rule in strategy.rules.get(bucket) or []:
            if not isinstance(rule, dict):
                warnings.append(f"rules.{bucket} contains a non-dict rule")
                continue
            _validate_rule_node(rule, contexts, strategy, warnings, bucket)


def _validate_rule_node(node: dict[str, Any], contexts: dict[str, dict[str, pd.Series]], strategy: StrategySpec, warnings: list[str], bucket: str) -> None:
    if "all" in node or "any" in node or "not" in node:
        for child in node.get("all") or []:
            if isinstance(child, dict):
                _validate_rule_node(child, contexts, strategy, warnings, bucket)
        for child in node.get("any") or []:
            if isinstance(child, dict):
                _validate_rule_node(child, contexts, strategy, warnings, bucket)
        child = node.get("not")
        if isinstance(child, dict):
            _validate_rule_node(child, contexts, strategy, warnings, bucket)
        return
    symbol = str(node.get("symbol") or strategy.base_symbol or next(iter(contexts), "")).upper().strip()
    if symbol not in contexts:
        warnings.append(f"rule bucket {bucket} references missing symbol {symbol}")
        return
    field = str(node.get("field") or node.get("left") or "").strip()
    if field and field not in contexts[symbol] and field not in {"close", "price"}:
        if not any(field.endswith(suffix) for suffix in ("_upper", "_lower", "_signal", "_hist")):
            warnings.append(f"rule bucket {bucket} references missing field {field} on {symbol}")
    op = str(node.get("op") or node.get("operator") or "").lower().strip()
    if op not in {"<", "<=", ">", ">=", "==", "!=", "=", "cross_above", "cross_below", "crosses_above", "crosses_below"}:
        warnings.append(f"rule bucket {bucket} uses unsupported operator {op}")


def _first_matching_rule(rules: list[dict[str, Any]], contexts: dict[str, dict[str, pd.Series]], symbol: str, dt: pd.Timestamp) -> dict[str, Any] | None:
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        mask = _evaluate_rule(rule, contexts, symbol)
        if dt in mask.index and bool(mask.loc[dt]):
            rule = dict(rule)
            rule["label"] = _rule_label(rule, rule.get("label") or rule.get("ruleId") or rule.get("action") or "rule")
            return rule
    return None


def _evaluate_rule(node: dict[str, Any], contexts: dict[str, dict[str, pd.Series]], symbol: str) -> pd.Series:
    if "all" in node:
        parts = [_evaluate_rule(child, contexts, symbol) for child in node.get("all") or [] if isinstance(child, dict)]
        return _combine_series(parts, mode="all", fallback=contexts[symbol]["close"])
    if "any" in node:
        parts = [_evaluate_rule(child, contexts, symbol) for child in node.get("any") or [] if isinstance(child, dict)]
        return _combine_series(parts, mode="any", fallback=contexts[symbol]["close"])
    if "not" in node and isinstance(node.get("not"), dict):
        return ~_evaluate_rule(node["not"], contexts, symbol)

    rule_symbol = str(node.get("symbol") or symbol).upper().strip()
    ref_symbol = str(node.get("ref_symbol") or rule_symbol).upper().strip()
    ctx = contexts.get(rule_symbol) or contexts[symbol]
    field_name = str(node.get("field") or node.get("left") or "close").strip()
    left = _series_for_rule(ctx, field_name)
    if left is None:
        left = ctx["close"]

    op = str(node.get("op") or node.get("operator") or "").lower().strip()
    ref_value = node.get("value")
    ref_field = node.get("ref") or node.get("right") or node.get("compare_to")
    if isinstance(ref_field, str) and ref_field in ctx:
        right = ctx[ref_field]
    elif isinstance(ref_field, str) and ref_symbol in contexts and ref_field in contexts[ref_symbol]:
        right = contexts[ref_symbol][ref_field]
    else:
        right = _coerce_scalar_or_series(ref_value, left.index)

    return _compare_series(left, right, op)


def _series_for_rule(ctx: dict[str, pd.Series], field: str) -> pd.Series | None:
    field = str(field or "").strip()
    if field in ctx:
        return pd.to_numeric(ctx[field], errors="coerce")
    if field.lower() in _CLOSE_LIKE_FIELDS:
        return pd.to_numeric(ctx["close"], errors="coerce")
    return None


def _compare_series(left: pd.Series, right: pd.Series | float | int | None, op: str) -> pd.Series:
    left = pd.to_numeric(left, errors="coerce")
    if isinstance(right, pd.Series):
        right_series = pd.to_numeric(right, errors="coerce").reindex(left.index).ffill()
    else:
        scalar = float(right) if right is not None else np.nan
        right_series = pd.Series(scalar, index=left.index)
    if op in {"", "truthy"}:
        return left.notna() & (left > 0)
    if op in {"<", "lt"}:
        return left < right_series
    if op in {"<=", "lte"}:
        return left <= right_series
    if op in {">", "gt"}:
        return left > right_series
    if op in {">=", "gte"}:
        return left >= right_series
    if op in {"=", "==", "eq"}:
        return left == right_series
    if op in {"!=", "ne"}:
        return left != right_series
    if op in {"cross_above", "crosses_above"}:
        prev = left.shift(1)
        prev_right = right_series.shift(1) if isinstance(right, pd.Series) else right_series.shift(1)
        return (left > right_series) & (prev <= prev_right)
    if op in {"cross_below", "crosses_below"}:
        prev = left.shift(1)
        prev_right = right_series.shift(1) if isinstance(right, pd.Series) else right_series.shift(1)
        return (left < right_series) & (prev >= prev_right)
    return left.notna() & (left > 0)


def _combine_series(parts: list[pd.Series], *, mode: str, fallback: pd.Series) -> pd.Series:
    if not parts:
        return pd.Series(False, index=fallback.index)
    base = pd.Series(True if mode == "all" else False, index=fallback.index)
    aligned = [part.reindex(base.index).fillna(False) for part in parts]
    if mode == "all":
        out = base
        for part in aligned:
            out = out & part.astype(bool)
        return out
    out = base
    for part in aligned:
        out = out | part.astype(bool)
    return out


def _trim_factor(rule: dict[str, Any]) -> float:
    factor = rule.get("factor")
    if factor is None and isinstance(rule.get("emit"), dict):
        factor = rule["emit"].get("factor") or rule["emit"].get("value")
    if factor is None:
        action = _rule_action(rule, default="trim_half")
        if action == "trim_half":
            return 0.5
        return 0.5
    value = _safe_float(factor, 0.5)
    return min(1.0, max(0.0, value))


def _rule_action(rule: dict[str, Any], *, default: str) -> str:
    action = str(rule.get("action") or rule.get("emit", {}).get("action") or default).strip().lower()
    if action in {"buy"}:
        return "enter_long"
    if action in {"sell", "close"}:
        return "exit_all"
    if action in {"reduce_half", "halve", "trim"}:
        return "trim_half"
    if action in _SUPPORTED_ACTIONS:
        return action
    return default


def _rule_label(rule: dict[str, Any], fallback: str) -> str:
    return str(rule.get("label") or rule.get("ruleId") or rule.get("name") or fallback).strip() or fallback


def _coerce_scalar_or_series(value: Any, index: pd.Index) -> pd.Series | float | None:
    if isinstance(value, pd.Series):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _strategy_cost_bps(params: dict[str, Any]) -> float:
    """Use the same singular or component cost policy as allocation."""

    if "cost_bps" in params:
        return max(0.0, _safe_float(params.get("cost_bps"), 0.0))
    return sum(max(0.0, _safe_float(params.get(key), 0.0)) for key in ("fees_bps", "slippage_bps", "spread_bps"))


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    series = pd.to_numeric(series, errors="coerce")
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def _rolling(series: pd.Series, period: int, method: str) -> pd.Series:
    rolling = series.rolling(period, min_periods=period)
    if method in {"mean", "avg"}:
        return rolling.mean()
    if method == "min":
        return rolling.min()
    if method == "max":
        return rolling.max()
    if method == "std":
        return rolling.std()
    if method == "sum":
        return rolling.sum()
    if method == "median":
        return rolling.median()
    return rolling.mean()
