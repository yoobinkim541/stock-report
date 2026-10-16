"""Sweet-spot parameter search using synthetic learnable market data.

Public API
----------
generate_synthetic_market_data(n, seed)   — reproducible synthetic data with learnable signal
evaluate_threshold_strategy(data, params) — threshold strategy backtest (shift(1), no lookahead)
optimize_sweet_spot(data, param_grid)     — grid search for best params
plot_results(result, outdir)              — matplotlib equity/trial charts (optional; skips if no matplotlib)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ml.backtest import (
    BacktestResult,
    buy_and_hold,
    cagr as _calc_cagr,
    max_drawdown as _calc_mdd,
    sharpe_ratio as _calc_sharpe,
)
from ml.optimization import composite_score, grid_search_parameters


_SYNTHETIC_RISK_FREE = 0.0


def _offline_buy_and_hold(close: pd.Series, name: str) -> BacktestResult:
    """Buy-and-hold metrics for synthetic data without a live rate lookup."""
    return buy_and_hold(close, name=name, risk_free=_SYNTHETIC_RISK_FREE)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class SweetSpotResult:
    best_params: dict
    best_result: BacktestResult       # best threshold strategy (grid-searched on full data)
    ml_result: BacktestResult         # actual ML model result (nested OOS)
    overlay_result: BacktestResult    # risk overlay: 200MA trend × ML size
    baseline_result: BacktestResult
    qqq_result: BacktestResult
    spy_result: BacktestResult
    trials: pd.DataFrame
    equity: pd.DataFrame              # columns: ML_model, overlay, threshold, SPY, QQQ
    weights: pd.Series
    wf_summary: dict
    # ML 이 실제 평가된 OOS 구간으로 맞춘 벤치마크 — 전체기간 수치와 나란히 놓으면
    # 기간이 달라 비교가 성립하지 않는다(감사 2026-08-21). 표시용 소비자는 이쪽을 쓸 것.
    qqq_oos_result: BacktestResult | None = None
    spy_oos_result: BacktestResult | None = None


# ---------------------------------------------------------------------------
# Synthetic data generator
# ---------------------------------------------------------------------------

def generate_synthetic_market_data(n: int = 756, seed: int = 42) -> dict:
    """Generate synthetic but learnable market data.

    The hidden signal is an AR(1) process.  Asset returns at day t are driven
    by the signal at day t-1 (no lookahead).  Observable features are noisy
    proxies of the hidden signal, so a threshold strategy trained on them can
    beat random but is not perfect.

    Returns dict with keys:
      close      — pd.Series: strategy asset price
      spy_close  — pd.Series: SPY benchmark price
      qqq_close  — pd.Series: QQQ benchmark price
      features   — pd.DataFrame: momentum, volatility, sentiment
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")

    # Hidden AR(1) signal
    signal = np.empty(n)
    signal[0] = 0.0
    ar_noise = rng.normal(0, 0.5, n)
    for t in range(1, n):
        signal[t] = 0.7 * signal[t - 1] + ar_noise[t]

    # Asset returns driven by lagged signal -> no lookahead in the DGP.
    # The strategy asset has a stronger learnable component than the passive
    # benchmarks, so the optimizer can find a genuine sweet spot without
    # fabricating monotonic positive sample paths.
    signal_lagged = np.concatenate([[0.0], signal[:-1]])
    market_noise = rng.normal(0, 0.008, n)
    asset_rets = 0.0060 * np.tanh(signal_lagged) + 0.0002 + 0.35 * market_noise + rng.normal(0, 0.010, n)
    spy_rets = 0.00025 + 0.0008 * np.tanh(signal_lagged) + market_noise + rng.normal(0, 0.004, n)
    qqq_rets = 0.00035 + 0.0012 * np.tanh(signal_lagged) + 1.15 * market_noise + rng.normal(0, 0.005, n)

    close = pd.Series(100 * np.cumprod(1 + asset_rets), index=idx, name="asset")
    spy_close = pd.Series(100 * np.cumprod(1 + spy_rets), index=idx, name="SPY")
    qqq_close = pd.Series(100 * np.cumprod(1 + qqq_rets), index=idx, name="QQQ")

    # Observable features (all computed without lookahead)
    daily_ret = close.pct_change().fillna(0)
    momentum = close.pct_change(20).fillna(0)
    volatility = daily_ret.rolling(20, min_periods=1).std().fillna(0)
    # Sentiment: noisy observation of the hidden signal
    sentiment = pd.Series(signal + rng.normal(0, 0.3, n), index=idx, name="sentiment")

    features = pd.DataFrame({
        "momentum": momentum,
        "volatility": volatility,
        "sentiment": sentiment,
    })

    return {
        "close": close,
        "spy_close": spy_close,
        "qqq_close": qqq_close,
        "features": features,
    }


