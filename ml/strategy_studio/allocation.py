"""Deterministic signal-to-target allocation helpers.

The allocator deliberately stops at target weights.  Order creation and fills
belong to the execution layer, so this module has no broker or simulator
dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from ml.optimization import cost_aware_objective

from .signals import SignalPanel


_COVARIANCE_EPS = 1e-12

try:
    from sklearn.covariance import LedoitWolf
except ImportError:  # pragma: no cover - exercised when sklearn is unavailable
    LedoitWolf = None


@dataclass(slots=True)
class AllocationResult:
    """Target weights and the reasons the raw target was changed."""

    weights: pd.DataFrame
    diagnostics: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "weights": _frame_to_json(self.weights),
            "diagnostics": _json_safe(self.diagnostics),
            "warnings": _json_safe(self.warnings),
        }


def estimate_shrunk_covariance(returns: pd.DataFrame) -> pd.DataFrame:
    """Estimate a symmetric, PSD covariance matrix for a return panel.

    Ledoit-Wolf is preferred when it is installed and enough complete rows are
    available.  The fallback uses a diagonal covariance for short or invalid
    samples, then clips tiny negative eigenvalues caused by floating-point
    noise.  The output always keeps the input column order.
    """

    if returns is None:
        return pd.DataFrame()
    if not isinstance(returns, pd.DataFrame):
        raise TypeError("returns must be a pandas DataFrame")
    if returns.empty:
        columns = list(returns.columns)
        if not columns:
            return pd.DataFrame()
        return pd.DataFrame(
            np.eye(len(columns), dtype="float64") * _COVARIANCE_EPS,
            index=columns,
            columns=columns,
        )

    frame = returns.copy().apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)
    columns = list(frame.columns)
    complete = frame.dropna(how="any")
    matrix: np.ndarray | None = None

    if LedoitWolf is not None and len(complete) >= 2 and columns:
        try:
            matrix = np.asarray(LedoitWolf().fit(complete.to_numpy(dtype="float64")).covariance_, dtype="float64")
        except (TypeError, ValueError, np.linalg.LinAlgError):
            matrix = None

    if matrix is None:
        variances = frame.var(axis=0, ddof=1).fillna(_COVARIANCE_EPS).clip(lower=_COVARIANCE_EPS)
        matrix = np.diag(variances.to_numpy(dtype="float64"))
        if len(complete) >= 2 and len(columns) > 1:
            try:
                sample = complete.cov(ddof=1).to_numpy(dtype="float64")
                if np.isfinite(sample).all():
                    matrix = sample
            except (TypeError, ValueError):
                pass

    matrix = _project_psd(matrix)
    return pd.DataFrame(matrix, index=columns, columns=columns, dtype="float64")


def allocate_targets(
    signal_panel: SignalPanel,
    returns: pd.DataFrame,
    config: dict[str, object],
    costs: dict[str, object],
) -> AllocationResult:
    """Convert a public :class:`SignalPanel` into constrained target weights.

    Constraint order is intentionally explicit: signal validity, position cap,
    gross exposure, target volatility, and finally turnover.  Each change made
    by a constraint receives a structured diagnostic so callers can explain
    why the requested target was not applied.
    """

    if not isinstance(signal_panel, SignalPanel):
        raise TypeError("signal_panel must be a SignalPanel")
    if not isinstance(returns, pd.DataFrame):
        raise TypeError("returns must be a pandas DataFrame")
    config = dict(config or {})
    costs = dict(costs or {})
    score = signal_panel.score.copy().apply(pd.to_numeric, errors="coerce")
    if score.empty:
        return AllocationResult(pd.DataFrame(index=score.index, columns=score.columns, dtype="float64"), warnings=["signal panel is empty"])

    index = score.index
    symbols = list(score.columns)
    confidence = signal_panel.confidence.reindex(index=index, columns=symbols).apply(pd.to_numeric, errors="coerce")
    returns_frame = returns.copy().apply(pd.to_numeric, errors="coerce")
    returns_frame = returns_frame.replace([np.inf, -np.inf], np.nan)
    warnings: list[str] = []
    diagnostics: list[dict[str, object]] = []

    position_default = config.get("position_pct")
    if position_default is None:
        position_default = 1.0
    max_position = _config_float(config, "max_position_pct", position_default, warnings, minimum=0.0)
    max_gross = _config_float(config, "max_gross_exposure", 1.0, warnings, minimum=0.0)
    max_turnover = _optional_config_float(config, "max_turnover", warnings, minimum=0.0)
    target_volatility = _optional_config_float(config, "target_volatility", warnings, minimum=0.0)
    min_confidence = _config_float(config, "min_confidence", 0.0, warnings, minimum=0.0, maximum=1.0)
    risk_aversion = _config_float(config, "risk_aversion", 1.0, warnings, minimum=0.0)
    turnover_penalty = _config_float(config, "turnover_penalty", 0.0, warnings, minimum=0.0)
    cost_bps = _total_cost_bps(costs, warnings)
    allow_short = _config_bool(config, "allow_short", True, warnings)
    optimizer = str(config.get("optimizer") or "cost_aware_risk_budget").strip().lower()

    if optimizer not in {"cost_aware_risk_budget", "equal_weight", "risk_budget"}:
        warnings.append(f"unsupported optimizer {optimizer}; using cost_aware_risk_budget")
        optimizer = "cost_aware_risk_budget"

    previous = _initial_previous_weights(config.get("previous_weights"), symbols)
    weights = pd.DataFrame(0.0, index=index, columns=symbols, dtype="float64")
    valid_score = score.notna() & np.isfinite(score)
    valid_confidence = confidence.notna() & np.isfinite(confidence)
    valid_confidence &= (confidence >= 0.0) & (confidence <= 1.0)
    low_confidence = valid_score & valid_confidence & (confidence < min_confidence)
    usable = valid_score & valid_confidence & ~low_confidence
    covariance_warnings: set[str] = set()
    try:
        covariance_refresh_bars = int(config.get("covariance_refresh_bars", 5))
    except (TypeError, ValueError):
        covariance_refresh_bars = 5
        warnings.append("invalid covariance_refresh_bars; using 5")
    if covariance_refresh_bars < 1:
        warnings.append("covariance_refresh_bars must be >= 1; using 1")
        covariance_refresh_bars = 1
    cached_covariance = None
    cached_covariance_position = -covariance_refresh_bars

    for position_in_index, timestamp in enumerate(index):
        refresh_covariance = (
            cached_covariance is None
            or position_in_index - cached_covariance_position >= covariance_refresh_bars
        )
        if refresh_covariance:
            available_returns = _point_in_time_returns(returns_frame, timestamp)
            relevant_returns = available_returns.reindex(columns=symbols)
            complete_rows = len(relevant_returns.dropna(how="any"))
            fallback_reason: str | None = None
            if LedoitWolf is None:
                fallback_reason = "Ledoit-Wolf unavailable; using sample/diagonal fallback"
            elif complete_rows < 2:
                fallback_reason = "fewer than two complete point-in-time return rows"
            cached_covariance = estimate_shrunk_covariance(available_returns)
            cached_covariance_position = position_in_index
            if fallback_reason is not None:
                warning = f"covariance fallback at {_timestamp_text(timestamp)}: {fallback_reason}"
                if fallback_reason not in covariance_warnings:
                    warnings.append(warning)
                    covariance_warnings.add(fallback_reason)
                _add_diagnostic(
                    diagnostics,
                    timestamp,
                    "__portfolio__",
                    float(complete_rows),
                    _COVARIANCE_EPS,
                    "point_in_time_covariance",
                    "covariance_fallback",
                    reason=fallback_reason,
                )
        row_covariance = _aligned_covariance(cached_covariance, symbols)

        raw_signal = score.loc[timestamp].to_numpy(dtype="float64", na_value=np.nan)
        row_confidence = confidence.loc[timestamp].to_numpy(dtype="float64", na_value=np.nan)
        row_usable = usable.loc[timestamp].to_numpy(dtype=bool)
        row_low_confidence = low_confidence.loc[timestamp].to_numpy(dtype=bool)
        for position, symbol in enumerate(symbols):
            value = raw_signal[position]
            if row_low_confidence[position]:
                _add_diagnostic(
                    diagnostics, timestamp, symbol, value, 0.0,
                    "min_confidence", "low_confidence",
                )
            elif not row_usable[position]:
                _add_diagnostic(
                    diagnostics, timestamp, symbol, value, 0.0,
                    "valid_signal", "invalid_signal",
                )

        signal_values = np.where(row_usable, raw_signal * row_confidence, 0.0)
        signal_values = np.nan_to_num(signal_values, nan=0.0, posinf=0.0, neginf=0.0)
        if not allow_short:
            short_rows = signal_values < 0.0
            for position, is_short in enumerate(short_rows):
                if is_short:
                    _add_diagnostic(
                        diagnostics, timestamp, symbols[position], signal_values[position], 0.0,
                        "long_only", "allow_short",
                    )
            signal_values = np.maximum(signal_values, 0.0)

        prior = _apply_weight_constraints(
            previous,
            row_covariance,
            symbols=symbols,
            max_position=max_position,
            max_gross=max_gross,
            target_volatility=target_volatility,
            allow_short=allow_short,
            timestamp=timestamp,
            diagnostics=diagnostics,
            stage="previous",
        )
        if optimizer == "equal_weight":
            active = np.abs(signal_values) > 0.0
            count = int(active.sum())
            candidate = np.where(active, np.sign(signal_values) / count if count else 0.0, 0.0)
        elif optimizer == "risk_budget":
            candidate = _risk_budget_weights(signal_values, row_covariance)
        else:
            candidate = _objective_weights(
                signal_values,
                row_covariance,
                prior,
                risk_aversion=risk_aversion,
                turnover_penalty=turnover_penalty,
                cost_bps=cost_bps,
            )

        constrained = _apply_weight_constraints(
            candidate,
            row_covariance,
            symbols=symbols,
            max_position=max_position,
            max_gross=max_gross,
            target_volatility=target_volatility,
            allow_short=allow_short,
            timestamp=timestamp,
            diagnostics=diagnostics,
            stage="initial",
        )

        # 4. Turnover is measured as the full L1 target change.
        turnover = float(np.abs(constrained - prior).sum())
        if max_turnover is not None and turnover > max_turnover and turnover > 0.0:
            factor = max_turnover / turnover
            before_row = constrained.copy()
            constrained = prior + (constrained - prior) * factor
            for position, symbol in enumerate(symbols):
                if not np.isclose(before_row[position], constrained[position]):
                    _add_diagnostic(
                        diagnostics, timestamp, symbol, before_row[position], constrained[position],
                        "max_turnover", "turnover_limit",
                    )

        # Projection after turnover is deliberate: an invalid or stale prior
        # weight must never make the final target violate portfolio bounds.
        constrained = _apply_weight_constraints(
            constrained,
            row_covariance,
            symbols=symbols,
            max_position=max_position,
            max_gross=max_gross,
            target_volatility=target_volatility,
            allow_short=allow_short,
            timestamp=timestamp,
            diagnostics=diagnostics,
            stage="post_turnover",
        )
        final_turnover = float(np.abs(constrained - prior).sum())
        if max_turnover is not None and final_turnover > max_turnover + 1e-12:
            warning = "turnover limit could not be met after projecting prior weights"
            if warning not in warnings:
                warnings.append(warning)
            _add_diagnostic(
                diagnostics,
                timestamp,
                "__portfolio__",
                final_turnover,
                max_turnover,
                "max_turnover",
                "turnover_conflict",
                reason="final bounds take priority over an invalid prior weight",
            )

        weights.loc[timestamp, symbols] = constrained
        objective_before = cost_aware_objective(
            signal_values, prior, row_covariance, prior,
            risk_aversion=risk_aversion, turnover_penalty=turnover_penalty, cost_bps=cost_bps,
        )
        objective_after = cost_aware_objective(
            signal_values, constrained, row_covariance, prior,
            risk_aversion=risk_aversion, turnover_penalty=turnover_penalty, cost_bps=cost_bps,
        )
        diagnostics.append({
            "type": "objective",
            "symbol": "__portfolio__",
            "before": float(objective_before),
            "after": float(objective_after),
            "constraint": "cost_aware_objective",
            "timestamp": _timestamp_text(timestamp),
            "risk_aversion": float(risk_aversion),
            "turnover_penalty": float(turnover_penalty),
            "cost_bps": float(cost_bps),
        })
        estimated_cost = float(np.abs(constrained - prior).sum() * cost_bps / 10000.0)
        diagnostics.append({
            "type": "transaction_cost",
            "symbol": "__portfolio__",
            "before": float(np.abs(constrained - prior).sum()),
            "after": estimated_cost,
            "constraint": "transaction_cost",
            "timestamp": _timestamp_text(timestamp),
            "cost_bps": float(cost_bps),
            "estimated_cost": estimated_cost,
        })
        previous = constrained

    return AllocationResult(weights=weights, diagnostics=diagnostics, warnings=warnings)


def _objective_weights(
    scores: np.ndarray,
    covariance: np.ndarray,
    previous: np.ndarray,
    *,
    risk_aversion: float,
    turnover_penalty: float,
    cost_bps: float,
) -> np.ndarray:
    """Choose deterministic non-zero coordinates using the shared objective."""

    current = np.zeros_like(scores, dtype="float64")
    current_score = cost_aware_objective(
        scores, current, covariance, previous,
        risk_aversion=risk_aversion, turnover_penalty=turnover_penalty, cost_bps=cost_bps,
    )
    for position, value in enumerate(scores):
        if not np.isfinite(value) or np.isclose(value, 0.0):
            continue
        candidate = current.copy()
        candidate[position] = value
        candidate_score = cost_aware_objective(
            scores, candidate, covariance, previous,
            risk_aversion=risk_aversion, turnover_penalty=turnover_penalty, cost_bps=cost_bps,
        )
        if candidate_score >= current_score:
            current = candidate
            current_score = candidate_score
    return current


def _risk_budget_weights(scores: np.ndarray, covariance: np.ndarray) -> np.ndarray:
    active = np.abs(scores) > 0.0
    if not active.any():
        return np.zeros_like(scores, dtype="float64")
    volatility = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    inverse = np.zeros_like(volatility, dtype="float64")
    positive_volatility = active & (volatility > 0.0)
    inverse[positive_volatility] = 1.0 / volatility[positive_volatility]
    inverse[active & ~positive_volatility] = 1.0
    total = float(inverse.sum())
    if total <= 0.0:
        return np.sign(scores) * active / max(1, int(active.sum()))
    return np.sign(scores) * inverse / total


def _portfolio_volatility(weights: np.ndarray, covariance: np.ndarray) -> float:
    variance = float(weights @ covariance @ weights)
    return float(np.sqrt(max(0.0, variance)))


def _apply_weight_constraints(
    values: np.ndarray,
    covariance: np.ndarray,
    *,
    symbols: list[str],
    max_position: float,
    max_gross: float,
    target_volatility: float | None,
    allow_short: bool,
    timestamp: Any,
    diagnostics: list[dict[str, object]],
    stage: str,
) -> np.ndarray:
    """Apply the non-turnover constraints and record every projection."""

    constrained = np.asarray(values, dtype="float64").copy()
    for position, symbol in enumerate(symbols):
        before = float(constrained[position])
        capped = float(np.clip(before, -max_position, max_position))
        if not np.isclose(before, capped):
            _add_diagnostic(
                diagnostics, timestamp, symbol, before, capped,
                "max_position_pct", "position_cap",
                stage=stage,
            )
        after = capped
        if not allow_short and after < 0.0:
            _add_diagnostic(
                diagnostics, timestamp, symbol, after, 0.0,
                "allow_short", "long_only", stage=stage,
            )
            after = 0.0
        constrained[position] = after

    gross = float(np.abs(constrained).sum())
    if gross > max_gross and gross > 0.0:
        factor = max_gross / gross
        before_row = constrained.copy()
        constrained *= factor
        for position, symbol in enumerate(symbols):
            if not np.isclose(before_row[position], constrained[position]):
                _add_diagnostic(
                    diagnostics, timestamp, symbol, before_row[position], constrained[position],
                    "max_gross_exposure", "gross_exposure", stage=stage,
                )

    if target_volatility is not None:
        portfolio_vol = _portfolio_volatility(constrained, covariance)
        if target_volatility == 0.0 and portfolio_vol > 0.0:
            factor = 0.0
        elif target_volatility > 0.0 and portfolio_vol > target_volatility:
            factor = target_volatility / portfolio_vol
        else:
            factor = 1.0
        if factor < 1.0:
            before_row = constrained.copy()
            constrained *= factor
            for position, symbol in enumerate(symbols):
                if not np.isclose(before_row[position], constrained[position]):
                    _add_diagnostic(
                        diagnostics, timestamp, symbol, before_row[position], constrained[position],
                        "target_volatility", "target_volatility", stage=stage,
                    )
    return constrained


def _aligned_covariance(covariance: pd.DataFrame, symbols: list[str]) -> np.ndarray:
    if covariance.empty:
        return np.zeros((len(symbols), len(symbols)), dtype="float64")
    aligned = covariance.reindex(index=symbols, columns=symbols).fillna(0.0)
    return _project_psd(aligned.to_numpy(dtype="float64"))


def _initial_previous_weights(value: Any, symbols: list[str]) -> np.ndarray:
    if isinstance(value, pd.DataFrame):
        value = value.iloc[-1] if not value.empty else None
    if isinstance(value, pd.Series):
        mapping = value.to_dict()
    elif isinstance(value, dict):
        mapping = value
    else:
        mapping = {}
    result = np.zeros(len(symbols), dtype="float64")
    for position, symbol in enumerate(symbols):
        try:
            parsed = float(mapping.get(symbol, 0.0))
        except (TypeError, ValueError):
            parsed = 0.0
        result[position] = parsed if np.isfinite(parsed) else 0.0
    return result


def _total_cost_bps(costs: dict[str, object], warnings: list[str]) -> float:
    if "cost_bps" in costs:
        return _config_float(costs, "cost_bps", 0.0, warnings, minimum=0.0)
    total = 0.0
    for key in ("fees_bps", "slippage_bps", "spread_bps"):
        total += _config_float(costs, key, 0.0, warnings, minimum=0.0)
    return total


def _config_float(
    config: dict[str, object],
    key: str,
    default: object,
    warnings: list[str],
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = config.get(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        warnings.append(f"{key} is not numeric; using {default}")
        try:
            parsed = float(default)
        except (TypeError, ValueError):
            parsed = 0.0
    if not np.isfinite(parsed):
        warnings.append(f"{key} is not finite; using {default}")
        try:
            parsed = float(default)
        except (TypeError, ValueError):
            parsed = 0.0
    if minimum is not None and parsed < minimum:
        warnings.append(f"{key} below minimum; using {minimum}")
        parsed = minimum
    if maximum is not None and parsed > maximum:
        warnings.append(f"{key} above maximum; using {maximum}")
        parsed = maximum
    return parsed


def _optional_config_float(
    config: dict[str, object],
    key: str,
    warnings: list[str],
    *,
    minimum: float | None = None,
) -> float | None:
    if key not in config or config.get(key) is None:
        return None
    return _config_float(config, key, 0.0, warnings, minimum=minimum)


def _add_diagnostic(
    diagnostics: list[dict[str, object]],
    timestamp: Any,
    symbol: str,
    before: Any,
    after: Any,
    constraint: str,
    diagnostic_type: str,
    *,
    stage: str | None = None,
    reason: str | None = None,
) -> None:
    diagnostic: dict[str, object] = {
        "type": diagnostic_type,
        "symbol": str(symbol),
        "before": _finite_or_none(before),
        "after": _finite_or_none(after),
        "constraint": constraint,
        "timestamp": _timestamp_text(timestamp),
    }
    if stage is not None:
        diagnostic["stage"] = stage
    if reason is not None:
        diagnostic["reason"] = reason
    diagnostics.append(diagnostic)


def _config_bool(config: dict[str, object], key: str, default: bool, warnings: list[str]) -> bool:
    value = config.get(key, default)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on", "enabled"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", "disabled"}:
            return False
    if isinstance(value, (int, float, np.integer, np.floating)) and np.isfinite(value):
        if float(value) == 1.0:
            return True
        if float(value) == 0.0:
            return False
    warnings.append(f"{key} is not a recognized boolean; using {default}")
    return bool(default)


def _point_in_time_returns(returns: pd.DataFrame, timestamp: Any) -> pd.DataFrame:
    """Keep only return observations available at or before ``timestamp``."""

    if returns.empty:
        return returns.iloc[:0]
    target = _timestamp_key(timestamp)
    if pd.isna(target):
        return returns.iloc[:0]
    mask = []
    for candidate in returns.index:
        candidate_timestamp = _timestamp_key(candidate)
        mask.append(not pd.isna(candidate_timestamp) and candidate_timestamp <= target)
    return returns.loc[mask]


def _timestamp_key(value: Any) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT
    if pd.isna(timestamp):
        return pd.NaT
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _finite_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _timestamp_text(value: Any) -> str:
    try:
        return pd.Timestamp(value).isoformat()
    except (TypeError, ValueError):
        return str(value)


def _frame_to_json(frame: pd.DataFrame) -> dict[str, object]:
    return {
        "orient": "split",
        "index": [_json_safe(value) for value in frame.index],
        "columns": [_json_safe(value) for value in frame.columns],
        "data": [[_json_safe(value) for value in row] for row in frame.to_numpy(dtype=object)],
    }


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return None if pd.isna(value) else pd.Timestamp(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        missing = False
    if isinstance(missing, (bool, np.bool_)) and bool(missing):
        return None
    return value


def _project_psd(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype="float64")
    if values.size == 0:
        return values.reshape((0, 0))
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    values = (values + values.T) / 2.0
    try:
        eigenvalues, eigenvectors = np.linalg.eigh(values)
        eigenvalues = np.maximum(eigenvalues, 0.0)
        values = (eigenvectors * eigenvalues) @ eigenvectors.T
    except np.linalg.LinAlgError:
        values = np.diag(np.maximum(np.diag(values), 0.0))
    return (values + values.T) / 2.0


__all__ = ["AllocationResult", "allocate_targets", "estimate_shrunk_covariance"]
