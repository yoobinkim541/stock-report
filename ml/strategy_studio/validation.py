"""Time-series validation, performance metrics, provenance checks, and gates.

This module is deliberately independent from strategy construction.  It consumes
the in-memory ``StrategyRun`` contract and emits plain JSON-safe payloads at its
boundary.  A validation result is evidence for promotion, never an order or an
execution decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import combinations
from math import isfinite, sqrt
from typing import Any
import warnings as _warnings

import numpy as np
import pandas as pd

from .execution import to_jsonable


TRADING_DAYS = 252
MIN_STATISTICAL_OBSERVATIONS = 30
_VALID_ENVIRONMENTS = {"shadow", "pilot", "live", "sandbox", "paper"}
_ENVIRONMENT_ALIASES = {"sandbox": "shadow", "paper": "pilot"}


def _warn(message: str) -> None:
    _warnings.warn(message, UserWarning, stacklevel=3)


def _canonical_timestamp(value: object) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _timestamp_text(value: object) -> str:
    return _canonical_timestamp(value).isoformat()


def _is_timestamp_like(value: object) -> bool:
    return isinstance(value, (pd.Timestamp, datetime, date, np.datetime64)) or isinstance(value, str)


def _normalise_index(index: pd.Index | Sequence[object], name: str = "index") -> pd.Index:
    values = index if isinstance(index, pd.Index) else pd.Index(index)
    if values.has_duplicates:
        raise ValueError(f"{name} contains duplicate timestamps")

    should_parse = isinstance(values, pd.DatetimeIndex)
    if not should_parse and len(values):
        should_parse = all(_is_timestamp_like(value) for value in values)
    if should_parse:
        try:
            parsed = pd.to_datetime(values, errors="raise")
            values = pd.DatetimeIndex(parsed)
            if values.tz is not None:
                values = values.tz_convert("UTC")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contains invalid timestamps") from exc
    if not values.is_monotonic_increasing:
        raise ValueError(f"{name} must be monotonic increasing")
    if values.has_duplicates:
        raise ValueError(f"{name} contains duplicate timestamps")
    return values


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if number < 0 or float(value) != number:
        raise ValueError(f"{name} must be a non-negative integer")
    return number


@dataclass(frozen=True, slots=True)
class ValidationSplit:
    """One chronological train/test split and its excluded observations."""

    train: pd.Index
    test: pd.Index
    path_id: str = "path-0"
    embargo_bars: int = 0
    blocked: pd.Index = field(default_factory=pd.Index, repr=False, compare=False)

    def __post_init__(self) -> None:
        train = _normalise_index(self.train, "train")
        test = _normalise_index(self.test, "test")
        blocked = _normalise_index(self.blocked, "blocked") if len(self.blocked) else pd.Index([])
        embargo = _non_negative_int(self.embargo_bars, "embargo_bars")
        if len(set(train).intersection(set(test))):
            raise ValueError("train and test contain overlapping observations")
        if len(set(train).intersection(set(blocked))):
            raise ValueError("train contains embargoed or purged observations")
        object.__setattr__(self, "train", train)
        object.__setattr__(self, "test", test)
        object.__setattr__(self, "blocked", blocked)
        object.__setattr__(self, "embargo_bars", embargo)
        object.__setattr__(self, "path_id", str(self.path_id))

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable({
            "train": self.train,
            "test": self.test,
            "path_id": self.path_id,
            "embargo_bars": self.embargo_bars,
            "blocked": self.blocked,
        })


def check_split_leakage(
    split: ValidationSplit,
    *,
    label_horizon: int = 0,
    label_end: pd.Series | Sequence[object] | None = None,
) -> list[str]:
    """Return auditable leakage findings for a split.

    ``label_end`` is positional and aligned to ``split.train`` when supplied.
    Generators retain their purge/embargo rows in ``blocked`` so this check does
    not infer omitted timestamps from wall-clock gaps.
    """

    issues: list[str] = []
    train_set = set(split.train)
    test_set = set(split.test)
    blocked_set = set(split.blocked)
    if train_set.intersection(test_set):
        issues.append("train_test_overlap")
    if train_set.intersection(blocked_set):
        issues.append("train_contains_blocked_rows")
    if blocked_set.intersection(test_set):
        issues.append("blocked_contains_test_rows")

    horizon = _non_negative_int(label_horizon, "label_horizon")
    if horizon and len(split.train) and len(split.test) and not isinstance(split.train, pd.DatetimeIndex):
        test_start = min(split.test)
        prior_train = [value for value in split.train if value < test_start]
        if prior_train and max(prior_train) + horizon >= test_start:
            issues.append("label_horizon_overlaps_test")

    if label_end is not None and len(split.train) and len(split.test):
        if isinstance(label_end, pd.Series) and len(label_end) != len(split.train):
            ends = [label_end.get(start) for start in split.train]
        else:
            ends = list(label_end) if not isinstance(label_end, pd.Series) else label_end.tolist()
        if len(ends) == len(split.train):
            test_start = split.test[0]
            test_end = split.test[-1]
            for start, end in zip(split.train, ends):
                if end >= test_start and start <= test_end:
                    issues.append("label_end_overlaps_test")
                    break
    return list(dict.fromkeys(issues))


def _label_end_values(
    index: pd.Index,
    label_end: pd.Series | Sequence[object] | None,
    label_horizon: int,
) -> list[object] | None:
    if label_end is None:
        if label_horizon <= 0:
            return None
        return [index[min(position + label_horizon, len(index) - 1)] for position in range(len(index))]
    values = label_end.tolist() if isinstance(label_end, pd.Series) else list(label_end)
    if len(values) != len(index):
        raise ValueError("label_end must have one value per index row")
    if isinstance(index, pd.DatetimeIndex):
        try:
            parsed = pd.to_datetime(values, errors="raise")
            if getattr(index, "tz", None) is not None:
                parsed = pd.to_datetime(parsed, utc=True)
            return list(parsed)
        except (TypeError, ValueError) as exc:
            raise ValueError("label_end contains invalid timestamps") from exc
    return values


def _overlapping_label_positions(
    index: pd.Index,
    candidates: Sequence[int],
    test_positions: Sequence[int],
    label_ends: Sequence[object] | None,
) -> set[int]:
    if not candidates or not test_positions:
        return set()
    test_values = [index[position] for position in test_positions]
    test_min = min(test_values)
    test_max = max(test_values)
    removed: set[int] = set()
    for position in candidates:
        end = label_ends[position] if label_ends is not None else index[position]
        if end >= test_min and index[position] <= test_max:
            removed.add(position)
    return removed


def make_purged_walk_forward_splits(
    index: pd.Index,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    embargo_bars: int,
    *,
    label_horizon: int = 0,
    label_end: pd.Series | Sequence[object] | None = None,
) -> list[ValidationSplit]:
    """Create deterministic fixed-window purged walk-forward splits.

    ``embargo_bars`` is placed between train and test.  Label endpoints can be
    supplied for exact purging; otherwise ``label_horizon`` purges rows whose
    forward label would reach the test window.  Rows removed by either rule are
    retained in ``ValidationSplit.blocked`` for auditability.
    """

    values = _normalise_index(index)
    train_size = _non_negative_int(train_bars, "train_bars")
    test_size = _non_negative_int(test_bars, "test_bars")
    step = _non_negative_int(step_bars, "step_bars")
    embargo = _non_negative_int(embargo_bars, "embargo_bars")
    horizon = _non_negative_int(label_horizon, "label_horizon")
    if train_size == 0 or test_size == 0 or step == 0:
        _warn("purged walk-forward windows require positive train, test, and step sizes")
        return []
    if not len(values) or train_size + embargo + test_size > len(values):
        _warn("purged walk-forward windows do not fit the supplied index")
        return []

    ends = _label_end_values(values, label_end, horizon)
    splits: list[ValidationSplit] = []
    start = 0
    path = 0
    while start + train_size + embargo + test_size <= len(values):
        train_positions = list(range(start, start + train_size))
        test_start = start + train_size + embargo
        test_positions = list(range(test_start, test_start + test_size))
        blocked_positions = set(range(start + train_size, test_start))
        purged = _overlapping_label_positions(values, train_positions, test_positions, ends)
        train_positions = [position for position in train_positions if position not in purged]
        blocked_positions.update(purged)
        split = ValidationSplit(
            train=values.take(train_positions),
            test=values.take(test_positions),
            path_id=f"wf-{path}",
            embargo_bars=embargo,
            blocked=values.take(sorted(blocked_positions)),
        )
        if not train_positions:
            _warn(f"discarded empty validation split after purge: {split.path_id}")
        elif not check_split_leakage(split):
            splits.append(split)
        else:
            _warn(f"discarded leaked validation split: {split.path_id}")
        start += step
        path += 1
    if not splits:
        _warn("no valid purged walk-forward splits were produced")
    return splits


def make_cpcv_splits(
    index: pd.Index,
    groups: int,
    test_groups: int,
    embargo_bars: int,
    *,
    label_horizon: int = 0,
    label_end: pd.Series | Sequence[object] | None = None,
) -> list[ValidationSplit]:
    """Enumerate chronological CPCV paths with purge and embargo blackout rows."""

    values = _normalise_index(index)
    group_count = _non_negative_int(groups, "groups")
    selected_count = _non_negative_int(test_groups, "test_groups")
    embargo = _non_negative_int(embargo_bars, "embargo_bars")
    horizon = _non_negative_int(label_horizon, "label_horizon")
    if group_count < 2 or selected_count == 0 or selected_count >= group_count:
        _warn("CPCV requires at least two groups and fewer test groups than total groups")
        return []
    if group_count > len(values) or not len(values):
        _warn("CPCV groups do not fit the supplied index")
        return []

    group_positions = [list(part) for part in np.array_split(np.arange(len(values)), group_count)]
    ends = _label_end_values(values, label_end, horizon)
    splits: list[ValidationSplit] = []
    for combo in combinations(range(group_count), selected_count):
        test_positions = sorted(position for group in combo for position in group_positions[group])
        test_set = set(test_positions)
        blocked: set[int] = set()
        for position in test_positions:
            left = max(0, position - embargo)
            right = min(len(values), position + embargo + 1)
            blocked.update(range(left, right))
        blocked.difference_update(test_set)
        candidates = [position for position in range(len(values)) if position not in test_set and position not in blocked]
        purged = _overlapping_label_positions(values, candidates, test_positions, ends)
        blocked.update(purged)
        train_positions = [position for position in candidates if position not in purged]
        path_id = "cpcv-" + "-".join(str(group) for group in combo)
        split = ValidationSplit(
            train=values.take(train_positions),
            test=values.take(test_positions),
            path_id=path_id,
            embargo_bars=embargo,
            blocked=values.take(sorted(blocked)),
        )
        if not train_positions:
            _warn(f"discarded empty validation split after purge: {path_id}")
        elif not check_split_leakage(split):
            splits.append(split)
        else:
            _warn(f"discarded leaked validation split: {path_id}")
    return splits


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _run_value(run: object, key: str, default: Any = None) -> Any:
    if isinstance(run, Mapping):
        return run.get(key, default)
    return getattr(run, key, default)


def _run_metrics(run: object) -> Mapping[str, Any]:
    metrics = _run_value(run, "metrics", {})
    return metrics if isinstance(metrics, Mapping) else {}


def _run_spec(run: object) -> Mapping[str, Any]:
    spec = _run_value(run, "spec", {})
    return spec if isinstance(spec, Mapping) else {}


def _normalise_series(series: pd.Series) -> pd.Series:
    output = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if isinstance(output.index, pd.DatetimeIndex):
        output = output.copy()
        output.index = pd.DatetimeIndex(pd.to_datetime(output.index, utc=True))
    if output.index.has_duplicates:
        output = output[~output.index.duplicated(keep="first")]
    return output.sort_index()


def _run_equity(run: object) -> pd.DataFrame:
    equity = _run_value(run, "equity", None)
    return equity.copy() if isinstance(equity, pd.DataFrame) else pd.DataFrame()


def _run_returns(run: object, kind: str) -> pd.Series:
    equity = _run_equity(run)
    if equity.empty:
        return pd.Series(dtype="float64")
    column = "net_return" if kind == "net" else "gross_return"
    if column in equity:
        return _normalise_series(equity[column])
    nav_column = "nav" if kind == "net" else "gross_nav"
    if nav_column in equity:
        return _normalise_series(equity[nav_column].pct_change().fillna(0.0))
    return pd.Series(dtype="float64")


def _run_cost_drag(run: object) -> pd.Series:
    equity = _run_equity(run)
    if "cost_drag" not in equity:
        return pd.Series(dtype="float64")
    return _normalise_series(equity["cost_drag"])


def _returns_nav(returns: pd.Series) -> pd.Series:
    if returns.empty:
        return pd.Series(dtype="float64")
    return (1.0 + returns).cumprod()


def _periods_per_year(index: pd.Index) -> float:
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return float(TRADING_DAYS)
    deltas = np.diff(index.asi8) / 1_000_000_000.0
    deltas = deltas[deltas > 0]
    if not len(deltas):
        return float(TRADING_DAYS)
    median_seconds = float(np.median(deltas))
    if median_seconds >= 20 * 60 * 60:
        return float(TRADING_DAYS)
    return max(1.0, 365.25 * 24 * 60 * 60 / median_seconds)


def _cagr_from_nav(nav: pd.Series) -> float | None:
    if len(nav) < 2:
        return None
    start = _finite_float(nav.iloc[0])
    end = _finite_float(nav.iloc[-1])
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    if isinstance(nav.index, pd.DatetimeIndex):
        days = (nav.index[-1] - nav.index[0]).total_seconds() / 86400.0
        if days <= 0:
            return None
        years = days / 365.25
    else:
        years = max((len(nav) - 1) / TRADING_DAYS, 1.0 / TRADING_DAYS)
    return float((end / start) ** (1.0 / years) - 1.0)


def _cagr_from_returns(returns: pd.Series) -> float | None:
    """Annualise the full return stream, including its first observation."""

    if len(returns) < 2:
        return None
    total = float((1.0 + returns).prod())
    if not isfinite(total) or total <= 0:
        return None
    if isinstance(returns.index, pd.DatetimeIndex):
        days = (returns.index[-1] - returns.index[0]).total_seconds() / 86400.0
        years = days / 365.25 if days > 0 else None
    else:
        years = (len(returns) - 1) / TRADING_DAYS
    if years is None or years <= 0:
        return None
    return float(total ** (1.0 / years) - 1.0)


def _max_drawdown(nav: pd.Series) -> float | None:
    if nav.empty:
        return None
    running_max = nav.cummax()
    return _finite_float((nav / running_max - 1.0).min())


def _volatility(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    deviation = float(returns.std(ddof=1))
    return deviation * sqrt(_periods_per_year(returns.index)) if isfinite(deviation) else None


def _sharpe(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    deviation = float(returns.std(ddof=1))
    if not isfinite(deviation) or deviation <= 0:
        return None
    return float(returns.mean() / deviation * sqrt(_periods_per_year(returns.index)))


def _sortino(returns: pd.Series) -> float | None:
    if len(returns) < 2:
        return None
    downside = np.minimum(returns.to_numpy(dtype="float64"), 0.0)
    deviation = float(np.sqrt(np.mean(np.square(downside))))
    if not isfinite(deviation) or deviation <= 0:
        return None
    return float(returns.mean() / deviation * sqrt(_periods_per_year(returns.index)))


def _calmar(cagr: float | None, drawdown: float | None) -> float | None:
    if cagr is None or drawdown is None or drawdown >= 0:
        return None
    return _finite_float(cagr / abs(drawdown))


def _concat_series(series_list: Sequence[pd.Series]) -> pd.Series:
    valid = [series for series in series_list if not series.empty]
    if not valid:
        return pd.Series(dtype="float64")
    combined = pd.concat(valid)
    if combined.index.has_duplicates:
        combined = combined[~combined.index.duplicated(keep="first")]
    return _normalise_series(combined)


def _trade_outcomes(run: object) -> list[float]:
    trades = _run_value(run, "trades", [])
    if not isinstance(trades, Sequence) or isinstance(trades, (str, bytes)):
        return []
    outcomes: list[float] = []
    for trade in trades:
        if not isinstance(trade, Mapping):
            continue
        value = next((trade.get(key) for key in ("net_pnl", "pnl", "realized_pnl", "profit_loss", "return") if trade.get(key) is not None), None)
        number = _finite_float(value)
        if number is not None and number != 0.0:
            outcomes.append(number)
    return outcomes


def _hit_rate(outcomes: Sequence[float]) -> float | None:
    return float(sum(value > 0 for value in outcomes) / len(outcomes)) if outcomes else None


def _profit_factor(outcomes: Sequence[float]) -> float | None:
    if not outcomes:
        return None
    gains = sum(value for value in outcomes if value > 0)
    losses = -sum(value for value in outcomes if value < 0)
    if losses <= 0:
        return None
    return _finite_float(gains / losses)


def _metric_or_series_sum(run: object, key: str, series: pd.Series) -> float:
    value = _finite_float(_run_metrics(run).get(key))
    if value is not None:
        return value
    return float(series.sum()) if not series.empty else 0.0


def _metric_or_series_mean(run: object, key: str, series: pd.Series) -> float:
    value = _finite_float(_run_metrics(run).get(key))
    if value is not None:
        return value
    return float(series.mean()) if not series.empty else 0.0


def _fold_metrics(run: object, path_id: str) -> tuple[dict[str, Any], pd.Series, pd.Series, list[float]]:
    net = _run_returns(run, "net")
    gross = _run_returns(run, "gross")
    cost = _run_cost_drag(run)
    if gross.empty and not net.empty:
        gross = net.add(cost.reindex(net.index).fillna(0.0), fill_value=0.0)
    net_nav = _returns_nav(net)
    gross_nav = _returns_nav(gross)
    metrics = _run_metrics(run)
    outcomes = _trade_outcomes(run)
    if not outcomes:
        outcomes = [float(value) for value in net if value != 0.0]
    trade_count = _finite_float(metrics.get("trade_count"))
    if trade_count is None:
        trades = _run_value(run, "trades", [])
        trade_count = float(len(trades)) if isinstance(trades, Sequence) and not isinstance(trades, (str, bytes)) else 0.0
    net_cagr = _cagr_from_returns(net)
    gross_cagr = _cagr_from_returns(gross)
    drawdown = _max_drawdown(net_nav)
    turnover_series = _run_equity(run).get("turnover", pd.Series(dtype="float64"))
    if not isinstance(turnover_series, pd.Series):
        turnover_series = pd.Series(dtype="float64")
    turnover_series = _normalise_series(turnover_series)
    output = {
        "path_id": path_id,
        "gross_cagr": gross_cagr,
        "net_cagr": net_cagr,
        "cagr": net_cagr,
        "volatility": _volatility(net),
        "gross_volatility": _volatility(gross),
        "sharpe": _sharpe(net),
        "gross_sharpe": _sharpe(gross),
        "sortino": _sortino(net),
        "max_drawdown": drawdown,
        "calmar": _calmar(net_cagr, drawdown),
        "turnover": _metric_or_series_sum(run, "turnover", turnover_series),
        "turnover_mean": _metric_or_series_mean(run, "turnover", turnover_series),
        "cost_drag": _metric_or_series_sum(run, "cost_drag", cost),
        "hit_rate": _hit_rate(outcomes),
        "profit_factor": _profit_factor(outcomes),
        "trade_count": int(trade_count),
        "test_periods": len(net),
        "n_observations": len(net),
    }
    for key in ("regime_concentration", "regime", "tested_configurations", "n_trials"):
        if key in metrics:
            output[key] = to_jsonable(metrics[key])
    return output, net, gross, outcomes


def _benchmark_run(benchmarks: Mapping[str, object]) -> object | None:
    if not benchmarks:
        return None
    key = sorted(benchmarks, key=str)[0]
    return benchmarks[key]


def _configuration_matrix(folds: Sequence[object]) -> np.ndarray | None:
    matrices: list[np.ndarray] = []
    for run in folds:
        candidate = _run_metrics(run).get("configuration_returns")
        if candidate is None:
            candidate = _run_value(run, "configuration_returns", None)
        if isinstance(candidate, pd.DataFrame):
            array = candidate.to_numpy(dtype="float64")
        elif candidate is not None:
            try:
                array = np.asarray(candidate, dtype="float64")
            except (TypeError, ValueError):
                continue
        else:
            continue
        if array.ndim == 1:
            array = array[:, None]
        if array.ndim == 2 and array.shape[1] >= 2:
            matrices.append(array)
    if not matrices:
        return None
    width = min(matrix.shape[1] for matrix in matrices)
    return np.concatenate([matrix[:, :width] for matrix in matrices], axis=0)


def _tested_configurations(folds: Sequence[object]) -> int:
    values: list[int] = []
    for run in folds:
        metrics = _run_metrics(run)
        spec = _run_spec(run)
        candidate = metrics.get("tested_configurations", metrics.get("n_trials"))
        if candidate is None and isinstance(spec.get("validation"), Mapping):
            candidate = spec["validation"].get("tested_configurations", spec["validation"].get("n_trials"))
        try:
            if candidate is not None:
                values.append(max(1, int(candidate)))
        except (TypeError, ValueError):
            continue
    return max(values) if values else 1


def _provenance_for_run(run: object) -> Mapping[str, Any] | None:
    provenance = _run_value(run, "provenance", None)
    if isinstance(provenance, Mapping):
        return provenance
    signals = _run_value(run, "signals", {})
    if isinstance(signals, Mapping) and isinstance(signals.get("provenance"), Mapping):
        return signals["provenance"]
    return None


def _age_limit_seconds(value: object) -> float | None:
    number = _finite_float(value)
    if number is not None:
        return max(0.0, number)
    if isinstance(value, timedelta):
        return max(0.0, value.total_seconds())
    return None


def _provenance_section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = payload.get(name)
    if isinstance(section, Mapping):
        return section
    return {}


def check_data_model_provenance(
    provenance: Mapping[str, Any] | None,
    *,
    evaluation_at: object | None = None,
    max_data_age_seconds: float | timedelta | None = None,
    max_model_age_seconds: float | timedelta | None = None,
) -> dict[str, Any]:
    """Check source/model identity, freshness, and point-in-time ordering.

    Missing metadata is a warning and becomes a promotion failure only when a
    caller explicitly uses the returned ``ok``/``errors`` state in its gate.
    The function never uses wall-clock time implicitly, keeping results replayable.
    """

    payload = provenance if isinstance(provenance, Mapping) else {}
    errors: list[str] = []
    warnings: list[str] = []
    evaluation = _canonical_timestamp(evaluation_at) if evaluation_at is not None else None
    data = _provenance_section(payload, "data")
    model = _provenance_section(payload, "model")
    if not data and any(key in payload for key in ("source", "data_source", "data_version", "data_as_of")):
        data = payload
    if not model and any(key in payload for key in ("model_id", "model_version", "trained_until", "model_as_of")):
        model = payload

    data_source = data.get("source", data.get("data_source"))
    data_version = data.get("version", data.get("data_version"))
    data_as_of = data.get("as_of", data.get("timestamp", data.get("data_as_of")))
    data_status = str(data.get("freshness", data.get("status", ""))).strip().lower()
    if not data_source or not data_version:
        warnings.append("data provenance is incomplete")
    if data_as_of is None:
        warnings.append("data provenance timestamp is missing")
    else:
        try:
            data_timestamp = _canonical_timestamp(data_as_of)
            if evaluation is not None and data_timestamp > evaluation:
                errors.append("data timestamp is later than evaluation timestamp")
            if evaluation is not None and _age_limit_seconds(max_data_age_seconds) is not None:
                age = (evaluation - data_timestamp).total_seconds()
                if age < 0 or age > float(_age_limit_seconds(max_data_age_seconds)):
                    warnings.append("data provenance is stale")
        except (TypeError, ValueError):
            errors.append("data provenance timestamp is invalid")
    if data_status in {"stale", "expired", "missing", "unknown"}:
        warnings.append(f"data provenance is stale: {data_status}")

    model_id = model.get("model_id")
    model_version = model.get("model_version", model.get("version"))
    model_as_of = model.get("as_of", model.get("trained_until", model.get("model_as_of")))
    model_status = str(model.get("freshness", model.get("status", ""))).strip().lower()
    if not model_id or not model_version:
        warnings.append("model provenance is incomplete")
    if model_as_of is None:
        warnings.append("model provenance timestamp is missing")
    else:
        try:
            model_timestamp = _canonical_timestamp(model_as_of)
            if evaluation is not None and model_timestamp > evaluation:
                errors.append("model timestamp is later than evaluation timestamp")
            if evaluation is not None and _age_limit_seconds(max_model_age_seconds) is not None:
                age = (evaluation - model_timestamp).total_seconds()
                if age < 0 or age > float(_age_limit_seconds(max_model_age_seconds)):
                    warnings.append("model provenance is stale")
        except (TypeError, ValueError):
            errors.append("model provenance timestamp is invalid")
    if model_status in {"stale", "expired", "missing", "unknown"}:
        warnings.append(f"model provenance is stale: {model_status}")

    warnings = list(dict.fromkeys(warnings))
    errors = list(dict.fromkeys(errors))
    return to_jsonable({
        "ok": not errors and not any("stale" in warning for warning in warnings),
        "errors": errors,
        "warnings": warnings,
        "data": {"source": data_source, "version": data_version, "as_of": data_as_of, "status": data_status or None},
        "model": {"model_id": model_id, "model_version": model_version, "as_of": model_as_of, "status": model_status or None},
    })


def validate_provenance(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for callers that name the check as validation."""

    return check_data_model_provenance(*args, **kwargs)