# ---------------------------------------------------------------------------
# Strategy evaluator
# ---------------------------------------------------------------------------

def evaluate_threshold_strategy(data: dict, params: dict) -> BacktestResult:
    """Backtest a threshold strategy on the provided data dict.

    Position at end of day t is applied starting day t+1 via shift(1).

    Params (all optional):
      threshold:   float — signal decision boundary (default 0.0)
      max_weight:  float — position when signal > threshold (default 1.0)
      safe_weight: float — position when signal ≤ threshold (default 0.0)
      signal_col:  str   — feature column to use as signal (default "sentiment")
    """
    threshold = params.get("threshold", 0.0)
    max_weight = params.get("max_weight", 1.0)
    safe_weight = params.get("safe_weight", 0.0)
    signal_col = params.get("signal_col", "sentiment")

    close = data["close"]
    if signal_col not in data["features"].columns:
        available = list(data["features"].columns)
        raise ValueError(f"signal_col '{signal_col}' not found. Available: {available}")
    signal = data["features"][signal_col]

    # shift(1): signal observed at close of t → position from open of t+1
    position = signal.shift(1).map(lambda s: max_weight if s > threshold else safe_weight)

    df = pd.concat([close.rename("close"), position.rename("pos")], axis=1).dropna()
    asset_ret = df["close"].pct_change()
    strat_ret = df["pos"] * asset_ret
    equity = (1 + strat_ret.fillna(0)).cumprod() * df["close"].iloc[0]
    turnover = float(df["pos"].diff().abs().mean())

    return BacktestResult(
        name=f"threshold(thr={threshold:.2g},w={max_weight:.2g}/{safe_weight:.2g})",
        cumulative_return=float((1 + strat_ret.fillna(0)).prod() - 1),
        cagr=_calc_cagr(equity),
        max_drawdown=_calc_mdd(equity),
        sharpe=_calc_sharpe(strat_ret.dropna(), risk_free=_SYNTHETIC_RISK_FREE),
        turnover=turnover,
        n_days=len(df),
        extra={"equity": equity},
    )


# ---------------------------------------------------------------------------
# Risk-overlay strategy: trend as base position, ML adjusts size only
# ---------------------------------------------------------------------------

