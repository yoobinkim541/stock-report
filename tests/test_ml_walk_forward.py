import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.walk_forward import (
    FoldResult,
    WalkForwardSplit,
    expanding_splits,
    leakage_guard_future_columns,
    leakage_guard_target_shift,
    run_walk_forward,
    summarize_walk_forward,
    walk_forward_splits,
)


# ---------------------------------------------------------------------------
# walk_forward_splits
# ---------------------------------------------------------------------------

def test_walk_forward_splits_basic():
    splits = list(walk_forward_splits(n_rows=100, train_size=60, val_size=10, test_size=10))
    assert len(splits) >= 1
    first = splits[0]
    assert first.train_start == 0
    assert first.train_end == 60
    assert first.val_start == 60
    assert first.val_end == 70
    assert first.test_start == 70
    assert first.test_end == 80


def test_walk_forward_splits_non_overlapping_test():
    splits = list(walk_forward_splits(n_rows=100, train_size=60, val_size=0, test_size=10, step=10))
    for i, s in enumerate(splits[:-1]):
        assert s.test_end <= splits[i + 1].test_start


def test_walk_forward_splits_no_val():
    splits = list(walk_forward_splits(n_rows=50, train_size=30, val_size=0, test_size=10))
    for s in splits:
        assert s.val_start == s.val_end  # empty val


def test_walk_forward_splits_fold_indices_monotone():
    splits = list(walk_forward_splits(n_rows=100, train_size=40, val_size=10, test_size=10, step=5))
    assert [s.fold for s in splits] == list(range(len(splits)))


def test_walk_forward_splits_exhausts_data():
    splits = list(walk_forward_splits(n_rows=20, train_size=10, val_size=5, test_size=5))
    assert len(splits) == 1
    assert splits[0].test_end == 20


def test_walk_forward_splits_none_when_too_small():
    splits = list(walk_forward_splits(n_rows=10, train_size=8, val_size=5, test_size=3))
    assert len(splits) == 0


# ---------------------------------------------------------------------------
# expanding_splits
# ---------------------------------------------------------------------------

def test_expanding_splits_train_grows():
    splits = list(expanding_splits(n_rows=50, initial_train=20, test_size=5))
    assert all(s.train_start == 0 for s in splits)
    train_sizes = [s.train_end for s in splits]
    assert train_sizes == sorted(train_sizes)
    assert train_sizes[0] == 20


# ---------------------------------------------------------------------------
# Leakage guards
# ---------------------------------------------------------------------------

def test_leakage_guard_detects_ichi_chikou():
    df = pd.DataFrame({
        "rsi_14": [1.0, 2.0],
        "ichi_chikou": [0.5, 0.6],  # lookahead!
    })
    with pytest.raises(ValueError, match="ichi_chikou"):
        leakage_guard_future_columns(df, raise_on_error=True)


def test_leakage_guard_detects_fwd_prefix():
    df = pd.DataFrame({"rsi_14": [1.0], "fwd_return": [0.01]})
    with pytest.raises(ValueError, match="fwd_return"):
        leakage_guard_future_columns(df, raise_on_error=True)


def test_leakage_guard_warn_not_raise():
    df = pd.DataFrame({"ichi_chikou": [0.1, 0.2]})
    with pytest.warns(UserWarning):
        found = leakage_guard_future_columns(df, raise_on_error=False)
    assert "ichi_chikou" in found


def test_leakage_guard_clean_features_pass():
    df = pd.DataFrame({"rsi_14": [1.0], "mom_5d": [0.02]})
    found = leakage_guard_future_columns(df, raise_on_error=True)
    assert found == []


def test_leakage_guard_target_shift_valid_name():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    features = pd.DataFrame({"rsi_14": range(10)}, index=idx)
    target = pd.Series(range(10), index=idx, name="target_cls_h1")
    # Should not raise with a correctly-named forward target
    ok = leakage_guard_target_shift(features, target, raise_on_error=True)
    assert ok is True


def test_leakage_guard_target_shift_bad_name():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    features = pd.DataFrame({"rsi_14": range(10)}, index=idx)
    target = pd.Series(range(10), index=idx, name="close")  # not a labelled forward target
    with pytest.raises(ValueError, match="naming convention"):
        leakage_guard_target_shift(features, target, raise_on_error=True)


# ---------------------------------------------------------------------------
# run_walk_forward
# ---------------------------------------------------------------------------

def _make_synthetic(n=80):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    features = pd.DataFrame(
        {"f1": np.linspace(0, 1, n), "f2": np.linspace(1, 0, n)},
        index=idx,
    )
    target = pd.Series(
        np.where(np.linspace(0, 1, n) > 0.5, 1.0, 0.0),
        index=idx,
        name="target_cls_h1",
    )
    return features, target


def test_run_walk_forward_basic():
    features, target = _make_synthetic()
    splits = list(walk_forward_splits(n_rows=80, train_size=40, val_size=10, test_size=10))

    def train_fn(X, y):
        from ml.models import MarketRiskModel
        m = MarketRiskModel(task="regression")
        m.fit(X, y)
        return m

    def predict_fn(model, X):
        return model.predict(X)

    def evaluate_fn(y_true, y_pred):
        return float(np.mean((y_true - y_pred) ** 2))

    results = run_walk_forward(splits, features, target, train_fn, predict_fn, evaluate_fn)
    assert len(results) > 0
    for r in results:
        assert isinstance(r, FoldResult)
        assert r.test_score is not None


def test_run_walk_forward_with_leakage_check_raises():
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    features = pd.DataFrame({"ichi_chikou": np.ones(80)}, index=idx)
    target = pd.Series(np.ones(80), index=idx, name="target_cls_h1")
    splits = list(walk_forward_splits(n_rows=80, train_size=40, val_size=0, test_size=10))

    with pytest.raises(ValueError, match="ichi_chikou"):
        run_walk_forward(
            splits, features, target,
            train_fn=lambda X, y: None,
            predict_fn=lambda m, X: np.zeros(len(X)),
            evaluate_fn=lambda yt, yp: 0.0,
            leakage_check=True,
        )


def test_run_walk_forward_no_leakage_check():
    """run_walk_forward with leakage_check=False should not raise on bad columns."""
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    # Use a name that would fail shift check but pass column name check
    features = pd.DataFrame({"f1": np.linspace(0, 1, 80)}, index=idx)
    target = pd.Series(np.ones(80), index=idx, name="target_cls_h1")
    splits = list(walk_forward_splits(n_rows=80, train_size=40, val_size=0, test_size=10))

    results = run_walk_forward(
        splits, features, target,
        train_fn=lambda X, y: None,
        predict_fn=lambda m, X: np.zeros(len(X)),
        evaluate_fn=lambda yt, yp: float(np.mean((yt - yp) ** 2)),
        leakage_check=False,
    )
    assert len(results) > 0


# ---------------------------------------------------------------------------
# summarize_walk_forward
# ---------------------------------------------------------------------------

def test_summarize_walk_forward():
    results = [
        FoldResult(fold=0, n_train=40, n_val=0, n_test=10, val_score=None, test_score=0.1),
        FoldResult(fold=1, n_train=40, n_val=0, n_test=10, val_score=None, test_score=0.3),
    ]
    summary = summarize_walk_forward(results)
    assert summary["n_folds"] == 2
    assert summary["test_mean"] == pytest.approx(0.2)
    assert summary["val_mean"] is None
