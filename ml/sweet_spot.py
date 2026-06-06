"""Sweet-spot parameter search using synthetic learnable market data.

Public API
----------
generate_synthetic_market_data(n, seed)   — reproducible synthetic data with learnable signal
evaluate_threshold_strategy(data, params) — threshold strategy backtest (shift(1), no lookahead)
optimize_sweet_spot(data, param_grid)     — grid search for best params
plot_results(result, outdir)              — matplotlib equity/trial charts (optional; skips if no matplotlib)
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class SweetSpotResult:
    best_params: dict
    best_result: BacktestResult
    baseline_result: BacktestResult
    qqq_result: BacktestResult
    spy_result: BacktestResult
    trials: pd.DataFrame
    equity: pd.DataFrame
    weights: pd.Series
    wf_summary: dict


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

    # Asset returns driven by lagged signal → no lookahead in the DGP
    signal_lagged = np.concatenate([[0.0], signal[:-1]])
    asset_rets = 0.003 * np.tanh(signal_lagged) + 0.0002 + rng.normal(0, 0.012, n)
    spy_rets = rng.normal(0.0003, 0.010, n)
    qqq_rets = rng.normal(0.0004, 0.012, n)

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
        sharpe=_calc_sharpe(strat_ret.dropna()),
        turnover=turnover,
        n_days=len(df),
        extra={"equity": equity},
    )


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
        _qqq_cagr_fold = buy_and_hold(train_data["qqq_close"], name="QQQ").cagr or 0.0

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

def optimize_sweet_spot(
    data: Optional[dict] = None,
    param_grid: Optional[dict] = None,
) -> SweetSpotResult:
    """Grid-search for the best threshold strategy parameters.

    Objective: composite_score(CAGR, MDD, turnover, QQQ-excess).

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

    qqq_result = buy_and_hold(data["qqq_close"], name="QQQ")
    spy_result = buy_and_hold(data["spy_close"], name="SPY")
    baseline_result = evaluate_threshold_strategy(
        data, {"threshold": 0.0, "max_weight": 1.0, "safe_weight": 0.0}
    )

    qqq_cagr = qqq_result.cagr or 0.0
    trial_rows: list[dict] = []

    def _objective(params: dict) -> float:
        r = evaluate_threshold_strategy(data, params)
        excess = (r.cagr or 0.0) - qqq_cagr
        score = composite_score(
            cagr=r.cagr,
            max_drawdown=r.max_drawdown,
            turnover=r.turnover or 0.0,
            excess_return=excess,
        )
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

    _best = evaluate_threshold_strategy(data, best_params)
    best_result = BacktestResult(
        name="ML 전략 (최적화 샘플)",
        cumulative_return=_best.cumulative_return,
        cagr=_best.cagr,
        max_drawdown=_best.max_drawdown,
        sharpe=_best.sharpe,
        turnover=_best.turnover,
        n_days=_best.n_days,
        extra=_best.extra,
    )

    trials = pd.DataFrame(trial_rows)

    # Equity curves for all strategies
    best_eq = best_result.extra.get("equity", pd.Series(dtype=float))
    base_eq = baseline_result.extra.get("equity", pd.Series(dtype=float))
    spy_eq = (1 + data["spy_close"].pct_change().fillna(0)).cumprod() * 100
    qqq_eq = (1 + data["qqq_close"].pct_change().fillna(0)).cumprod() * 100
    equity_df = pd.DataFrame({
        "ML_optimized": best_eq,
        "baseline": base_eq,
        "SPY": spy_eq,
        "QQQ": qqq_eq,
    }).dropna()

    # Proper walk-forward: per-fold optimization → true OOS evaluation (no leakage)
    wf_summary = _run_proper_walk_forward(data, param_grid)

    # Portfolio weights derived from optimizer's best parameters (not hard-coded)
    weights = _derive_weights_from_params(best_params)

    return SweetSpotResult(
        best_params=best_params,
        best_result=best_result,
        baseline_result=baseline_result,
        qqq_result=qqq_result,
        spy_result=spy_result,
        trials=trials,
        equity=equity_df,
        weights=weights,
        wf_summary=wf_summary,
    )


# ---------------------------------------------------------------------------
# Optional matplotlib visualization
# ---------------------------------------------------------------------------

def plot_results(result: SweetSpotResult, outdir: str = "/tmp") -> list[str]:
    """Save equity_curves.png and sweet_spot_trials.png to *outdir*.

    Silently skips and returns [] if matplotlib is not installed.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    import os
    written: list[str] = []

    # ── Equity curves ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 5))
    for col in result.equity.columns:
        norm = result.equity[col] / result.equity[col].iloc[0] * 100
        ax.plot(result.equity.index, norm, label=col)
    ax.set_title("Equity Curves — Sweet Spot vs Benchmarks")
    ax.set_xlabel("Date")
    ax.set_ylabel("Normalized Value (base=100)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    path1 = os.path.join(outdir, "equity_curves.png")
    fig.savefig(path1, dpi=100, bbox_inches="tight")
    plt.close(fig)
    written.append(path1)

    # ── Sweet-spot trials scatter ─────────────────────────────────────────────
    t = result.trials
    if not t.empty and "threshold" in t.columns and "score" in t.columns:
        fig, ax = plt.subplots(figsize=(10, 5))
        color_col = t["cagr"] if "cagr" in t.columns else t["score"]
        sc = ax.scatter(t["threshold"], t["score"], c=color_col, cmap="RdYlGn", alpha=0.7, s=60)
        plt.colorbar(sc, ax=ax, label="CAGR")
        best_thr = result.best_params.get("threshold", 0)
        ax.axvline(best_thr, color="blue", linestyle="--", label=f"best thr={best_thr:.2g}")
        ax.set_title("Sweet Spot Trials — Threshold vs Composite Score")
        ax.set_xlabel("Threshold")
        ax.set_ylabel("Composite Score")
        ax.legend()
        ax.grid(True, alpha=0.3)
        path2 = os.path.join(outdir, "sweet_spot_trials.png")
        fig.savefig(path2, dpi=100, bbox_inches="tight")
        plt.close(fig)
        written.append(path2)

    return written