def evaluate_risk_overlay_strategy(data: dict, train_fraction: float = 2 / 3) -> BacktestResult:
    """ML as risk overlay: 200MA trend is base, ML signal adjusts position size.

    Logic (shift(1) applied throughout):
      trend_up  = QQQ > 200MA
      ml_signal = ExcessReturnModel OOS prediction
      ml_pos    = percentile rank of ml_signal (0~1)

      position:
        trend_up  → 0.6 + 0.4 * ml_pos  (0.6 ~ 1.0, always long)
        trend_down → 0.0 + 0.4 * ml_pos  (0.0 ~ 0.4, defensive)

    Rationale: never fully exits in uptrend (participates in bull runs);
    ML modulates size instead of making binary in/out decisions.
    """
    close     = data["close"]
    qqq_close = data.get("qqq_close", close)
    features  = data["features"]
    n = len(close)
    split = int(n * train_fraction)

    # 200MA trend signal on QQQ (shift(1) applied later)
    ma200     = qqq_close.rolling(200, min_periods=60).mean()
    trend_up  = (qqq_close > ma200).astype(float)

    # ML OOS predictions.  This shares the same strictly point-in-time model
    # path as the main strategy, rather than fitting a second adapter with a
    # different fallback behaviour.
    ml_signal = _generate_ml_signal(data, train_fraction=train_fraction)

    # Expanding percentile rank — 각 시점 t 를 t 까지의 분포로만 순위화.
    # 전역 rank(pct=True) 는 t 의 순위가 t 이후 미래 예측분포에 의존하는 look-ahead 였다(감사 확정).
    ml_pos = ml_signal.expanding(min_periods=20).apply(
        lambda s: s.rank(pct=True).iloc[-1], raw=False).fillna(0.5)

    # Position: combine trend + ML size
    trend_s = trend_up.shift(1).fillna(False)   # shift(1): no lookahead
    ml_s    = ml_pos.shift(1).fillna(0.5)
    position = np.where(
        trend_s,
        0.6 + 0.4 * ml_s,   # trend up: 0.6~1.0
        0.0 + 0.4 * ml_s,   # trend down: 0.0~0.4
    )
    position_s = pd.Series(position, index=close.index)

    # Do not report an in-sample return as if it were an ML result.  The
    # pre-split period is cash; only predictions made after training are used.
    position_s.iloc[:split] = 0.0

    df = pd.concat([close.rename("close"), position_s.rename("pos")], axis=1).dropna()
    asset_ret = df["close"].pct_change()
    strat_ret = df["pos"] * asset_ret
    equity    = (1 + strat_ret.fillna(0)).cumprod() * df["close"].iloc[0]

    return BacktestResult(
        name="ML 리스크오버레이 (200MA+ML크기조절)",
        cumulative_return=float((1 + strat_ret.fillna(0)).prod() - 1),
        cagr=_calc_cagr(equity),
        max_drawdown=_calc_mdd(equity),
        sharpe=_calc_sharpe(strat_ret.dropna(), risk_free=_SYNTHETIC_RISK_FREE),
        turnover=float(df["pos"].diff().abs().mean()),
        n_days=len(df),
        extra={"equity": equity},
    )


# ---------------------------------------------------------------------------
# ML model signal: OOS predictions from an optional sklearn model
# ---------------------------------------------------------------------------

def _generate_ml_signal(data: dict, train_fraction: float = 2 / 3) -> pd.Series:
    """Train a regularized forward-return model on the first data segment.

    Returns a Series of model predictions named "ml_signal".  Values are NaN
    for the in-sample period so the strategy defaults to safe_weight during
    those rows — making the backtest strictly OOS from the split point.

    A standardized sklearn Ridge model is used when available.  The fallback
    is the same ridge equation solved with NumPy, so importing/running the
    smoke path does not require sklearn.  Predictions are only emitted for
    rows after the training split.
    """
    features_df = data["features"]
    close = data["close"]
    n = len(features_df)
    split = int(n * float(train_fraction))
    if n < 8 or split < 4 or split >= n:
        return pd.Series(np.full(n, np.nan), index=features_df.index, name="ml_signal")

    # Target at t is return from t to t+1.  The final row has no observable
    # target and is excluded from fitting instead of being filled with zero.
    fwd_return = close.pct_change().shift(-1)
    X = features_df.values.astype(float)
    y = fwd_return.values.astype(float)
    train_mask = np.isfinite(y[:split]) & np.isfinite(X[:split]).all(axis=1)
    if train_mask.sum() < 4:
        return pd.Series(np.full(n, np.nan), index=features_df.index, name="ml_signal")

    x_train = X[:split][train_mask]
    y_train = y[:split][train_mask]
    x_test = np.nan_to_num(X[split:], nan=0.0, posinf=0.0, neginf=0.0)

    try:
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        model = make_pipeline(StandardScaler(), Ridge(alpha=1.0))
        # DataFrame input keeps sklearn feature-name validation quiet while
        # retaining a dependency-free NumPy fallback below.
        model.fit(pd.DataFrame(x_train, columns=features_df.columns), y_train)
        predictions = np.asarray(
            model.predict(pd.DataFrame(x_test, columns=features_df.columns)),
            dtype=float,
        )
        backend = "sklearn_ridge"
    except ImportError:
        # Standardize with training statistics and solve ridge with an
        # unpenalized intercept.  This is intentionally equivalent in shape
        # to the sklearn branch, not a constant-return fallback.
        mean = np.nanmean(x_train, axis=0)
        scale = np.nanstd(x_train, axis=0)
        scale[~np.isfinite(scale) | (scale < 1e-12)] = 1.0
        train_z = (x_train - mean) / scale
        test_z = (x_test - mean) / scale
        train_design = np.column_stack([np.ones(len(train_z)), train_z])
        test_design = np.column_stack([np.ones(len(test_z)), test_z])
        penalty = np.eye(train_design.shape[1])
        penalty[0, 0] = 0.0
        coef = np.linalg.pinv(train_design.T @ train_design + penalty) @ train_design.T @ y_train
        predictions = test_design @ coef
        backend = "numpy_ridge"

    predictions = np.nan_to_num(predictions, nan=0.0, posinf=0.0, neginf=0.0)
    output = np.full(n, np.nan)
    output[split:] = predictions
    signal = pd.Series(output, index=features_df.index, name="ml_signal")
    signal.attrs["model_backend"] = backend
    return signal


