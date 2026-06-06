"""p6 — Baseline metrics and backtesting.

Provides:
  - buy_and_hold(close): cumulative return, CAGR, max drawdown for a close series.
  - rule_baseline(feature_df, close, ...): a simple threshold rule using a feature column.
  - portfolio_metrics(weights, close): weighted portfolio metrics including turnover.
  - BacktestResult: lightweight named container for results.

All calculations work on pandas Series/DataFrames; no network required.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_RF_CACHE = Path(os.path.expanduser("~/.cache/risk_free_rate.json"))
_RF_FALLBACK = 0.0425   # ~4.25% — 현재 Fed Funds 수준
_RF_PROCESS_CACHE: Optional[float] = None   # 프로세스 내 1회만 FRED 조회


def get_risk_free_rate() -> float:
    """FRED DFF(Fed Funds 실효금리) 조회. 실패 시 4.25% fallback.

    캐시: 프로세스 내 1회, 파일 캐시 24시간 (~/.cache/risk_free_rate.json)
    반환: 연간 소수점 (e.g. 0.0425 → 4.25%)
    """
    global _RF_PROCESS_CACHE
    if _RF_PROCESS_CACHE is not None:
        return _RF_PROCESS_CACHE

    # 파일 캐시 확인
    if _RF_CACHE.exists():
        try:
            data = json.loads(_RF_CACHE.read_text())
            age = datetime.now() - datetime.fromisoformat(data["ts"])
            if age < timedelta(hours=24):
                _RF_PROCESS_CACHE = float(data["rate"])
                return _RF_PROCESS_CACHE
        except Exception:
            pass

    # FRED API (파일 캐시 미스 시에만 호출)
    try:
        import requests
        r = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": "DFF"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        r.raise_for_status()
        import csv, io
        rows = [row for row in csv.DictReader(io.StringIO(r.text))
                if row.get("DFF") and row["DFF"] != "."]
        if rows:
            rate = float(rows[-1]["DFF"]) / 100.0
            _RF_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _RF_CACHE.write_text(json.dumps({"rate": rate, "ts": datetime.now().isoformat()}))
            _RF_PROCESS_CACHE = rate
            return rate
    except Exception:
        pass

    _RF_PROCESS_CACHE = _RF_FALLBACK
    return _RF_FALLBACK


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """Container for backtest performance metrics."""
    name: str
    cumulative_return: float          # e.g. 0.42 → 42 %
    cagr: Optional[float]             # annualised; None if < 1 year
    max_drawdown: float               # e.g. -0.35 → -35 %
    sharpe: Optional[float]           # annualised Sharpe (252 days); None if std=0
    turnover: Optional[float]         # mean daily weight change; None if not applicable
    n_days: int
    extra: dict = field(default_factory=dict)

    def summary(self) -> str:
        cagr_str = f"{self.cagr:.1%}" if self.cagr is not None else "n/a"
        sharpe_str = f"{self.sharpe:.2f}" if self.sharpe is not None else "n/a"
        turn_str = f"{self.turnover:.4f}" if self.turnover is not None else "n/a"
        return (
            f"{self.name}: "
            f"cum={self.cumulative_return:.1%} "
            f"CAGR={cagr_str} "
            f"MDD={self.max_drawdown:.1%} "
            f"Sharpe={sharpe_str} "
            f"turnover={turn_str} "
            f"({self.n_days}d)"
        )


# ---------------------------------------------------------------------------
# Core metric helpers
# ---------------------------------------------------------------------------

def cumulative_return(close: pd.Series) -> float:
    """Total return from first to last valid price."""
    valid = close.dropna()
    if len(valid) < 2:
        return 0.0
    return float(valid.iloc[-1] / valid.iloc[0]) - 1.0


def max_drawdown(close: pd.Series) -> float:
    """Maximum peak-to-trough drawdown (negative number)."""
    valid = close.dropna()
    if len(valid) < 2:
        return 0.0
    running_max = valid.cummax()
    drawdown = (valid - running_max) / running_max
    return float(drawdown.min())


def cagr(close: pd.Series) -> Optional[float]:
    """Compound Annual Growth Rate. Returns None if < 1 year of data."""
    valid = close.dropna()
    if len(valid) < 2:
        return None
    n_days = (valid.index[-1] - valid.index[0]).days
    if n_days < 365:
        return None
    total = float(valid.iloc[-1] / valid.iloc[0])
    years = n_days / 365.25
    return total ** (1.0 / years) - 1.0


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0, periods: int = 252) -> Optional[float]:
    """Annualised Sharpe ratio from daily return series."""
    excess = returns - risk_free / periods
    std = float(excess.std())
    if std == 0 or math.isnan(std):
        return None
    return float(excess.mean() / std * math.sqrt(periods))


def portfolio_turnover(weights: pd.DataFrame) -> float:
    """Mean daily absolute weight change (one-way turnover)."""
    diffs = weights.diff().abs().sum(axis=1)
    return float(diffs.mean())


# ---------------------------------------------------------------------------
# Buy-and-hold benchmark
# ---------------------------------------------------------------------------

def buy_and_hold(close: pd.Series, name: str | None = None) -> BacktestResult:
    """Compute buy-and-hold metrics for a single price series."""
    label = name or str(close.name or "bah")
    valid = close.dropna()
    rets = valid.pct_change().dropna()
    rf = get_risk_free_rate()
    return BacktestResult(
        name=label,
        cumulative_return=cumulative_return(valid),
        cagr=cagr(valid),
        max_drawdown=max_drawdown(valid),
        sharpe=sharpe_ratio(rets, risk_free=rf),
        turnover=None,
        n_days=len(valid),
    )


# ---------------------------------------------------------------------------
# Simple rule / grid baseline
# ---------------------------------------------------------------------------

def rule_baseline(
    feature_df: pd.DataFrame,
    close: pd.Series,
    signal_col: str,
    threshold: float = 0.0,
    long_weight: float = 1.0,
    cash_weight: float = 0.0,
    name: str | None = None,
) -> BacktestResult:
    """Threshold rule: invest *long_weight* when signal > threshold, else *cash_weight*.

    Args:
        feature_df: Feature DataFrame from ml/features.py (date-indexed).
        close: Price series (date-indexed, aligned to feature_df).
        signal_col: Column in feature_df used as the rule signal.
        threshold: Decision boundary. Signal > threshold → long.
        long_weight: Portfolio weight when in long position (default 1.0 = fully invested).
        cash_weight: Portfolio weight when out (default 0.0 = all cash).
        name: Label for the result.

    Returns:
        BacktestResult with strategy performance metrics.

    Note:
        Positions are determined at the *end* of day t and applied starting day t+1
        (shift(1)) to avoid lookahead leakage.
    """
    label = name or f"rule({signal_col}>{threshold:.2g})"

    if signal_col not in feature_df.columns:
        raise ValueError(f"signal_col '{signal_col}' not found in feature_df. Available: {list(feature_df.columns)}")

    signal = feature_df[signal_col]
    # Shift by 1: decision at close of t → position from t+1 open
    position = signal.shift(1).gt(threshold).map({True: long_weight, False: cash_weight})

    # Align close and position
    both = pd.concat([close.rename("close"), position.rename("pos")], axis=1).dropna()
    asset_ret = both["close"].pct_change()
    strat_ret = both["pos"] * asset_ret

    equity = (1 + strat_ret.fillna(0)).cumprod() * both["close"].iloc[0]
    weights_df = pd.DataFrame({"asset": both["pos"]})
    rf = get_risk_free_rate()

    return BacktestResult(
        name=label,
        cumulative_return=float((1 + strat_ret.fillna(0)).prod() - 1),
        cagr=cagr(equity),
        max_drawdown=max_drawdown(equity),
        sharpe=sharpe_ratio(strat_ret.dropna(), risk_free=rf),
        turnover=portfolio_turnover(weights_df),
        n_days=len(both),
        extra={"signal_col": signal_col, "threshold": threshold},
    )


# ---------------------------------------------------------------------------
# Weighted portfolio metrics
# ---------------------------------------------------------------------------

def portfolio_metrics(
    weights: pd.DataFrame,
    close_panel: pd.DataFrame,
    name: str = "portfolio",
) -> BacktestResult:
    """Compute performance for a time-varying weighted portfolio.

    Args:
        weights: Date-indexed DataFrame of ticker → weight (rows sum ~1).
                 Positions applied to *next day* returns (shift(1) internally).
        close_panel: Date-indexed DataFrame of ticker → close price.
        name: Label for the result.

    Returns:
        BacktestResult with cumulative_return, CAGR, MDD, Sharpe, and turnover.
    """
    # Align and shift weights (no lookahead)
    w = weights.shift(1).fillna(0)
    rets = close_panel.pct_change()

    # Match columns
    common = w.columns.intersection(rets.columns)
    w = w[common]
    rets = rets[common]

    portfolio_ret = (w * rets).sum(axis=1)
    equity = (1 + portfolio_ret.fillna(0)).cumprod()

    return BacktestResult(
        name=name,
        cumulative_return=float(equity.iloc[-1] - 1) if len(equity) else 0.0,
        cagr=cagr(equity),
        max_drawdown=max_drawdown(equity),
        sharpe=sharpe_ratio(portfolio_ret.dropna(), risk_free=get_risk_free_rate()),
        turnover=portfolio_turnover(w),
        n_days=len(equity),
    )


# ---------------------------------------------------------------------------
# Convenience: compare multiple strategies
# ---------------------------------------------------------------------------

def compare(results: list[BacktestResult]) -> pd.DataFrame:
    """Return a comparison DataFrame from a list of BacktestResult objects."""
    rows = []
    for r in results:
        rows.append({
            "strategy": r.name,
            "cum_return": r.cumulative_return,
            "cagr": r.cagr,
            "max_drawdown": r.max_drawdown,
            "sharpe": r.sharpe,
            "turnover": r.turnover,
            "n_days": r.n_days,
        })
    return pd.DataFrame(rows).set_index("strategy")
