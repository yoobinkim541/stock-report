#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_bars(ticker: str, market: str, date: str | None = None):
    from providers import intraday_bars

    return intraday_bars.load_bars(ticker, date_utc=date)


def _parse_ts(value: str):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def build_candidate_outcome(candidate: dict, bars, horizon_min: int) -> dict | None:
    if bars is None or getattr(bars, "empty", True):
        return None
    ts = _parse_ts(candidate.get("bar_ts"))
    entry = float(candidate.get("entry_price") or 0.0)
    if ts is None or entry <= 0:
        return None

    future = bars[bars.index >= ts]
    if len(future) <= horizon_min:
        return None
    window = future.iloc[: int(horizon_min) + 1]
    exit_price = float(window["Close"].iloc[-1])
    gross_return = exit_price / entry - 1.0
    estimated_cost = float(candidate.get("estimated_cost") or 0.0)
    net_return_est = (exit_price - entry - estimated_cost) / entry
    mfe = float(window["High"].max()) / entry - 1.0
    mae = float(window["Low"].min()) / entry - 1.0

    return {
        "candidate_id": candidate["id"],
        "horizon_min": int(horizon_min),
        "entry_price": round(entry, 6),
        "exit_price": round(exit_price, 6),
        "gross_return": round(gross_return, 6),
        "net_return_est": round(net_return_est, 6),
        "mfe": round(mfe, 6),
        "mae": round(mae, 6),
        "success": bool(net_return_est > 0),
    }


def run_market(market: str, base_dir: Path | None = None, horizons: tuple[int, ...] = (5, 15, 30)) -> int:
    from ml.intraday_candidate_ledger import CandidateLedger

    ledger = CandidateLedger(market, base_dir=base_dir)
    added = 0
    cache = {}
    for candidate, horizon in ledger.pending(horizons):
        ticker = candidate.get("ticker")
        if not ticker:
            continue
        date = candidate.get("date")
        key = (ticker, str(market).lower(), date)
        if key not in cache:
            cache[key] = _load_bars(ticker, market, date=date)
        outcome = build_candidate_outcome(candidate, cache[key], horizon)
        if outcome is None:
            continue
        ledger.log_outcome(outcome)
        added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="kr,us")
    args = parser.parse_args(argv)
    total = 0
    for market in [m.strip().lower() for m in args.markets.split(",") if m.strip()]:
        if market in {"kr", "us"}:
            total += run_market(market)
    print(f"candidate outcomes added={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