# ---------------------------------------------------------------------------
# Portfolio weights derived from optimizer parameters
# ---------------------------------------------------------------------------

def _derive_weights_from_params(best_params: dict) -> pd.Series:
    """Derive portfolio weights from the optimizer's best parameters.

    Maps risk-appetite signals (threshold, max_weight, safe_weight) to
    a concrete ticker weight vector via build_weights() from ml.portfolio.
    This ensures the reported portfolio reflects what the optimizer found,
    rather than hard-coded constants.

    Risk mapping:
      - low threshold  → signal fires more often → higher equity allocation
      - high max_weight → more concentrated bet   → higher growth-name scores
      - high safe_weight → larger cash floor       → higher SGOV allocation
    """
    from ml.portfolio import PortfolioConfig, build_weights

    max_w = float(best_params.get("max_weight", 1.0))
    safe_w = float(best_params.get("safe_weight", 0.0))
    threshold = float(best_params.get("threshold", 0.0))

    # Map threshold from assumed range [-1, 1]: lower → more bullish
    thr_norm = (threshold + 1.0) / 2.0          # 0 = most bullish, 1 = most bearish
    equity_conviction = float(np.clip(min(max_w, 1.0) * (1.0 - thr_norm), 0.0, 1.0))

    # SGOV score: high when bearish or safe_weight is high
    # Stock scores: high when bullish
    sgov_score = float(np.clip((1.0 - equity_conviction) * 0.80 + safe_w * 0.20, 0.05, 1.0))
    scores = pd.Series({
        "SGOV":  sgov_score,
        "QQQI":  equity_conviction * 0.80,
        "NVDA":  equity_conviction * 0.70,
        "QQQ":   equity_conviction * 0.60,
        "MSFT":  equity_conviction * 0.50,
        "GOOGL": equity_conviction * 0.45,
        "ORCL":  equity_conviction * 0.40,
    })

    safe_min = float(np.clip(safe_w * 0.5 + 0.05, 0.05, 0.40))
    # safe_weight_max scales with bearishness so SGOV can absorb more allocation
    safe_max = float(np.clip(safe_min + 0.90 * (1.0 - equity_conviction), safe_min, 0.95))
    cfg = PortfolioConfig(
        safe_weight_min=safe_min,
        safe_weight_max=safe_max,
        qqq_weight=0.0,
        top_n=6,
        max_single_position=0.25,
        allow_cash=False,
    )
    w = build_weights(scores, cfg)
    # Normalise to exactly 1.0 (distribute any residual cash proportionally)
    total = float(w.sum())
    if total > 1e-9:
        w = w / total
    return w


# ---------------------------------------------------------------------------
# Proper walk-forward validation (no leakage)
# ---------------------------------------------------------------------------

