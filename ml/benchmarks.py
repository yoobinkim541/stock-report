"""Broad synthetic benchmark comparisons for the ML sweet-spot smoke report.

All data is local and deterministic.  The benchmark panel derives proxy ETF
paths from the synthetic QQQ/SPY series plus seeded idiosyncratic noise, so
tests and smoke scripts have no network dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ml.backtest import BacktestResult, buy_and_hold, portfolio_metrics


M7_TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA")


@dataclass
class BenchmarkComparison:
    results: list[BacktestResult]
    equity: pd.DataFrame
    current_portfolio_note: str
    current_portfolio_weights: Optional[pd.Series] = None


def _returns_to_price(returns: pd.Series, name: str) -> pd.Series:
    return (100.0 * (1.0 + returns.fillna(0.0)).cumprod()).rename(name)


def _seed_for_ticker(ticker: str) -> int:
    return sum((i + 1) * ord(ch) for i, ch in enumerate(ticker)) % (2**32)


def _proxy_ticker_returns(ticker: str, qqq_ret: pd.Series, spy_ret: pd.Series) -> pd.Series:
    rng = np.random.default_rng(_seed_for_ticker(ticker))
    beta = 0.75 + (_seed_for_ticker(ticker) % 65) / 100.0
    drift = ((_seed_for_ticker(ticker) % 11) - 4) / 1_000_000
    noise = pd.Series(rng.normal(0, 0.008, len(qqq_ret)), index=qqq_ret.index)
    return drift + beta * qqq_ret.fillna(0) + (1.0 - beta * 0.55) * spy_ret.fillna(0) + noise


def build_benchmark_price_panel(data: dict, extra_tickers: Optional[list[str]] = None) -> pd.DataFrame:
    """Build deterministic proxy prices for benchmark strategies."""
    qqq = data["qqq_close"].rename("QQQ")
    spy = data["spy_close"].rename("SPY")
    qqq_ret = qqq.pct_change().fillna(0)
    spy_ret = spy.pct_change().fillna(0)
    idx = qqq.index
    rng = np.random.default_rng(123)

    panel = pd.DataFrame({"QQQ": qqq, "SPY": spy}, index=idx)
    panel["QLD"] = _returns_to_price(2.0 * qqq_ret - 0.00018, "QLD")
    panel["TQQQ"] = _returns_to_price(3.0 * qqq_ret - 0.00045, "TQQQ")
    panel["SGOV"] = _returns_to_price(pd.Series(0.00018 + rng.normal(0, 0.00005, len(idx)), index=idx), "SGOV")
    panel["SHY"] = _returns_to_price(pd.Series(0.00012 + rng.normal(0, 0.0007, len(idx)), index=idx), "SHY")
    panel["IEF"] = _returns_to_price(0.00010 - 0.15 * spy_ret + pd.Series(rng.normal(0, 0.0025, len(idx)), index=idx), "IEF")
    panel["TLT"] = _returns_to_price(0.00008 - 0.35 * spy_ret + pd.Series(rng.normal(0, 0.0060, len(idx)), index=idx), "TLT")
    panel["GLD"] = _returns_to_price(0.00012 - 0.05 * spy_ret + pd.Series(rng.normal(0, 0.0070, len(idx)), index=idx), "GLD")
    panel["DBC"] = _returns_to_price(0.00010 + 0.10 * spy_ret + pd.Series(rng.normal(0, 0.0080, len(idx)), index=idx), "DBC")
    panel["SCHD"] = _returns_to_price(0.00020 + 0.70 * spy_ret + pd.Series(rng.normal(0, 0.0045, len(idx)), index=idx), "SCHD")

    for ticker in sorted(set(M7_TICKERS).union(extra_tickers or [])):
        if ticker not in panel.columns:
            panel[ticker] = _returns_to_price(_proxy_ticker_returns(ticker, qqq_ret, spy_ret), ticker)

    return panel.dropna(axis=1, how="all")


def _constant_weight_result(panel: pd.DataFrame, weights: dict[str, float], name: str) -> BacktestResult:
    cols = [t for t in weights if t in panel.columns]
    if not cols:
        raise ValueError(f"no benchmark columns available for {name}")
    raw = pd.Series({t: weights[t] for t in cols}, dtype=float)
    raw = raw / raw.sum()
    weight_df = pd.DataFrame([raw] * len(panel), index=panel.index).fillna(0.0)
    result = portfolio_metrics(weight_df, panel[cols], name=name)
    equity = result.extra.get("equity")
    if equity is None:
        rets = panel[cols].pct_change()
        port_ret = (weight_df.shift(1).fillna(0.0) * rets).sum(axis=1)
        result.extra["equity"] = 100.0 * (1.0 + port_ret.fillna(0.0)).cumprod()
    return result


def _mechanical_bull_bear(panel: pd.DataFrame) -> BacktestResult:
    qqq = panel["QQQ"]
    bull = qqq > qqq.rolling(100, min_periods=20).mean()
    weights = pd.DataFrame(0.0, index=panel.index, columns=["QQQ", "QLD", "SGOV", "TLT"])
    weights.loc[bull, ["QQQ", "QLD"]] = [0.55, 0.25]
    weights.loc[bull, ["SGOV", "TLT"]] = [0.15, 0.05]
    weights.loc[~bull, ["QQQ", "QLD"]] = [0.15, 0.00]
    weights.loc[~bull, ["SGOV", "TLT"]] = [0.65, 0.20]
    result = portfolio_metrics(weights, panel[weights.columns], name="기계적 Bull/Bear 리밸런싱")
    rets = panel[weights.columns].pct_change()
    port_ret = (weights.shift(1).fillna(0.0) * rets).sum(axis=1)
    result.extra["equity"] = 100.0 * (1.0 + port_ret.fillna(0.0)).cumprod()
    return result


def load_current_portfolio_weights(path: str | Path = "portfolio_snapshot.json") -> tuple[Optional[pd.Series], str]:
    """Load current portfolio weights from a local snapshot, if available."""
    p = Path(path)
    if not p.exists():
        return None, f"현재 포트폴리오: {p} 없음 - 비교 제외"
    try:
        snapshot = json.loads(p.read_text())
    except Exception as exc:
        return None, f"현재 포트폴리오: {p} 로드 실패 ({exc}) - 비교 제외"

    values: dict[str, float] = {}
    for section in ("overseas_general", "overseas_fractional", "domestic"):
        holdings = snapshot.get(section, {}).get("holdings_usd") or snapshot.get(section, {}).get("holdings") or []
        for holding in holdings:
            ticker = str(holding.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            value = holding.get("value_usd")
            if value is None:
                shares = holding.get("shares", 0) or 0
                price = holding.get("current_price_usd") or holding.get("current_price") or 0
                value = shares * price
            try:
                value_f = float(value)
            except (TypeError, ValueError):
                continue
            if value_f > 0:
                values[ticker] = values.get(ticker, 0.0) + value_f

    if not values:
        return None, f"현재 포트폴리오: {p}에 평가금액 있는 보유종목 없음 - 비교 제외"
    weights = pd.Series(values, dtype=float)
    weights = weights / weights.sum()
    return weights, f"현재 포트폴리오: {p}에서 {len(weights)}개 보유종목 반영"


def _bah_with_equity(price: pd.Series, name: str) -> BacktestResult:
    """buy_and_hold() with normalized equity curve stored in extra["equity"]."""
    result = buy_and_hold(price, name=name)
    valid = price.dropna()
    if len(valid) > 0 and valid.iloc[0] != 0:
        result.extra["equity"] = valid / valid.iloc[0] * 100.0
    return result


def build_benchmark_comparison(
    data: dict,
    ml_result: Optional[BacktestResult] = None,
    current_portfolio_path: str | Path = "portfolio_snapshot.json",
) -> BenchmarkComparison:
    """Compare ML strategy against broad deterministic benchmark strategies."""
    current_weights, note = load_current_portfolio_weights(current_portfolio_path)
    extra = list(current_weights.index) if current_weights is not None else []
    panel = build_benchmark_price_panel(data, extra_tickers=extra)

    results: list[BacktestResult] = [
        _bah_with_equity(panel["QQQ"], name="QQQ 매수보유"),
        _bah_with_equity(panel["SPY"], name="SPY 매수보유"),
        _bah_with_equity(panel["QLD"], name="QLD 매수보유"),
        _bah_with_equity(panel["TQQQ"], name="TQQQ 매수보유"),
        _constant_weight_result(panel, {"QLD": 0.70, "TQQQ": 0.30}, "QLD/TQQQ 바벨"),
        _constant_weight_result(panel, {"QQQ": 0.30, "TLT": 0.40, "IEF": 0.15, "GLD": 0.075, "DBC": 0.075}, "올웨더 포트폴리오"),
        _mechanical_bull_bear(panel),
        _constant_weight_result(panel, {"QQQ": 0.60, "IEF": 0.30, "SGOV": 0.10}, "채권혼합 60/30/10"),
        _constant_weight_result(panel, {"SCHD": 1.00}, "SCHD 배당 스타일"),
        _constant_weight_result(panel, {t: 1.0 for t in M7_TICKERS}, "M7 동일가중 추적"),
    ]

    if current_weights is not None:
        available = current_weights[current_weights.index.isin(panel.columns)]
        missing = sorted(set(current_weights.index) - set(available.index))
        if len(available) > 0:
            result = _constant_weight_result(panel, available.to_dict(), "현재 사용자 포트폴리오")
            result.extra["missing_tickers"] = missing
            results.append(result)
            if missing:
                note += f" (프록시 없음 제외: {', '.join(missing)})"
        else:
            note += " - synthetic proxy와 겹치는 티커 없음"

    equity_cols: dict[str, pd.Series] = {}
    if ml_result is not None and "equity" in ml_result.extra:
        equity_cols["ML 전략"] = ml_result.extra["equity"]
    for result in results:
        if "equity" in result.extra:
            equity_cols[result.name] = result.extra["equity"]
        elif result.name in panel.columns:
            equity_cols[result.name] = panel[result.name]
    equity = pd.DataFrame(equity_cols).dropna(how="all")
    return BenchmarkComparison(results=results, equity=equity, current_portfolio_note=note, current_portfolio_weights=current_weights)
