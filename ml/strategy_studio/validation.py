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


def _json_order_value(value: object) -> object:
    if _is_timestamp_like(value):
        try:
            return _timestamp_text(value)
        except (TypeError, ValueError):
            pass
    return to_jsonable(value)


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


def _is_missing_scalar(value: object) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _has_metadata_value(value: object) -> bool:
    if value is None or _is_missing_scalar(value):
        return False
    return bool(str(value).strip())


@dataclass(frozen=True, slots=True)
class ValidationSplit:
    """One chronological train/test split and its excluded observations."""

    train: pd.Index
    test: pd.Index
    path_id: str = "path-0"
    embargo_bars: int = 0
    blocked: pd.Index = field(default_factory=lambda: pd.Index([]), repr=False, compare=False)
    future_training: bool = False
    strictly_chronological: bool = False
    label_horizon: int | None = None
    label_end_provided: bool = False

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
        object.__setattr__(self, "future_training", bool(self.future_training))
        object.__setattr__(self, "strictly_chronological", bool(self.strictly_chronological))
        horizon = None if self.label_horizon is None else _non_negative_int(self.label_horizon, "label_horizon")
        object.__setattr__(self, "label_horizon", horizon)
        object.__setattr__(self, "label_end_provided", bool(self.label_end_provided))

    def to_dict(self) -> dict[str, Any]:
        train_max = self.train[-1] if len(self.train) else None
        test_min = self.test[0] if len(self.test) else None
        return to_jsonable({
            "train": self.train,
            "test": self.test,
            "path_id": self.path_id,
            "embargo_bars": self.embargo_bars,
            "blocked": self.blocked,
            "future_training": self.future_training,
            "strictly_chronological": self.strictly_chronological,
            "label_horizon": self.label_horizon,
            "label_end_provided": self.label_end_provided,
            "chronology_evidence": {
                "fold_id": self.path_id,
                "valid": bool(
                    len(self.train) and len(self.test)
                    and not self.future_training
                    and train_max < test_min
                ),
                "future_training": self.future_training,
                "train_before_test": bool(
                    len(self.train) and len(self.test) and train_max < test_min
                ),
                "train_max": _json_order_value(train_max) if train_max is not None else None,
                "test_min": _json_order_value(test_min) if test_min is not None else None,
            },
        })