def _run_proper_walk_forward(
    data: dict,
    param_grid: dict,
    initial_train: int = 378,
    test_size: int = 189,
) -> dict:
    """Walk-forward validation: optimize on expanding train window, evaluate OOS.

    For each fold:
      1. Find best params by running grid search on the *train* window only.
      2. Apply those fold-specific best params to the held-out *test* window.
    This eliminates the leakage that occurs when full-data best_params are reused
    during WF evaluation.

    With default n=756, initial_train=378, test_size=189 → 2 folds:
      Fold 0: train=[0:378], test=[378:567]
      Fold 1: train=[0:567], test=[567:756]
    """
    n = len(data["close"])
    wf_sharpes: list[float] = []
    wf_cagrs: list[float] = []
    n_folds = 0

    train_end = initial_train
    while train_end + test_size <= n:
        # Slice — preserve index alignment
        train_data = {
            k: (v.iloc[:train_end] if isinstance(v, (pd.Series, pd.DataFrame)) else v)
            for k, v in data.items()
        }
        test_data = {
            k: (v.iloc[train_end:train_end + test_size] if isinstance(v, (pd.Series, pd.DataFrame)) else v)
            for k, v in data.items()
        }

        # Optimize on train fold only — default arg binding captures current loop values
        _qqq_cagr_fold = _offline_buy_and_hold(train_data["qqq_close"], "QQQ").cagr or 0.0

        def _fold_objective(params: dict, _td=train_data, _qc=_qqq_cagr_fold) -> float:
            r = evaluate_threshold_strategy(_td, params)
            return composite_score(
                cagr=r.cagr,
                max_drawdown=r.max_drawdown,
                turnover=r.turnover or 0.0,
                excess_return=(r.cagr or 0.0) - _qc,
            )

        fold_gs = grid_search_parameters(_fold_objective, param_grid)
        fold_best_params = fold_gs["best_params"]

        # True OOS evaluation with fold-specific params
        fr = evaluate_threshold_strategy(test_data, fold_best_params)
        if fr.sharpe is not None:
            wf_sharpes.append(fr.sharpe)
        if fr.cagr is not None:
            wf_cagrs.append(fr.cagr)
        n_folds += 1
        train_end += test_size

    return {
        "n_folds": n_folds,
        "mean_sharpe": float(np.mean(wf_sharpes)) if wf_sharpes else None,
        "std_sharpe": float(np.std(wf_sharpes)) if len(wf_sharpes) > 1 else 0.0,
        "mean_cagr": float(np.mean(wf_cagrs)) if wf_cagrs else None,
    }


# ---------------------------------------------------------------------------
# Sweet-spot optimizer
# ---------------------------------------------------------------------------

def oos_aligned_benchmarks(equity) -> dict:
    """ML 이 평가된 구간과 **동일한 창**으로 계산한 벤치마크 {name: BacktestResult}.

    ML 은 학습분을 뺀 OOS 구간에서만 평가되는데, 벤치마크를 전체 기간 CAGR 로 두면
    서로 다른 기간을 비교하게 된다(실측 2026-08-21: ML 126일 5.20% vs 표시된 QQQ
    756일 26.46%, 그러나 같은 126일 QQQ 는 42.86% — 표시값이 ML 을 실제보다 좋아
    보이게 했다). equity 의 ML_model 유효 구간으로 잘라 재계산한다.
    """
    if equity is None or getattr(equity, "empty", True):
        return {}
    cols = set(getattr(equity, "columns", []))
    if "ML_model" not in cols:
        return {}
    ml_valid = equity["ML_model"].dropna()
    if ml_valid.empty:
        return {}
    window = equity.loc[ml_valid.index[0]:ml_valid.index[-1]]
    out: dict = {}
    for name in ("QQQ", "SPY"):
        if name not in cols:
            continue
        series = window[name].dropna()
        if len(series) < 2:
            continue
        out[name] = _offline_buy_and_hold(series, name=f"{name}(OOS 정합)")
    return out


def _sweet_spot_score(
    result: BacktestResult,
    qqq_cagr: float,
    spy_cagr: float,
) -> float:
    """CAGR − drawdown/turnover penalties + excess vs both benchmarks."""
    excess_vs_benchmarks = 0.5 * (
        (result.cagr or 0.0) - qqq_cagr
        + (result.cagr or 0.0) - spy_cagr
    )
    return composite_score(
        cagr=result.cagr,
        max_drawdown=result.max_drawdown,
        turnover=result.turnover or 0.0,
        excess_return=excess_vs_benchmarks,
    )


