import numpy as np
import pandas as pd

from ml import ranker as rk


class _FakeRegressor:
    feature_importances_ = np.array([3.0, 1.0])

    def fit(self, X, y, **kwargs):
        self.mean_ = float(np.mean(y))
        return self

    def predict(self, X):
        arr = np.asarray(X, dtype=float)
        return arr[:, 0]


def _dataset(n_days: int = 90, n_tickers: int = 8) -> dict:
    dates = pd.bdate_range("2025-01-01", periods=n_days)
    tickers = [f"T{i:02d}" for i in range(n_tickers)]
    index = pd.MultiIndex.from_product([dates, tickers], names=["date", "ticker"])
    day_signal = np.repeat(np.linspace(-1.0, 1.0, n_days), n_tickers)
    ticker_signal = np.tile(np.linspace(-0.2, 0.2, n_tickers), n_days)
    f0 = day_signal + ticker_signal
    f1 = np.sin(np.arange(len(index)) / 7.0)
    features = pd.DataFrame({"f0": f0, "f1": f1}, index=index)
    excess = pd.Series(0.03 * f0 + 0.002 * f1, index=index, name="excess")
    return {"features": features, "excess": excess, "meta": {"forward_days": 5}}


def test_train_ranker_records_backend_metadata(monkeypatch):
    monkeypatch.setattr(rk.gbdt, "make_regressor", lambda **kwargs: _FakeRegressor())

    result = rk.train_ranker(_dataset(), use_ranker=False, backend="lightgbm")

    assert result.meta["backend"] == "lightgbm"
    assert result.meta["backend_label"] == "LightGBM"
    assert result.meta["model_type"] == "_FakeRegressor"
    assert result.meta["use_ranker"] is False
    assert list(result.feature_importance.index) == ["f0", "f1"]
    assert result.oos_ic > 0


def test_walk_forward_backtest_accepts_backend(monkeypatch):
    monkeypatch.setattr(rk.gbdt, "make_regressor", lambda **kwargs: _FakeRegressor())

    out = rk.walk_forward_backtest(_dataset(n_days=360), n_folds=3, min_train_months=3, backend="lightgbm", use_ranker=False)

    assert out["backend"] == "lightgbm"
    assert out["n_folds"] > 0
    assert out["mean_ic"] is not None


def test_evaluate_ranker_backend_handles_unavailable(monkeypatch):
    monkeypatch.setattr(rk.gbdt, "backend_available", lambda backend: False)

    out = rk.evaluate_ranker_backend(_dataset(), backend="xgboost")

    assert out["backend"] == "xgboost"
    assert out["available"] is False
    assert "미설치" in out["error"]


def test_walk_forward_uses_ranker_and_reports_purge(monkeypatch):
    fits = []

    def fit(model, X, labels, groups, names, backend):
        fits.append((len(X), len(labels), sum(groups), backend))

    monkeypatch.setattr(rk.gbdt, "make_ranker", lambda **kwargs: _FakeRegressor())
    monkeypatch.setattr(rk.gbdt, "fit_ranker_model", fit)
    out = rk.walk_forward_backtest(_dataset(n_days=360, n_tickers=16), n_folds=3, min_train_months=3)
    assert len(fits) == out["n_folds"] == 3
    assert all(rows == labels == groups for rows, labels, groups, _ in fits)
    assert out["use_ranker"] is True
    assert out["purged"] is True and out["embargo"] == 5
    assert out["top_decile_excess"] == np.mean(out["fold_top10_rets"])

    unpurged = rk.walk_forward_backtest(
        _dataset(n_days=360), n_folds=3, min_train_months=3, embargo=0,
    )
    assert unpurged["purged"] is False


def test_compare_ranker_backends_marks_better_challenger(monkeypatch):
    def fake_eval(dataset, *, backend, **kwargs):
        score = 0.03 if backend == "lightgbm" else 0.06
        return {
            "backend": backend,
            "backend_label": rk.gbdt.backend_label(backend),
            "available": True,
            "oos_ic": score,
            "wf_mean_ic": score,
        }

    monkeypatch.setattr(rk, "evaluate_ranker_backend", fake_eval)

    out = rk.compare_ranker_backends(_dataset(), n_folds=2)

    assert out["best_backend"] == "xgboost"
    assert out["champion_backend"] == "lightgbm"
    assert out["adopt_candidate"] is False  # IC alone is not validation evidence.
