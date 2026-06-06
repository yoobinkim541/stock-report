"""p10 — Portfolio construction from model scores.

Constructs a weight vector from per-ticker scores with constraints:
  - Cash/safe bucket: SGOV / SHY / IEF / TLT proxies
  - QQQ core: optional fixed allocation
  - Top-N stock allocation: proportional to model scores
  - Max single position, min/max safe weight, rebalance threshold

Public API
----------
PortfolioConfig          — constraint dataclass
build_weights(scores, config)   — return Series of weights summing to ~1
rebalance_needed(current, target, threshold)  — True if turnover > threshold
portfolio_turnover(...)  — re-exported from ml.backtest for convenience
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from ml.backtest import portfolio_turnover  # re-export


# ---------------------------------------------------------------------------
# Safe-bucket proxy tickers (ordered by duration: shortest → longest)
# ---------------------------------------------------------------------------

SAFE_TICKERS = ("SGOV", "SHY", "IEF", "TLT")
CORE_TICKERS = ("QQQ",)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class PortfolioConfig:
    """Constraint parameters for portfolio construction."""

    # Safe / cash bucket
    safe_weight_min: float = 0.05      # always keep at least 5% in safe assets
    safe_weight_max: float = 0.60      # never more than 60% in safe assets
    safe_tickers: tuple[str, ...] = SAFE_TICKERS

    # QQQ core allocation (0 = skip)
    qqq_weight: float = 0.0
    core_tickers: tuple[str, ...] = CORE_TICKERS

    # Stock selection
    top_n: int = 10                    # number of top-scoring stocks to hold
    max_single_position: float = 0.20  # max weight for any single stock

    # Rebalancing
    rebalance_threshold: float = 0.05  # min turnover to trigger rebalance

    # Total weight normalisation
    allow_cash: bool = True            # if True, residual weight stays in cash (not redistributed)

    def validate(self) -> None:
        if not 0 <= self.safe_weight_min <= self.safe_weight_max <= 1:
            raise ValueError("safe_weight_min <= safe_weight_max must hold in [0,1]")
        if not 0 <= self.qqq_weight <= 1:
            raise ValueError("qqq_weight must be in [0,1]")
        if not 0 < self.max_single_position <= 1:
            raise ValueError("max_single_position must be in (0,1]")
        if not 1 <= self.top_n:
            raise ValueError("top_n must be >= 1")


# ---------------------------------------------------------------------------
# Core weight builder
# ---------------------------------------------------------------------------

def build_weights(
    scores: pd.Series,
    config: Optional[PortfolioConfig] = None,
    safe_available: Optional[Sequence[str]] = None,
) -> pd.Series:
    """Convert per-ticker scores into a portfolio weight vector.

    Args:
        scores: Series mapping ticker → float score (higher = more attractive).
                May include safe-bucket and core tickers; they are handled
                according to config. Tickers not in scores receive weight 0.
        config: PortfolioConfig. Defaults to PortfolioConfig().
        safe_available: Subset of SAFE_TICKERS that are available in scores.
                        If None, inferred from scores.index.

    Returns:
        pd.Series of weights indexed by ticker, summing to ≤ 1.0 (may be < 1
        if allow_cash=True and residual is left as cash).
    """
    cfg = config if config is not None else PortfolioConfig()
    cfg.validate()

    weights: dict[str, float] = {}
    allocated = 0.0

    # --- 1. QQQ core ---
    if cfg.qqq_weight > 0:
        for t in cfg.core_tickers:
            if t in scores.index:
                weights[t] = cfg.qqq_weight
                allocated += cfg.qqq_weight
                break  # only first core ticker

    # --- 2. Safe bucket ---
    # Use first available safe ticker (prefer shorter duration = SGOV)
    safe_pool = safe_available if safe_available is not None else [
        t for t in cfg.safe_tickers if t in scores.index
    ]
    if safe_pool:
        safe_ticker = safe_pool[0]
        # Score-based safe weight within [min, max]
        safe_score = float(scores.get(safe_ticker, 0.0))
        # Normalise safe score to drive allocation within allowed range
        safe_w = cfg.safe_weight_min + (cfg.safe_weight_max - cfg.safe_weight_min) * max(0.0, min(1.0, safe_score))
        safe_w = min(safe_w, 1.0 - allocated)
        if safe_w > 0:
            weights[safe_ticker] = safe_w
            allocated += safe_w

    # --- 3. Remaining tickers: top-N by score ---
    remaining_budget = max(0.0, 1.0 - allocated)
    exclude = set(weights.keys()) | set(cfg.safe_tickers) | set(cfg.core_tickers)
    stock_scores = scores.drop(labels=[t for t in exclude if t in scores.index], errors="ignore")

    if len(stock_scores) > 0 and remaining_budget > 0:
        # Select top-N
        top = stock_scores.nlargest(cfg.top_n)
        # Convert scores to weights proportional to score magnitude
        raw = top.clip(lower=0)
        total = raw.sum()
        if total > 0:
            prop = (raw / total * remaining_budget).clip(upper=cfg.max_single_position)
            # Re-normalise after clipping
            clipped_total = prop.sum()
            if clipped_total > 0:
                prop = prop / clipped_total * remaining_budget
                prop = prop.clip(upper=cfg.max_single_position)
            for ticker, w in prop.items():
                if w > 0:
                    weights[ticker] = float(w)
            allocated += prop.sum()
        else:
            # Scores are all ≤ 0 — equal-weight top N
            w_each = min(remaining_budget / len(top), cfg.max_single_position)
            for ticker in top.index:
                weights[ticker] = w_each
            allocated += w_each * len(top)

    return pd.Series(weights, dtype=float).fillna(0.0)


# ---------------------------------------------------------------------------
# Rebalancing trigger
# ---------------------------------------------------------------------------

def rebalance_needed(
    current: pd.Series,
    target: pd.Series,
    threshold: Optional[float] = None,
    config: Optional[PortfolioConfig] = None,
) -> bool:
    """Return True if the total absolute weight deviation exceeds the threshold.

    Args:
        current: Current portfolio weights (may have different tickers than target).
        target: Desired portfolio weights.
        threshold: Override; if None, uses config.rebalance_threshold or 0.05.
        config: PortfolioConfig for default threshold.
    """
    th = threshold
    if th is None:
        th = config.rebalance_threshold if config is not None else 0.05

    all_tickers = current.index.union(target.index)
    cur = current.reindex(all_tickers).fillna(0.0)
    tgt = target.reindex(all_tickers).fillna(0.0)
    turnover = float((cur - tgt).abs().sum())
    return turnover > th


# ---------------------------------------------------------------------------
# Weight validation
# ---------------------------------------------------------------------------

def validate_weights(weights: pd.Series, tol: float = 1e-6) -> None:
    """Raise if weights contain negatives or sum > 1 + tol."""
    if (weights < -tol).any():
        raise ValueError(f"Negative weights detected: {weights[weights < 0].to_dict()}")
    total = float(weights.sum())
    if total > 1.0 + tol:
        raise ValueError(f"Weights sum to {total:.6f}, which exceeds 1.0 + tol={tol}.")


# ---------------------------------------------------------------------------
# Multi-period weight matrix builder
# ---------------------------------------------------------------------------

def build_weight_matrix(
    score_panel: pd.DataFrame,
    config: Optional[PortfolioConfig] = None,
    safe_available: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Build a time-varying weight matrix from a panel of daily scores.

    Args:
        score_panel: Date-indexed DataFrame of ticker → daily score.
                     Positions at row t are derived from scores at row t and
                     applied to returns at t+1 (caller's responsibility to shift).
        config: PortfolioConfig.
        safe_available: Fixed list of available safe tickers.

    Returns:
        Date-indexed DataFrame of ticker → weight (same index as score_panel).
    """
    rows = {}
    for date, row in score_panel.iterrows():
        w = build_weights(row.dropna(), config=config, safe_available=safe_available)
        rows[date] = w
    return pd.DataFrame(rows).T.fillna(0.0)
