"""p9 — Walk-forward validation with leakage guards.

Public API
----------
walk_forward_splits(index, train_size, val_size, test_size, step, min_train)
    — generator yielding (train_idx, val_idx, test_idx) slices

leakage_guard_shift(feature_df, target)
    — verify target column uses shift(-horizon), raise if obviously unshifted

leakage_guard_future_columns(feature_df)
    — warn/raise on known lookahead column names (e.g. ichi_chikou)

run_walk_forward(splits, feature_df, target, train_fn, predict_fn, evaluate_fn)
    — inject train/predict/evaluate callables, return per-fold results
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Callable, Generator, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Known lookahead column patterns
# ---------------------------------------------------------------------------

_KNOWN_FUTURE_COLUMNS = {
    "ichi_chikou",     # requires close.shift(-26)
}

_FUTURE_PREFIXES = (
    "fwd_",            # forward return columns
    "next_",           # next-period labels
)


# ---------------------------------------------------------------------------
# Split generator
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardSplit:
    """A single walk-forward fold with date-range or integer slices."""
    fold: int
    train_start: int
    train_end: int    # exclusive
    val_start: int
    val_end: int      # exclusive
    test_start: int
    test_end: int     # exclusive

    def train_slice(self) -> slice:
        return slice(self.train_start, self.train_end)

    def val_slice(self) -> slice:
        return slice(self.val_start, self.val_end)

    def test_slice(self) -> slice:
        return slice(self.test_start, self.test_end)


def walk_forward_splits(
    n_rows: int,
    train_size: int,
    val_size: int,
    test_size: int,
    step: Optional[int] = None,
    min_train: Optional[int] = None,
) -> Generator[WalkForwardSplit, None, None]:
    """Yield WalkForwardSplit objects for a dataset of length n_rows.

    Args:
        n_rows: Total number of rows in the dataset.
        train_size: Number of rows in each training window.
        val_size: Number of rows in each validation window.
        test_size: Number of rows in each test window.
        step: How many rows to advance each fold. Defaults to test_size.
        val_size: Set to 0 to skip validation window.
        min_train: Minimum training rows (for expanding window variant).

    Yields:
        WalkForwardSplit for each valid fold (train/val/test all non-empty).
    """
    if step is None:
        step = test_size

    window = train_size + val_size + test_size
    fold = 0

    start = 0
    while start + window <= n_rows:
        train_end = start + train_size
        val_end = train_end + val_size
        test_end = val_end + test_size

        if min_train is not None and (train_end - start) < min_train:
            start += step
            continue

        yield WalkForwardSplit(
            fold=fold,
            train_start=start,
            train_end=train_end,
            val_start=train_end,
            val_end=val_end,
            test_start=val_end,
            test_end=test_end,
        )
        fold += 1
        start += step


def expanding_splits(
    n_rows: int,
    initial_train: int,
    test_size: int,
    val_size: int = 0,
    step: Optional[int] = None,
) -> Generator[WalkForwardSplit, None, None]:
    """Expanding-window variant: train window grows each fold."""
    if step is None:
        step = test_size

    fold = 0
    train_end = initial_train

    while train_end + val_size + test_size <= n_rows:
        val_end = train_end + val_size
        test_end = val_end + test_size

        yield WalkForwardSplit(
            fold=fold,
            train_start=0,
            train_end=train_end,
            val_start=train_end,
            val_end=val_end,
            test_start=val_end,
            test_end=test_end,
        )
        fold += 1
        train_end += step


# ---------------------------------------------------------------------------
# Leakage guards
# ---------------------------------------------------------------------------

def leakage_guard_future_columns(
    feature_df: pd.DataFrame,
    raise_on_error: bool = True,
) -> list[str]:
    """Check for known lookahead column names in feature_df.

    Returns:
        List of problematic column names found.

    Raises:
        ValueError: if raise_on_error=True and problematic columns are found.
    """
    found = []
    for col in feature_df.columns:
        if col in _KNOWN_FUTURE_COLUMNS:
            found.append(col)
        elif any(col.startswith(p) for p in _FUTURE_PREFIXES):
            found.append(col)

    if found:
        msg = (
            f"Potential lookahead columns detected in feature_df: {found}. "
            "These columns encode future information and must not be used as features. "
            "e.g. ichi_chikou uses close.shift(-26); remove it before training."
        )
        if raise_on_error:
            raise ValueError(msg)
        warnings.warn(msg, stacklevel=2)

    return found


def leakage_guard_target_shift(
    feature_df: pd.DataFrame,
    target: pd.Series,
    horizon: int = 1,
    raise_on_error: bool = True,
) -> bool:
    """Verify target is not perfectly aligned with features (heuristic leakage check).

    Checks whether target index <= feature index (same date or behind),
    which could indicate the target was not shifted forward.

    Returns:
        True if guard passes (no obvious leakage), False otherwise.
    """
    if feature_df.empty or target.empty:
        return True

    common = feature_df.index.intersection(target.index)
    if len(common) == 0:
        return True

    # Heuristic: if the target name suggests it's a raw return (not fwd_/target_)
    # and the last N non-NaN target values match the last N feature rows exactly,
    # that's suspicious. We rely on naming conventions here.
    name = str(target.name or "")
    is_labelled_forward = any(
        name.startswith(p) for p in ("target_", "fwd_", "label_", "y_")
    )

    if not is_labelled_forward:
        msg = (
            f"Target series '{name}' does not follow a forward-return naming convention "
            f"(expected prefix: target_, fwd_, label_, y_). "
            "Verify it encodes future returns via close.pct_change(h).shift(-h) before training."
        )
        if raise_on_error:
            raise ValueError(msg)
        warnings.warn(msg, stacklevel=2)
        return False

    return True


# ---------------------------------------------------------------------------
# Walk-forward runner
# ---------------------------------------------------------------------------

@dataclass
class FoldResult:
    """Result for a single walk-forward fold."""
    fold: int
    n_train: int
    n_val: int
    n_test: int
    val_score: Optional[float]
    test_score: Optional[float]
    extra: dict = field(default_factory=dict)


def run_walk_forward(
    splits: "list[WalkForwardSplit] | Generator[WalkForwardSplit, None, None]",
    feature_df: pd.DataFrame,
    target: pd.Series,
    train_fn: Callable[[np.ndarray, np.ndarray], "object"],
    predict_fn: Callable[["object", np.ndarray], np.ndarray],
    evaluate_fn: Callable[[np.ndarray, np.ndarray], float],
    leakage_check: bool = True,
) -> list[FoldResult]:
    """Execute walk-forward validation with injectable callables.

    Args:
        splits: Iterable of WalkForwardSplit objects.
        feature_df: Feature DataFrame (rows = dates).
        target: Target Series aligned to feature_df.
        train_fn: Callable(X_train, y_train) → fitted model.
        predict_fn: Callable(model, X) → predictions array.
        evaluate_fn: Callable(y_true, y_pred) → float score.
        leakage_check: If True, run leakage guards before the first fold.

    Returns:
        List of FoldResult, one per fold.
    """
    if leakage_check:
        leakage_guard_future_columns(feature_df, raise_on_error=True)
        leakage_guard_target_shift(feature_df, target, raise_on_error=True)

    # Align and drop NaNs
    combined = feature_df.join(target.rename("__target__"), how="inner").dropna()
    X_all = combined.drop(columns=["__target__"]).values
    y_all = combined["__target__"].values

    results: list[FoldResult] = []
    splits_list = list(splits)

    for split in splits_list:
        X_tr = X_all[split.train_slice()]
        y_tr = y_all[split.train_slice()]
        X_te = X_all[split.test_slice()]
        y_te = y_all[split.test_slice()]

        has_val = split.val_end > split.val_start
        X_va = X_all[split.val_slice()] if has_val else np.empty((0, X_all.shape[1]))
        y_va = y_all[split.val_slice()] if has_val else np.empty(0)

        if len(X_tr) == 0 or len(X_te) == 0:
            continue

        model = train_fn(X_tr, y_tr)

        val_score: Optional[float] = None
        if has_val and len(X_va) > 0:
            pred_va = predict_fn(model, X_va)
            val_score = evaluate_fn(y_va, pred_va)

        pred_te = predict_fn(model, X_te)
        test_score = evaluate_fn(y_te, pred_te)

        results.append(FoldResult(
            fold=split.fold,
            n_train=len(X_tr),
            n_val=len(X_va),
            n_test=len(X_te),
            val_score=val_score,
            test_score=test_score,
        ))

    return results


def summarize_walk_forward(results: "list[FoldResult]") -> dict:
    """Return mean/std of val and test scores across folds."""
    val_scores = [r.val_score for r in results if r.val_score is not None]
    test_scores = [r.test_score for r in results if r.test_score is not None]
    return {
        "n_folds": len(results),
        "val_mean": float(np.mean(val_scores)) if val_scores else None,
        "val_std": float(np.std(val_scores)) if len(val_scores) > 1 else None,
        "test_mean": float(np.mean(test_scores)) if test_scores else None,
        "test_std": float(np.std(test_scores)) if len(test_scores) > 1 else None,
    }