def optimize_sweet_spot(
    data: Optional[dict] = None,
    param_grid: Optional[dict] = None,
) -> SweetSpotResult:
    """Grid-search for the best threshold strategy parameters.

    Objective: CAGR − drawdown penalty − turnover penalty plus excess return
    against both QQQ and SPY.

    Returns SweetSpotResult with best params, baselines, trials DataFrame,
    equity curves, and a 2-fold walk-forward summary.
    """
    if data is None:
        data = generate_synthetic_market_data()

    if param_grid is None:
        param_grid = {
            "threshold": [-1.0, -0.5, 0.0, 0.5, 1.0],
            "max_weight": [0.8, 1.0],   # 1.2 removed: leverage creates unfair advantage vs. unleveraged QQQ benchmark
            "safe_weight": [0.0, 0.1],
        }

    qqq_result = _offline_buy_and_hold(data["qqq_close"], "QQQ")
    spy_result = _offline_buy_and_hold(data["spy_close"], "SPY")
    baseline_result = evaluate_threshold_strategy(
        data, {"threshold": 0.0, "max_weight": 1.0, "safe_weight": 0.0}
    )

    qqq_cagr = qqq_result.cagr or 0.0
    spy_cagr = spy_result.cagr or 0.0
    trial_rows: list[dict] = []

    def _objective(params: dict) -> float:
        r = evaluate_threshold_strategy(data, params)
        score = _sweet_spot_score(r, qqq_cagr, spy_cagr)
        trial_rows.append({
            **params,
            "score": score,
            "cagr": r.cagr,
            "max_drawdown": r.max_drawdown,
            "sharpe": r.sharpe,
            "cumulative_return": r.cumulative_return,
        })
        return score

    gs = grid_search_parameters(_objective, param_grid)
    best_params = gs["best_params"]

    # The neutral baseline is always a valid candidate, even when a caller
    # supplies a custom grid that omits it.  Never return a worse “optimum”.
    baseline_params = {"threshold": 0.0, "max_weight": 1.0, "safe_weight": 0.0}
    if not best_params or _sweet_spot_score(baseline_result, qqq_cagr, spy_cagr) > gs["best_value"]:
        best_params = baseline_params

    # Best threshold strategy (grid-searched on full data — in-sample benchmark)
    _best = evaluate_threshold_strategy(data, best_params)
    best_result = BacktestResult(
        name="임계값 전략 (최적화, 전체 기간)",
        cumulative_return=_best.cumulative_return,
        cagr=_best.cagr,
        max_drawdown=_best.max_drawdown,
        sharpe=_best.sharpe,
        turnover=_best.turnover,
        n_days=_best.n_days,
        extra=_best.extra,
    )

    # ML model: nested train(0~2/3) / val(2/3~5/6) / test(5/6~end)
    # threshold는 val 구간에서만 탐색 → test 구간은 1회만 평가 (누수 없음)
    n = len(data["close"])
    train_end = int(n * 2 / 3)
    val_end   = int(n * 5 / 6)

    ml_signal = _generate_ml_signal(data, train_fraction=2/3)
    ml_data   = {**data, "features": data["features"].assign(ml_signal=ml_signal)}

    # threshold 탐색 — validation 구간 예측값 분포에서만
    val_preds = ml_signal.iloc[train_end:val_end].dropna()
    if len(val_preds) > 10:
        ml_thresholds = sorted({
            float(np.percentile(val_preds, p)) for p in (5, 25, 50, 75)
        })
    else:
        ml_thresholds = [0.0]

    best_ml_score  = float("-inf")
    best_ml_params = {"threshold": ml_thresholds[0],
                      "max_weight": best_params.get("max_weight", 1.0),
                      "safe_weight": best_params.get("safe_weight", 0.0),
                      "signal_col": "ml_signal"}

    # Threshold and sizing are selected on validation only.  Percentiles make
    # the search scale-free because model predictions are returns, not prices.
    val_slice = {
        k: (v.iloc[train_end:val_end] if isinstance(v, (pd.Series, pd.DataFrame)) else v)
        for k, v in ml_data.items()
    }
    # The validation objective must use validation-window benchmarks.  Using
    # full-period QQQ/SPY CAGR here would leak the held-out test window into
    # model/threshold selection.
    val_qqq_cagr = _offline_buy_and_hold(val_slice["qqq_close"], "QQQ").cagr or 0.0
    val_spy_cagr = _offline_buy_and_hold(val_slice["spy_close"], "SPY").cagr or 0.0
    ml_sizing = sorted({
        (float(best_params.get("max_weight", 1.0)), float(best_params.get("safe_weight", 0.0))),
        (0.8, 0.0),
        (1.0, 0.0),
        (1.0, 0.1),
    })
    for thr in ml_thresholds:
        for max_weight, safe_weight in ml_sizing:
            _p = {
                "threshold": thr,
                "max_weight": max_weight,
                "safe_weight": safe_weight,
                "signal_col": "ml_signal",
            }
            _r = evaluate_threshold_strategy(val_slice, _p)
            _s = _sweet_spot_score(_r, val_qqq_cagr, val_spy_cagr)
            if _s > best_ml_score:
                best_ml_score  = _s
                best_ml_params = _p

    # 최종 test 구간에서 1회만 평가
    test_slice = {k: (v.iloc[val_end:] if isinstance(v, (pd.Series, pd.DataFrame)) else v)
                  for k, v in ml_data.items()}
    _ml = evaluate_threshold_strategy(test_slice, best_ml_params)
    ml_result = BacktestResult(
        name="ML 전략 (Ridge 모델, nested OOS)",
        cumulative_return=_ml.cumulative_return,
        cagr=_ml.cagr,
        max_drawdown=_ml.max_drawdown,
        sharpe=_ml.sharpe,
        turnover=_ml.turnover,
        n_days=_ml.n_days,
        extra={
            **_ml.extra,
            "model_backend": ml_signal.attrs.get("model_backend", "unknown"),
            "selected_params": dict(best_ml_params),
            "oos_start": str(data["close"].index[val_end]),
        },
    )

    trials = pd.DataFrame(trial_rows)

    # Risk overlay: 200MA trend + ML size adjustment
    overlay_result = evaluate_risk_overlay_strategy(data, train_fraction=2/3)

    # Equity curves
    ml_eq       = ml_result.extra.get("equity", pd.Series(dtype=float))
    overlay_eq  = overlay_result.extra.get("equity", pd.Series(dtype=float))
    threshold_eq = best_result.extra.get("equity", pd.Series(dtype=float))
    spy_eq = (1 + data["spy_close"].pct_change().fillna(0)).cumprod() * 100
    qqq_eq = (1 + data["qqq_close"].pct_change().fillna(0)).cumprod() * 100
    equity_df = pd.DataFrame({
        "ML_model":  ml_eq,
        "overlay":   overlay_eq,
        "threshold": threshold_eq,
        "SPY":       spy_eq,
        "QQQ":       qqq_eq,
    }).dropna()

    # ML OOS 구간에 맞춘 벤치마크 (표시용 — 기간 불일치 비교 방지)
    _aligned = oos_aligned_benchmarks(equity_df)

    # Proper walk-forward: per-fold optimization → true OOS evaluation (no leakage)
    wf_summary = _run_proper_walk_forward(data, param_grid)

    # Portfolio weights derived from optimizer's best parameters (not hard-coded)
    weights = _derive_weights_from_params(best_params)

    return SweetSpotResult(
        best_params=best_params,
        best_result=best_result,
        ml_result=ml_result,
        overlay_result=overlay_result,
        baseline_result=baseline_result,
        qqq_result=qqq_result,
        spy_result=spy_result,
        qqq_oos_result=_aligned.get("QQQ"),
        spy_oos_result=_aligned.get("SPY"),
        trials=trials,
        equity=equity_df,
        weights=weights,
        wf_summary=wf_summary,
    )


# ---------------------------------------------------------------------------
# Optional matplotlib visualization
# ---------------------------------------------------------------------------

def plot_results(result: SweetSpotResult, outdir: str = "/tmp") -> list[str]:
    """Compatibility wrapper for the two focused plotting functions."""
    from ml.visualization import plot_equity_curves, plot_sweet_spot_trials

    written: list[str] = []
    equity_path = plot_equity_curves(result.equity, outdir=outdir)
    if equity_path:
        written.append(equity_path)
    trials_path = plot_sweet_spot_trials(result.trials, result.best_params, outdir=outdir)
    if trials_path:
        written.append(trials_path)
    return written