def check_data_freshness(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if "now" in kwargs and "evaluation_at" not in kwargs:
        kwargs["evaluation_at"] = kwargs.pop("now")
    if "max_age_seconds" in kwargs:
        age = kwargs.pop("max_age_seconds")
        kwargs.setdefault("max_data_age_seconds", age)
    return check_data_model_provenance(*args, **kwargs)


def check_model_freshness(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if "now" in kwargs and "evaluation_at" not in kwargs:
        kwargs["evaluation_at"] = kwargs.pop("now")
    if "max_age_seconds" in kwargs:
        age = kwargs.pop("max_age_seconds")
        kwargs.setdefault("max_model_age_seconds", age)
    return check_data_model_provenance(*args, **kwargs)


def check_data_model_freshness(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return check_data_model_provenance(*args, **kwargs)


def check_leakage(*args: Any, **kwargs: Any) -> list[str]:
    return check_split_leakage(*args, **kwargs)


@dataclass(slots=True)
class ValidationReport:
    folds: list[dict[str, Any]] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    promotion_eligible: bool = False
    validation_mode: str = "single_pass"
    provenance: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ValidationReport":
        if not isinstance(payload, Mapping):
            raise TypeError("ValidationReport payload must be a dict")
        mode = str(payload.get("validation_mode", payload.get("mode", "single_pass")) or "single_pass")
        folds = payload.get("folds", [])
        aggregate = payload.get("aggregate", {})
        warning_values = payload.get("warnings", [])
        provenance = payload.get("provenance", {})
        return cls(
            folds=[dict(value) for value in folds if isinstance(value, Mapping)] if isinstance(folds, Sequence) and not isinstance(folds, (str, bytes)) else [],
            aggregate=dict(aggregate) if isinstance(aggregate, Mapping) else {},
            warnings=[str(value) for value in warning_values] if isinstance(warning_values, Sequence) and not isinstance(warning_values, (str, bytes)) else [],
            promotion_eligible=bool(payload.get("promotion_eligible", False)),
            validation_mode=mode,
            provenance=dict(provenance) if isinstance(provenance, Mapping) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable({
            "folds": self.folds,
            "aggregate": self.aggregate,
            "warnings": list(dict.fromkeys(self.warnings)),
            "promotion_eligible": bool(self.promotion_eligible),
            "validation_mode": self.validation_mode,
            "provenance": self.provenance,
        })


@dataclass(slots=True)
class PromotionDecision:
    accepted: bool
    environment: str
    failed_checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    activation_safe: bool = False
    preview: bool = False

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable({
            "accepted": bool(self.accepted),
            "environment": self.environment,
            "failed_checks": list(dict.fromkeys(self.failed_checks)),
            "warnings": list(dict.fromkeys(self.warnings)),
            "activation_safe": bool(self.activation_safe),
            "preview": bool(self.preview),
        })


def evaluate_validation_folds(
    folds: list[object],
    benchmarks: dict[str, object],
) -> ValidationReport:
    """Evaluate net/gross fold performance and conservative significance fields."""

    fold_payloads: list[dict[str, Any]] = []
    net_series: list[pd.Series] = []
    gross_series: list[pd.Series] = []
    outcomes: list[float] = []
    cost_drag_total = 0.0
    turnover_total = 0.0
    modes: list[str] = []
    warnings: list[str] = []
    provenance_results: list[dict[str, Any]] = []
    for number, run in enumerate(folds):
        spec = _run_spec(run)
        validation = spec.get("validation")
        if isinstance(validation, Mapping):
            modes.append(str(validation.get("mode") or "single_pass"))
        path_id = str(_run_metrics(run).get("path_id") or f"fold-{number}")
        fold, net, gross, run_outcomes = _fold_metrics(run, path_id)
        fold_payloads.append(fold)
        net_series.append(net)
        gross_series.append(gross)
        outcomes.extend(run_outcomes)
        cost_drag_total += float(fold.get("cost_drag") or 0.0)
        turnover_total += float(fold.get("turnover") or 0.0)
        if len(net) < MIN_STATISTICAL_OBSERVATIONS:
            warnings.append(f"fold {path_id} has insufficient observations for statistical significance: {len(net)} < {MIN_STATISTICAL_OBSERVATIONS}")
        provenance = _provenance_for_run(run)
        if provenance is None:
            warnings.append(f"fold {path_id} data/model provenance is unavailable")
        else:
            evaluation_at = net.index[-1] if not net.empty else None
            result = check_data_model_provenance(provenance, evaluation_at=evaluation_at)
            provenance_results.append(result)
            warnings.extend(result["warnings"])
            warnings.extend(result["errors"])

    net = _concat_series(net_series)
    gross = _concat_series(gross_series)
    if gross.empty and not net.empty:
        gross = net.copy()
    net_nav = _returns_nav(net)
    gross_nav = _returns_nav(gross)
    net_cagr = _cagr_from_returns(net)
    gross_cagr = _cagr_from_returns(gross)
    drawdown = _max_drawdown(net_nav)
    fold_cagrs = [value["net_cagr"] for value in fold_payloads if _finite_float(value.get("net_cagr")) is not None]
    regime_values = [_finite_float(value.get("regime_concentration")) for value in fold_payloads]
    regime_values = [value for value in regime_values if value is not None]
    benchmark = _benchmark_run(benchmarks)
    benchmark_net = _run_returns(benchmark, "net") if benchmark is not None else pd.Series(dtype="float64")
    benchmark_cagr = _cagr_from_returns(benchmark_net) if not benchmark_net.empty else _finite_float(_run_metrics(benchmark).get("cagr")) if benchmark is not None else None
    benchmark_excess = net_cagr - benchmark_cagr if net_cagr is not None and benchmark_cagr is not None else None
    if benchmark is None or benchmark_cagr is None:
        warnings.append("cost-adjusted benchmark excess is unavailable")

    tested = _tested_configurations(folds)
    aggregate: dict[str, Any] = {
        "gross_cagr": gross_cagr,
        "net_cagr": net_cagr,
        "cagr": net_cagr,
        "gross_return": float((1.0 + gross.fillna(0.0)).prod() - 1.0) if not gross.empty else 0.0,
        "net_return": float((1.0 + net.fillna(0.0)).prod() - 1.0) if not net.empty else 0.0,
        "volatility": _volatility(net),
        "gross_volatility": _volatility(gross),
        "sharpe": _sharpe(net),
        "gross_sharpe": _sharpe(gross),
        "sortino": _sortino(net),
        "max_drawdown": drawdown,
        "calmar": _calmar(net_cagr, drawdown),
        "turnover": turnover_total,
        "cost_drag": cost_drag_total,
        "hit_rate": _hit_rate(outcomes),
        "profit_factor": _profit_factor(outcomes),
        "trade_count": int(sum(int(value.get("trade_count") or 0) for value in fold_payloads)),
        "test_periods": len(fold_payloads),
        "n_observations": len(net),
        "benchmark_excess_cagr": benchmark_excess,
        "benchmark_excess": benchmark_excess,
        "fold_dispersion": float(np.std(fold_cagrs, ddof=1)) if len(fold_cagrs) > 1 else None,
        "net_cagr_std": float(np.std(fold_cagrs, ddof=1)) if len(fold_cagrs) > 1 else None,
        "regime_concentration": max(regime_values) if regime_values else None,
        "regime_stability": 1.0 - max(regime_values) if regime_values else None,
        "tested_configurations": tested,
    }
    if aggregate["regime_concentration"] is None:
        warnings.append("regime concentration is unavailable")

    matrix = _configuration_matrix(folds)
    if len(net) < MIN_STATISTICAL_OBSERVATIONS:
        warnings.append(f"DSR unavailable: insufficient observations ({len(net)} < {MIN_STATISTICAL_OBSERVATIONS})")
    elif tested <= 1:
        warnings.append("DSR unavailable: tested configuration count is not greater than one")
    else:
        try:
            from ml.validation import deflated_sharpe_ratio
            trial_sharpes = None
            if matrix is not None and matrix.shape[0] >= 2:
                standard_deviation = matrix.std(axis=0, ddof=1)
                trial_sharpes = matrix.mean(axis=0) / np.where(standard_deviation > 0, standard_deviation, np.nan)
                trial_sharpes = trial_sharpes[np.isfinite(trial_sharpes)]
            aggregate["dsr"] = deflated_sharpe_ratio(
                net.to_numpy(), n_trials=tested, trial_sharpes=trial_sharpes,
            )
            if aggregate["dsr"] is None:
                warnings.append("DSR unavailable: Sharpe variance is not estimable")
        except (ImportError, ValueError, TypeError):
            aggregate["dsr"] = None
            warnings.append("DSR unavailable: calculation failed")
    if "dsr" not in aggregate:
        aggregate["dsr"] = None
    aggregate["dsr_n_trials"] = tested

    if matrix is None or matrix.shape[0] < 10:
        aggregate["pbo"] = None
        aggregate["pbo_n_configs"] = int(matrix.shape[1]) if matrix is not None else 0
        warnings.append("PBO unavailable: configuration return matrix is missing or too small")
    else:
        try:
            from ml.validation import pbo_cscv
            result = pbo_cscv(matrix, n_splits=min(10, matrix.shape[0]))
            aggregate["pbo"] = result.get("pbo") if isinstance(result, Mapping) else None
            aggregate["pbo_n_configs"] = int(matrix.shape[1])
            aggregate["pbo_n_combos"] = int(result.get("n_combos", 0)) if isinstance(result, Mapping) else 0
            if aggregate["pbo"] is None:
                warnings.append("PBO unavailable: configuration matrix is insufficient")
        except (ImportError, ValueError, TypeError):
            aggregate["pbo"] = None
            aggregate["pbo_n_configs"] = int(matrix.shape[1])
            warnings.append("PBO unavailable: calculation failed")

    mode = modes[0] if modes and all(value == modes[0] for value in modes) else "single_pass"
    if len(set(modes)) > 1:
        warnings.append("validation folds use inconsistent validation modes")
    provenance = {
        "ok": bool(provenance_results) and all(result.get("ok") for result in provenance_results),
        "checks": provenance_results,
    }
    if not provenance_results and folds:
        provenance["ok"] = False
    aggregate["provenance_ok"] = provenance["ok"]
    report = ValidationReport(
        folds=fold_payloads,
        aggregate=aggregate,
        warnings=list(dict.fromkeys(str(value) for value in warnings)),
        promotion_eligible=False,
        validation_mode=mode,
        provenance=provenance,
    )
    return report


def _config_number(config: Mapping[str, Any], key: str, default: float) -> float:
    value = _finite_float(config.get(key, default))
    return default if value is None else value


def promotion_gate(report: ValidationReport, config: dict[str, object]) -> PromotionDecision:
    """Apply activation checks without conflating shadow, pilot, and live."""

    if not isinstance(report, ValidationReport):
        report = ValidationReport.from_dict(report)  # type: ignore[arg-type]
    settings = config if isinstance(config, Mapping) else {}
    mode = str(settings.get("mode") or report.validation_mode or "single_pass").strip().lower()
    requested_environment = str(settings.get("environment") or "shadow").strip().lower()
    environment = requested_environment
    failed: list[str] = []
    gate_warnings = list(report.warnings)
    preview = mode == "single_pass" or bool(settings.get("preview", False))

    if requested_environment not in _VALID_ENVIRONMENTS:
        failed.append("environment")
        gate_warnings.append(f"unsupported promotion environment: {requested_environment}")
    if preview:
        failed.append("preview_only")
        gate_warnings.append("single_pass is preview-only and cannot be activation-safe")
    elif mode not in {"purged_walk_forward", "cpcv"}:
        failed.append("validation_mode")
        gate_warnings.append(f"validation mode is not activation-safe: {mode}")

    aggregate = report.aggregate
    min_trades = int(_config_number(settings, "min_trades", 100.0))
    min_periods = int(_config_number(settings, "min_test_periods", 4.0))
    if int(aggregate.get("trade_count") or 0) < min_trades:
        failed.append("min_trades")
    test_periods = int(aggregate.get("test_periods") or len(report.folds))
    if test_periods < min_periods:
        failed.append("min_test_periods")
    min_observations = int(_config_number(settings, "min_observations", float(MIN_STATISTICAL_OBSERVATIONS)))
    observed = aggregate.get("n_observations")
    if observed is None:
        observed = sum(int(value.get("n_observations") or value.get("test_periods") or 0) for value in report.folds)
    if observed and int(observed) < min_observations:
        failed.append("min_observations")

    if bool(settings.get("require_cost_adjusted_positive_excess", True)):
        excess = _finite_float(aggregate.get("benchmark_excess_cagr", aggregate.get("net_excess_cagr")))
        if excess is None or excess <= 0:
            failed.append("net_excess")

    max_drawdown = _config_number(settings, "max_drawdown", _config_number(settings, "max_mdd", 0.25))
    drawdown = _finite_float(aggregate.get("max_drawdown"))
    if drawdown is None or abs(drawdown) > abs(max_drawdown):
        failed.append("max_drawdown")
    max_turnover = _config_number(settings, "max_turnover", 1.0)
    turnover = _finite_float(aggregate.get("turnover"))
    if turnover is None or turnover > max_turnover:
        failed.append("max_turnover")

    max_pbo = _config_number(settings, "max_pbo", 0.5)
    pbo = _finite_float(aggregate.get("pbo"))
    if pbo is None:
        failed.append("pbo_unavailable")
    elif pbo > max_pbo:
        failed.append("max_pbo")

    min_dsr = _config_number(settings, "min_dsr", 0.95)
    dsr = _finite_float(aggregate.get("dsr"))
    if dsr is None:
        failed.append("dsr_unavailable")
    elif dsr < min_dsr:
        failed.append("min_dsr")

    max_regime = _config_number(settings, "max_regime_concentration", 0.75)
    concentration = _finite_float(aggregate.get("regime_concentration"))
    if concentration is None:
        failed.append("max_regime_concentration_unavailable")
    elif concentration > max_regime:
        failed.append("max_regime_concentration")

    provenance_ok = aggregate.get("provenance_ok")
    if provenance_ok is False:
        failed.append("stale_provenance" if any("stale" in warning for warning in gate_warnings) else "provenance")
    if aggregate.get("leakage_detected"):
        failed.append("leakage")

    canonical_environment = _ENVIRONMENT_ALIASES.get(environment, environment)
    if canonical_environment == "live" and not bool(settings.get("explicit_live_activation", False)):
        failed.append("live_activation_explicit")
        gate_warnings.append("live activation requires explicit_live_activation=true")
    if canonical_environment == "shadow":
        gate_warnings.append("shadow environment is non-activating")

    failed = list(dict.fromkeys(failed))
    gate_warnings = list(dict.fromkeys(str(value) for value in gate_warnings))
    accepted = not failed
    activation_safe = accepted and not preview and canonical_environment in {"pilot", "live"}
    return PromotionDecision(
        accepted=accepted,
        environment=environment,
        failed_checks=failed,
        warnings=gate_warnings,
        activation_safe=activation_safe,
        preview=preview,
    )


__all__ = [
    "ValidationSplit",
    "ValidationReport",
    "PromotionDecision",
    "make_purged_walk_forward_splits",
    "make_cpcv_splits",
    "check_split_leakage",
    "check_data_model_provenance",
    "validate_provenance",
    "check_data_freshness",
    "check_model_freshness",
    "check_data_model_freshness",
    "check_leakage",
    "evaluate_validation_folds",
    "promotion_gate",
]
