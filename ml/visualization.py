"""p11 — Optional matplotlib visualizations for the ML sweet-spot pipeline.

All matplotlib imports are deferred inside each function so that base-Python
environments without matplotlib can still import this module safely.

Public API
----------
plot_equity_curves(equity_df, outdir, filename)            — save equity_curves.png
plot_sweet_spot_trials(trials, best_params, outdir, filename) — save sweet_spot_trials.png
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import pandas as pd


def plot_equity_curves(
    equity_df: pd.DataFrame,
    outdir: str = "/tmp",
    filename: str = "equity_curves.png",
) -> Optional[str]:
    """Save equity curve plot to *outdir/filename*.

    Each column in *equity_df* becomes a separate line, normalized to 100.
    Returns the absolute file path on success, or None if matplotlib is not
    installed or *equity_df* is empty.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    if equity_df.empty:
        return None

    Path(outdir).mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(13, 6))

    # Style: ML strategy gets a thicker line; benchmarks are thinner
    for col in equity_df.columns:
        series = equity_df[col].dropna()
        if series.empty or series.iloc[0] == 0:
            continue
        norm = series / series.iloc[0] * 100
        lw = 2.5 if "ML" in col else 1.2
        ax.plot(series.index, norm, label=col, linewidth=lw)

    ax.set_title("Equity Curves — ML Strategy vs Benchmarks\n(Normalized, base=100 | 최적화 샘플 / synthetic smoke)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Normalized Value (base=100)")
    ax.legend(fontsize=7, loc="upper left", ncol=2)
    ax.grid(True, alpha=0.3)

    path = os.path.join(outdir, filename)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_sweet_spot_trials(
    trials: pd.DataFrame,
    best_params: dict,
    outdir: str = "/tmp",
    filename: str = "sweet_spot_trials.png",
) -> Optional[str]:
    """Save sweet-spot trial scatter plot to *outdir/filename*.

    X-axis: threshold, Y-axis: composite score.  Color encodes CAGR.
    Returns the absolute file path on success, or None if matplotlib is not
    installed or *trials* is missing required columns.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    if trials.empty or "threshold" not in trials.columns or "score" not in trials.columns:
        return None

    Path(outdir).mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 5))

    color_col = trials["cagr"] if "cagr" in trials.columns else trials["score"]
    sc = ax.scatter(
        trials["threshold"],
        trials["score"],
        c=color_col,
        cmap="RdYlGn",
        alpha=0.85,
        s=90,
        edgecolors="none",
    )
    plt.colorbar(sc, ax=ax, label="CAGR")

    best_thr = best_params.get("threshold", 0)
    ax.axvline(
        best_thr,
        color="steelblue",
        linestyle="--",
        linewidth=1.8,
        label=f"최적 threshold = {best_thr:.3g}",
    )

    ax.set_title("Sweet-Spot Trials — Threshold vs Composite Score\n(최적화 샘플 / synthetic smoke)")
    ax.set_xlabel("Threshold")
    ax.set_ylabel("Composite Score")
    ax.legend()
    ax.grid(True, alpha=0.3)

    path = os.path.join(outdir, filename)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return path