def check_split_leakage(
    split: ValidationSplit,
    *,
    label_horizon: int | None = None,
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

    horizon = (
        _non_negative_int(label_horizon, "label_horizon")
        if label_horizon is not None
        else split.label_horizon
    )
    if horizon is None and label_end is None and not split.label_end_provided:
        issues.append("label_horizon_missing")
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
            try:
                normalised_ends = _label_end_values(split.train, ends, 0)
            except (TypeError, ValueError):
                issues.append("label_end_invalid")
                normalised_ends = None
            if normalised_ends is None:
                return list(dict.fromkeys(issues))
            ends = normalised_ends
            test_start = split.test[0]
            test_end = split.test[-1]
            for start, end in zip(split.train, ends):
                if end is None or _is_missing_scalar(end):
                    issues.append("label_end_invalid")
                    break
                if end >= test_start and start <= test_end:
                    issues.append("label_end_overlaps_test")
                    break
        elif label_end is not None:
            issues.append("label_end_length_mismatch")
    return list(dict.fromkeys(issues))


def _label_end_values(
    index: pd.Index,
    label_end: pd.Series | Sequence[object] | None,
    label_horizon: int | None,
) -> list[object] | None:
    if label_end is None:
        if label_horizon is None:
            raise ValueError("label horizon metadata is required for purged validation")
        if label_horizon <= 0:
            return None
        return [index[min(position + label_horizon, len(index) - 1)] for position in range(len(index))]
    values = label_end.tolist() if isinstance(label_end, pd.Series) else list(label_end)
    if len(values) != len(index):
        raise ValueError("label_end must have one value per index row")
    if isinstance(index, pd.DatetimeIndex):
        try:
            parsed = pd.DatetimeIndex(pd.to_datetime(values, errors="raise"))
            if getattr(index, "tz", None) is not None:
                parsed = pd.DatetimeIndex(pd.to_datetime(parsed, utc=True))
            elif parsed.tz is not None:
                parsed = parsed.tz_convert(None)
            if any(_is_missing_scalar(value) for value in parsed):
                raise ValueError("label_end contains invalid or NaT endpoints")
            return list(parsed)
        except (TypeError, ValueError) as exc:
            raise ValueError("label_end contains invalid timestamps") from exc
    if any(_is_missing_scalar(value) for value in values):
        raise ValueError("label_end contains invalid or NaT endpoints")
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


def _post_test_blackout_positions(
    index: pd.Index,
    test_positions: Sequence[int],
    embargo_bars: int,
    label_ends: Sequence[object] | None,
    label_horizon: int | None,
) -> set[int]:
    """Return rows after each test label window that cannot train a model."""

    blocked: set[int] = set()
    numeric_horizon = label_horizon or 0
    for position in test_positions:
        right = min(len(index), position + numeric_horizon + embargo_bars + 1)
        if label_ends is not None:
            endpoint = label_ends[position]
            cursor = position + 1
            while cursor < len(index) and index[cursor] <= endpoint:
                cursor += 1
            right = max(right, min(len(index), cursor + embargo_bars))
        blocked.update(range(position + 1, right))
    return blocked


def make_purged_walk_forward_splits(
    index: pd.Index,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    embargo_bars: int,
    *,
    label_horizon: int | None = None,
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
    horizon = _non_negative_int(label_horizon, "label_horizon") if label_horizon is not None else None
    if train_size == 0 or test_size == 0 or step == 0:
        _warn("purged walk-forward windows require positive train, test, and step sizes")
        return []
    if not len(values) or train_size + embargo + test_size > len(values):
        _warn("purged walk-forward windows do not fit the supplied index")
        return []

    try:
        ends = _label_end_values(values, label_end, horizon)
    except ValueError as exc:
        _warn(str(exc))
        return []
    splits: list[ValidationSplit] = []
    start = 0
    path = 0
    while start + train_size + embargo + test_size <= len(values):
        train_positions = list(range(start, start + train_size))
        test_start = start + train_size + embargo
        test_positions = list(range(test_start, test_start + test_size))
        blocked_positions = set(range(start + train_size, test_start))
        blocked_positions.update(
            _post_test_blackout_positions(values, test_positions, embargo, ends, horizon)
        )
        blocked_positions.difference_update(test_positions)
        purged = _overlapping_label_positions(values, train_positions, test_positions, ends)
        train_positions = [position for position in train_positions if position not in purged]
        blocked_positions.update(purged)
        split = ValidationSplit(
            train=values.take(train_positions),
            test=values.take(test_positions),
            path_id=f"wf-{path}",
            embargo_bars=embargo,
            blocked=values.take(sorted(blocked_positions)),
            label_horizon=horizon,
            label_end_provided=label_end is not None,
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
    label_horizon: int | None = None,
    label_end: pd.Series | Sequence[object] | None = None,
    strictly_chronological: bool = False,
) -> list[ValidationSplit]:
    """Enumerate chronological CPCV paths with purge and embargo blackout rows."""

    values = _normalise_index(index)
    group_count = _non_negative_int(groups, "groups")
    selected_count = _non_negative_int(test_groups, "test_groups")
    embargo = _non_negative_int(embargo_bars, "embargo_bars")
    horizon = _non_negative_int(label_horizon, "label_horizon") if label_horizon is not None else None
    if group_count < 2 or selected_count == 0 or selected_count >= group_count:
        _warn("CPCV requires at least two groups and fewer test groups than total groups")
        return []
    if group_count > len(values) or not len(values):
        _warn("CPCV groups do not fit the supplied index")
        return []

    group_positions = [list(part) for part in np.array_split(np.arange(len(values)), group_count)]
    try:
        ends = _label_end_values(values, label_end, horizon)
    except ValueError as exc:
        _warn(str(exc))
        return []
    splits: list[ValidationSplit] = []
    for combo in combinations(range(group_count), selected_count):
        test_positions = sorted(position for group in combo for position in group_positions[group])
        test_set = set(test_positions)
        blocked: set[int] = set()
        for position in test_positions:
            left = max(0, position - embargo - (horizon or 0))
            right = min(len(values), position + embargo + (horizon or 0) + 1)
            blocked.update(range(left, right))
        blocked.update(_post_test_blackout_positions(values, test_positions, embargo, ends, horizon))
        blocked.difference_update(test_set)
        if strictly_chronological:
            candidate_positions = range(min(test_positions))
        else:
            candidate_positions = range(len(values))
        candidates = [position for position in candidate_positions if position not in test_set and position not in blocked]
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
            future_training=any(position > min(test_positions) for position in train_positions),
            strictly_chronological=strictly_chronological,
            label_horizon=horizon,
            label_end_provided=label_end is not None,
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
    if isinstance(run, Mapping):
        metrics = run.get("metrics")
        if isinstance(metrics, Mapping) and metrics:
            return metrics
        nested = run.get("run")
        if nested is not None and nested is not run:
            return _run_metrics(nested)
        return run
    metrics = _run_value(run, "metrics", {})
    if isinstance(metrics, Mapping):
        return metrics
    return {}


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
    if isinstance(equity, pd.DataFrame):
        return equity.copy()
    if equity is None and isinstance(run, Mapping) and run.get("run") is not None:
        return _run_equity(run["run"])
    return pd.DataFrame()


def _run_returns(run: object, kind: str) -> pd.Series:
    raw_equity = _run_value(run, "equity", None)
    if raw_equity is None and isinstance(run, Mapping) and run.get("run") is not None:
        raw_equity = _run_value(run["run"], "equity", None)
    if isinstance(raw_equity, pd.Series):
        return _normalise_series(raw_equity.pct_change().fillna(0.0))
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


def _periods_per_year(index: pd.Index, configured: object | None = None) -> float:
    configured_value = _finite_float(configured)
    if configured_value is not None and configured_value > 0:
        return configured_value
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return float(TRADING_DAYS)
    deltas = np.diff(index.asi8) / 1_000_000_000.0
    deltas = deltas[deltas > 0]
    if not len(deltas):
        return float(TRADING_DAYS)
    median_seconds = float(np.median(deltas))
    day = 24 * 60 * 60
    if median_seconds < 20 * 60 * 60:
        return max(1.0, 365.25 * day / median_seconds)
    if median_seconds <= 2 * day:
        return float(TRADING_DAYS)
    if median_seconds <= 10 * day:
        return float(365.25 / 7.0)
    if median_seconds <= 45 * day:
        return 12.0
    return max(1.0, 365.25 * day / median_seconds)


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


def _volatility(returns: pd.Series, configured_periods: object | None = None) -> float | None:
    if len(returns) < 2:
        return None
    deviation = float(returns.std(ddof=1))
    return deviation * sqrt(_periods_per_year(returns.index, configured_periods)) if isfinite(deviation) else None


def _sharpe(returns: pd.Series, configured_periods: object | None = None) -> float | None:
    if len(returns) < 2:
        return None
    deviation = float(returns.std(ddof=1))
    if not isfinite(deviation) or deviation <= 0:
        return None
    return float(returns.mean() / deviation * sqrt(_periods_per_year(returns.index, configured_periods)))


def _sortino(returns: pd.Series, configured_periods: object | None = None) -> float | None:
    if len(returns) < 2:
        return None
    downside = np.minimum(returns.to_numpy(dtype="float64"), 0.0)
    deviation = float(np.sqrt(np.mean(np.square(downside))))
    if not isfinite(deviation) or deviation <= 0:
        return None
    return float(returns.mean() / deviation * sqrt(_periods_per_year(returns.index, configured_periods)))


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


def _configured_periods_per_year(run: object) -> float | None:
    metrics = _run_metrics(run)
    candidate = metrics.get("periods_per_year")
    if candidate is None:
        validation = _run_spec(run).get("validation")
        if isinstance(validation, Mapping):
            candidate = validation.get("periods_per_year")
    if candidate is None:
        candidate = _run_spec(run).get("periods_per_year")
    value = _finite_float(candidate)
    return value if value is not None and value > 0 else None


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
    periods_per_year = _periods_per_year(net.index, _configured_periods_per_year(run)) if not net.empty else None
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
        "volatility": _volatility(net, periods_per_year),
        "gross_volatility": _volatility(gross, periods_per_year),
        "sharpe": _sharpe(net, periods_per_year),
        "gross_sharpe": _sharpe(gross, periods_per_year),
        "sortino": _sortino(net, periods_per_year),
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
        "periods_per_year": periods_per_year,
    }
    for key in (
        "regime_concentration", "regime", "tested_configurations", "n_trials",
        "future_training", "strictly_chronological", "chronology_evidence",
        "cpcv_chronology_evidence",
    ):
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


def _configured_provenance_limits(run: object) -> tuple[object | None, object | None]:
    """Read explicit data/model age limits from validation or promotion config."""

    spec = _run_spec(run)
    sections = [spec.get("validation"), spec.get("promotion"), spec.get("provenance"), spec]
    data_limit: object | None = None
    model_limit: object | None = None
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        if data_limit is None:
            for key in ("max_data_age_seconds", "data_max_age_seconds", "data_age_limit_seconds"):
                if section.get(key) is not None:
                    data_limit = section[key]
                    break
        if model_limit is None:
            for key in ("max_model_age_seconds", "model_max_age_seconds", "model_age_limit_seconds"):
                if section.get(key) is not None:
                    model_limit = section[key]
                    break
    return data_limit, model_limit


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

    The function never uses wall-clock time implicitly, keeping results
    replayable. Missing identity, as-of timestamps, or configured age limits
    always make ``ok`` false; preview callers can still use the diagnostics.
    """

    payload = provenance if isinstance(provenance, Mapping) else {}
    errors: list[str] = []
    warnings: list[str] = []
    try:
        evaluation = _canonical_timestamp(evaluation_at) if _has_metadata_value(evaluation_at) else None
    except (TypeError, ValueError):
        evaluation = None
        errors.append("evaluation timestamp is invalid")
    data_age_limit = _age_limit_seconds(max_data_age_seconds)
    model_age_limit = _age_limit_seconds(max_model_age_seconds)
    age_limits_configured = data_age_limit is not None and model_age_limit is not None
    if not age_limits_configured:
        warnings.append("provenance age limits are not configured")
    if evaluation is None:
        warnings.append("evaluation timestamp is missing")
    data = _provenance_section(payload, "data")
    model = _provenance_section(payload, "model")
    if not data and any(key in payload for key in ("source", "data_source", "data_version", "data_as_of")):
        data = payload
    if not model and any(key in payload for key in ("model_id", "model_version", "trained_until", "model_as_of")):
        model = payload

    data_source = data.get("source", data.get("data_source"))
    data_version = data.get("version", data.get("data_version"))
    data_as_of = data.get("as_of", data.get("timestamp", data.get("data_as_of")))
    data_status_value = data.get("freshness", data.get("status", ""))
    data_status = str(data_status_value).strip().lower() if _has_metadata_value(data_status_value) else ""
    if not _has_metadata_value(data_source) or not _has_metadata_value(data_version):
        warnings.append("data provenance is incomplete")
    if not data_status:
        warnings.append("data provenance freshness metadata is missing")
    if not _has_metadata_value(data_as_of):
        warnings.append("data provenance timestamp is missing")
    else:
        try:
            data_timestamp = _canonical_timestamp(data_as_of)
            if evaluation is not None and data_timestamp > evaluation:
                errors.append("data timestamp is later than evaluation timestamp")
            if evaluation is not None and data_age_limit is not None:
                age = (evaluation - data_timestamp).total_seconds()
                if age < 0 or age > data_age_limit:
                    warnings.append("data provenance is stale")
        except (TypeError, ValueError):
            errors.append("data provenance timestamp is invalid")
    if data_status in {"stale", "expired", "missing", "unknown"}:
        warnings.append(f"data provenance is stale: {data_status}")

    model_id = model.get("model_id")
    model_version = model.get("model_version", model.get("version"))
    model_as_of = model.get("as_of", model.get("trained_until", model.get("model_as_of")))
    model_status_value = model.get("freshness", model.get("status", ""))
    model_status = str(model_status_value).strip().lower() if _has_metadata_value(model_status_value) else ""
    if not _has_metadata_value(model_id) or not _has_metadata_value(model_version):
        warnings.append("model provenance is incomplete")
    if not model_status:
        warnings.append("model provenance freshness metadata is missing")
    if not _has_metadata_value(model_as_of):
        warnings.append("model provenance timestamp is missing")
    else:
        try:
            model_timestamp = _canonical_timestamp(model_as_of)
            if evaluation is not None and model_timestamp > evaluation:
                errors.append("model timestamp is later than evaluation timestamp")
            if evaluation is not None and model_age_limit is not None:
                age = (evaluation - model_timestamp).total_seconds()
                if age < 0 or age > model_age_limit:
                    warnings.append("model provenance is stale")
        except (TypeError, ValueError):
            errors.append("model provenance timestamp is invalid")
    if model_status in {"stale", "expired", "missing", "unknown"}:
        warnings.append(f"model provenance is stale: {model_status}")

    warnings = list(dict.fromkeys(warnings))
    errors = list(dict.fromkeys(errors))
    complete = bool(
        _has_metadata_value(data_source) and _has_metadata_value(data_version) and _has_metadata_value(data_as_of)
        and data_status and _has_metadata_value(model_id) and _has_metadata_value(model_version)
        and _has_metadata_value(model_as_of)
        and model_status and evaluation is not None
    )
    ok = bool(complete and age_limits_configured and not errors and not any("stale" in warning for warning in warnings))
    return to_jsonable({
        "ok": ok,
        "provenance_ok": ok,
        "age_limits_configured": age_limits_configured,
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
    periods_per_year_values: list[float] = []
    cpcv_future_training = False
    cpcv_chronology_candidates: list[object] = []
    cpcv_fold_count = 0
    cpcv_fold_ids: list[str] = []
    for number, run in enumerate(folds):
        spec = _run_spec(run)
        metrics = _run_metrics(run)
        validation = spec.get("validation")
        validation_mode = ""
        path_id = str(metrics.get("path_id") or f"fold-{number}")
        if isinstance(validation, Mapping):
            validation_mode = str(validation.get("mode") or "single_pass").strip().lower()
            modes.append(validation_mode)
            if validation_mode == "cpcv":
                cpcv_fold_count += 1
                cpcv_fold_ids.append(path_id)
                cpcv_future_training = cpcv_future_training or bool(
                    validation.get("future_training", False)
                )
                candidate = validation.get("cpcv_chronology_evidence", validation.get("chronology_evidence"))
                if candidate is None:
                    candidate = metrics.get("cpcv_chronology_evidence", metrics.get("chronology_evidence"))
                if isinstance(candidate, Mapping):
                    cpcv_chronology_candidates.append(candidate)
                elif isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
                    cpcv_chronology_candidates.extend(candidate)
        raw_equity = _run_value(run, "equity", None)
        if isinstance(raw_equity, (pd.DataFrame, pd.Series)) and raw_equity.index.has_duplicates:
            warnings.append(f"fold-{number} has duplicate timestamps")
        fold, net, gross, run_outcomes = _fold_metrics(run, path_id)
        fold["validation_mode"] = validation_mode or "single_pass"
        if not net.empty:
            fold["test_start"] = _json_order_value(net.index[0])
            fold["test_end"] = _json_order_value(net.index[-1])
        if validation_mode == "cpcv" and isinstance(validation, Mapping):
            actual_train_max = _validation_actual_timestamp(
                validation,
                ("train_max", "train_end", "train_max_timestamp"),
                "train",
                -1,
            )
            if actual_train_max is not None:
                fold["train_max"] = _json_order_value(actual_train_max)
        if validation_mode == "cpcv":
            candidate = validation.get("cpcv_chronology_evidence", validation.get("chronology_evidence")) if isinstance(validation, Mapping) else None
            if candidate is None:
                candidate = metrics.get("cpcv_chronology_evidence", metrics.get("chronology_evidence"))
            if isinstance(candidate, Mapping):
                fold["chronology_evidence"] = to_jsonable(candidate)
        fold_payloads.append(fold)
        net_series.append(net)
        gross_series.append(gross)
        outcomes.extend(run_outcomes)
        if _finite_float(fold.get("periods_per_year")) is not None:
            periods_per_year_values.append(float(fold["periods_per_year"]))
        if not run_outcomes:
            warnings.append(f"fold {path_id} trade PnL unavailable; hit rate and profit factor omitted")
        if isinstance(validation, Mapping):
            cpcv_future_training = cpcv_future_training or bool(validation.get("future_training", False))
        if bool(metrics.get("future_training", False)) and validation_mode == "cpcv":
            cpcv_future_training = True
        cost_drag_total += float(fold.get("cost_drag") or 0.0)
        turnover_total += float(fold.get("turnover") or 0.0)
        if len(net) < MIN_STATISTICAL_OBSERVATIONS:
            warnings.append(f"fold {path_id} has insufficient observations for statistical significance: {len(net)} < {MIN_STATISTICAL_OBSERVATIONS}")
        provenance = _provenance_for_run(run)
        if provenance is None:
            missing_warning = f"fold {path_id} data/model provenance is unavailable"
            warnings.append(missing_warning)
            provenance_results.append({
                "fold": path_id,
                "ok": False,
                "provenance_ok": False,
                "age_limits_configured": False,
                "errors": [],
                "warnings": [missing_warning],
            })
        else:
            evaluation_at = net.index[-1] if not net.empty else None
            max_data_age, max_model_age = _configured_provenance_limits(run)
            result = check_data_model_provenance(
                provenance,
                evaluation_at=evaluation_at,
                max_data_age_seconds=max_data_age,
                max_model_age_seconds=max_model_age,
            )
            provenance_results.append(result)
            warnings.extend(result["warnings"])
            warnings.extend(result["errors"])

    seen_timestamps: set[object] = set()
    for series in net_series:
        overlap = seen_timestamps.intersection(set(series.index))
        if overlap:
            warnings.append("validation folds contain overlapping timestamps")
        seen_timestamps.update(series.index)
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
    benchmark_metrics = _run_metrics(benchmark) if benchmark is not None else {}
    supplied_benchmark_cagr = _finite_float(
        benchmark_metrics.get("net_cagr", benchmark_metrics.get("cagr"))
    )
    benchmark_cagr = (
        supplied_benchmark_cagr
        if supplied_benchmark_cagr is not None
        else _cagr_from_returns(benchmark_net)
        if not benchmark_net.empty
        else None
    )
    benchmark_excess = net_cagr - benchmark_cagr if net_cagr is not None and benchmark_cagr is not None else None
    if benchmark is None or benchmark_cagr is None:
        warnings.append("cost-adjusted benchmark excess is unavailable")

    matrix = _configuration_matrix(folds)
    tested = _tested_configurations(folds)
    if matrix is not None:
        tested = max(tested, int(matrix.shape[1]))
    if periods_per_year_values and len(set(periods_per_year_values)) == 1:
        aggregate_periods_per_year = periods_per_year_values[0]
    else:
        aggregate_periods_per_year = _periods_per_year(net.index)
    aggregate: dict[str, Any] = {
        "gross_cagr": gross_cagr,
        "net_cagr": net_cagr,
        "cagr": net_cagr,
        "gross_return": float((1.0 + gross.fillna(0.0)).prod() - 1.0) if not gross.empty else 0.0,
        "net_return": float((1.0 + net.fillna(0.0)).prod() - 1.0) if not net.empty else 0.0,
        "volatility": _volatility(net, aggregate_periods_per_year),
        "gross_volatility": _volatility(gross, aggregate_periods_per_year),
        "sharpe": _sharpe(net, aggregate_periods_per_year),
        "gross_sharpe": _sharpe(gross, aggregate_periods_per_year),
        "sortino": _sortino(net, aggregate_periods_per_year),
        "max_drawdown": drawdown,
        "calmar": _calmar(net_cagr, drawdown),
        "turnover": turnover_total,
        "cost_drag": cost_drag_total,
        "hit_rate": _hit_rate(outcomes),
        "profit_factor": _profit_factor(outcomes),
        "trade_count": int(sum(int(value.get("trade_count") or 0) for value in fold_payloads)),
        "test_periods": len(net),
        "fold_count": len(fold_payloads),
        "n_observations": len(net),
        "benchmark_excess_cagr": benchmark_excess,
        "benchmark_excess": benchmark_excess,
        "fold_dispersion": float(np.std(fold_cagrs, ddof=1)) if len(fold_cagrs) > 1 else None,
        "net_cagr_std": float(np.std(fold_cagrs, ddof=1)) if len(fold_cagrs) > 1 else None,
        "regime_concentration": max(regime_values) if regime_values else None,
        "regime_stability": 1.0 - max(regime_values) if regime_values else None,
        "tested_configurations": tested,
        "periods_per_year": aggregate_periods_per_year,
        "cpcv_future_training": cpcv_future_training,
    }
    if cpcv_fold_count:
        cpcv_fold_records = [
            fold for fold in fold_payloads
            if fold.get("validation_mode") == "cpcv"
        ]
        aggregate["cpcv_fold_count"] = cpcv_fold_count
        aggregate["cpcv_fold_ids"] = cpcv_fold_ids
        aggregate["cpcv_chronology_evidence"] = cpcv_chronology_candidates
        aggregate["cpcv_chronology_ok"] = _has_cpcv_chronology_evidence(
            cpcv_chronology_candidates,
            cpcv_fold_count,
            expected_ids=cpcv_fold_ids,
            actual_folds=cpcv_fold_records,
        )
    if aggregate["regime_concentration"] is None:
        warnings.append("regime concentration is unavailable")

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
            else:
                aggregate["dsr_evidence"] = {
                    "method": "deflated_sharpe_ratio",
                    "tested_configurations": tested,
                    "returns_observations": len(net),
                }
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
            else:
                aggregate["pbo_evidence"] = {
                    "method": "cscv",
                    "tested_configurations": int(matrix.shape[1]),
                    "matrix_shape": [int(matrix.shape[0]), int(matrix.shape[1])],
                    "n_combinations": aggregate["pbo_n_combos"],
                }
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


def _evidence_count(evidence: object) -> int | None:
    if not isinstance(evidence, Mapping):
        return None
    candidate = evidence.get("tested_configurations", evidence.get("tested_configuration_count", evidence.get("n_trials")))
    try:
        return int(candidate) if candidate is not None else None
    except (TypeError, ValueError):
        return None


def _has_dsr_evidence(evidence: object) -> bool:
    if not isinstance(evidence, Mapping) or (_evidence_count(evidence) or 0) < 2:
        return False
    method = str(evidence.get("method") or "").strip()
    observations = _finite_float(evidence.get("returns_observations"))
    return bool(method or (observations is not None and observations >= 2))


def _has_pbo_evidence(evidence: object) -> bool:
    if not isinstance(evidence, Mapping) or (_evidence_count(evidence) or 0) < 2:
        return False
    shape = evidence.get("matrix_shape")
    if shape is None and isinstance(evidence.get("matrix"), Sequence):
        matrix = evidence["matrix"]
        shape = getattr(matrix, "shape", None)
    if isinstance(shape, Sequence) and not isinstance(shape, (str, bytes)) and len(shape) == 2:
        try:
            return int(shape[0]) >= 10 and int(shape[1]) >= 2
        except (TypeError, ValueError):
            return False
    return bool(
        evidence.get("matrix_digest")
        or evidence.get("configuration_matrix")
        or evidence.get("matrix")
    )


def _proof_value(proof: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        if proof.get(key) is not None:
            return proof[key]
    return None


def _is_before_in_time(left: object, right: object) -> bool:
    if _is_timestamp_like(left) or _is_timestamp_like(right):
        try:
            return _canonical_timestamp(left) < _canonical_timestamp(right)
        except (TypeError, ValueError):
            return False
    try:
        return bool(left < right)  # type: ignore[operator]
    except (TypeError, ValueError):
        return False


def _same_time(left: object, right: object) -> bool:
    if _is_timestamp_like(left) or _is_timestamp_like(right):
        try:
            return _canonical_timestamp(left) == _canonical_timestamp(right)
        except (TypeError, ValueError):
            return False
    return left == right


def _consistent_timestamp(
    record: Mapping[str, Any],
    *keys: str,
) -> tuple[object | None, bool]:
    values = [record[key] for key in keys if key in record]
    if not values or any(_is_missing_scalar(value) for value in values):
        return None, False
    first = values[0]
    return first, all(_same_time(first, value) for value in values[1:])


def _validation_actual_timestamp(
    validation: Mapping[str, Any],
    keys: Sequence[str],
    sequence_key: str,
    position: int,
) -> object | None:
    if any(key in validation for key in keys):
        value, valid = _consistent_timestamp(validation, *keys)
        return value if valid else None
    values = validation.get(sequence_key)
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) and values:
        return values[position]
    return None


def _actual_cpcv_fold_records(report: ValidationReport) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    report_mode = str(report.validation_mode or "").strip().lower()
    for fold in report.folds:
        if not isinstance(fold, Mapping):
            continue
        fold_mode = str(fold.get("validation_mode") or "").strip().lower()
        if fold_mode == "cpcv" or (not fold_mode and report_mode == "cpcv"):
            records.append(fold)
    return records


def _has_cpcv_chronology_evidence(
    evidence: object,
    expected_folds: int,
    *,
    expected_ids: Sequence[str] | None = None,
    actual_folds: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    if expected_folds <= 0 or not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        return False
    if len(evidence) != expected_folds or expected_ids is None or len(expected_ids) != expected_folds:
        return False
    if actual_folds is None or len(actual_folds) != expected_folds:
        return False
    expected_id_set = {str(value) for value in expected_ids}
    if len(expected_id_set) != expected_folds:
        return False
    actual_by_id: dict[str, Mapping[str, Any]] = {}
    for actual in actual_folds:
        actual_id = _proof_value(actual, "path_id", "fold_id", "fold")
        if actual_id is None or str(actual_id) in actual_by_id:
            return False
        actual_by_id[str(actual_id)] = actual
    if set(actual_by_id) != expected_id_set:
        return False
    fold_ids: set[str] = set()
    for candidate in evidence:
        if not isinstance(candidate, Mapping):
            return False
        fold_id = _proof_value(candidate, "fold_id", "path_id", "fold")
        if fold_id is None or str(fold_id) in fold_ids or str(fold_id) not in expected_id_set:
            return False
        fold_ids.add(str(fold_id))
        if candidate.get("valid", candidate.get("proof_valid")) is not True:
            return False
        if candidate.get("future_training") is True:
            return False
        if candidate.get("future_training") is not False and candidate.get("no_future_training") is not True:
            return False
        if candidate.get("train_before_test", candidate.get("train_max_before_test_min")) is not True:
            return False
        train_max = _proof_value(candidate, "train_max", "train_end", "train_max_timestamp")
        test_min = _proof_value(candidate, "test_min", "test_start", "test_min_timestamp")
        if train_max is None or test_min is None or not _is_before_in_time(train_max, test_min):
            return False
        actual = actual_by_id[str(fold_id)]
        actual_evidence = actual.get("chronology_evidence")
        actual_proof = actual_evidence if isinstance(actual_evidence, Mapping) else {}
        if actual.get("future_training") is True or actual_proof.get("future_training") is True:
            return False
        if isinstance(actual_evidence, Mapping):
            if actual_evidence.get("valid", actual_evidence.get("proof_valid")) is False:
                return False
            if actual_evidence.get("train_before_test", actual_evidence.get("train_max_before_test_min")) is False:
                return False
        actual_train_max, train_timestamp_ok = _consistent_timestamp(
            actual, "train_max", "train_end", "train_max_timestamp"
        )
        actual_test_min, test_timestamp_ok = _consistent_timestamp(
            actual, "test_min", "test_start", "test_min_timestamp"
        )
        if (
            not train_timestamp_ok
            or not test_timestamp_ok
            or not _same_time(train_max, actual_train_max)
            or not _same_time(test_min, actual_test_min)
        ):
            return False
    return fold_ids == expected_id_set


def _cpcv_chronology_gate_ok(report: ValidationReport) -> bool:
    aggregate = report.aggregate
    actual_folds = _actual_cpcv_fold_records(report)
    declared_ids = aggregate.get("cpcv_fold_ids")
    if isinstance(declared_ids, Sequence) and not isinstance(declared_ids, (str, bytes)):
        expected_ids = [str(value) for value in declared_ids]
    else:
        expected_ids = [
            str(fold_id)
            for fold_id in (
                _proof_value(fold, "path_id", "fold_id", "fold")
                for fold in actual_folds
            )
            if fold_id is not None
        ]
    declared_count = aggregate.get("cpcv_fold_count")
    if declared_count is None and "fold_count" in aggregate:
        declared_count = aggregate["fold_count"]
    if declared_count is None:
        expected_count = len(expected_ids)
    else:
        try:
            expected_count = int(declared_count)
        except (TypeError, ValueError):
            return False
    chronology_evidence = aggregate.get("cpcv_chronology_evidence")
    if chronology_evidence is None:
        chronology_evidence = [
            fold["chronology_evidence"]
            for fold in actual_folds
            if isinstance(fold.get("chronology_evidence"), Mapping)
        ]
    return _has_cpcv_chronology_evidence(
        chronology_evidence,
        expected_count,
        expected_ids=expected_ids,
        actual_folds=actual_folds,
    )


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
    aggregate = report.aggregate

    if requested_environment not in _VALID_ENVIRONMENTS:
        failed.append("environment")
        gate_warnings.append(f"unsupported promotion environment: {requested_environment}")
    if preview:
        failed.append("preview_only")
        gate_warnings.append("single_pass is preview-only and cannot be activation-safe")
    elif mode not in {"purged_walk_forward", "cpcv"}:
        failed.append("validation_mode")
        gate_warnings.append(f"validation mode is not activation-safe: {mode}")
    if not preview and aggregate.get("cpcv_future_training"):
        failed.append("cpcv_future_training")
        gate_warnings.append("CPCV training includes groups after its test window")
    actual_cpcv_folds = _actual_cpcv_fold_records(report)
    cpcv_evidence_present = bool(
        mode == "cpcv"
        or actual_cpcv_folds
        or aggregate.get("cpcv_fold_count") is not None
        or aggregate.get("cpcv_fold_ids") is not None
        or aggregate.get("cpcv_chronology_evidence") is not None
        or aggregate.get("cpcv_chronology_ok") is not None
    )
    if not preview and cpcv_evidence_present and not _cpcv_chronology_gate_ok(report):
        failed.append("cpcv_chronology_evidence")
        gate_warnings.append("CPCV per-fold chronology evidence is incomplete, mismatched, or invalid")
    if not preview and mode == "cpcv":
        if not bool(settings.get("strictly_chronological", False)):
            failed.append("cpcv_not_activation_safe")
            gate_warnings.append("CPCV is diagnostic-only unless strictly_chronological=true")

    min_trades = int(_config_number(settings, "min_trades", 100.0))
    min_periods = int(_config_number(settings, "min_test_periods", 4.0))
    if int(aggregate.get("trade_count") or 0) < min_trades:
        failed.append("min_trades")
    if cpcv_evidence_present:
        fold_count = aggregate.get("cpcv_fold_count")
        if fold_count is None:
            fold_count = aggregate.get("fold_count")
        if fold_count is None:
            fold_count = len(actual_cpcv_folds)
    else:
        fold_count = aggregate.get("fold_count")
        if fold_count is None:
            fold_count = len(report.folds)
    if fold_count is None:
        fold_count = aggregate.get("test_periods")
    try:
        test_periods = int(fold_count or 0)
    except (TypeError, ValueError):
        test_periods = 0
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
    if not preview and dsr is not None and not _has_dsr_evidence(aggregate.get("dsr_evidence")):
        failed.append("dsr_evidence")
    if not preview and pbo is not None and not _has_pbo_evidence(aggregate.get("pbo_evidence")):
        failed.append("pbo_evidence")

    max_regime = _config_number(settings, "max_regime_concentration", 0.75)
    concentration = _finite_float(aggregate.get("regime_concentration"))
    if concentration is None:
        failed.append("max_regime_concentration_unavailable")
    elif concentration > max_regime:
        failed.append("max_regime_concentration")

    provenance_ok = aggregate.get("provenance_ok")
    if provenance_ok is not True:
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
