#!/usr/bin/env python3
"""Smoke script: run sweet-spot optimizer, print full report, save graph images.

Requires scikit-learn and matplotlib (via uv extras or pip):

    uv run --with scikit-learn --with matplotlib \\
        python scripts/ml_sweet_spot_smoke.py --output-dir /tmp/stock-report-ml-smoke

All data is synthetic and local; no network calls are made.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from project root or from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ML sweet-spot smoke: optimizer + benchmarks + graphs"
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/stock-report-ml-smoke",
        help="Directory for equity_curves.png and sweet_spot_trials.png",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for synthetic data (default: 42)",
    )
    args = parser.parse_args()

    outdir = args.output_dir
    Path(outdir).mkdir(parents=True, exist_ok=True)

    from ml.sweet_spot import generate_synthetic_market_data, optimize_sweet_spot
    from ml.benchmarks import build_benchmark_comparison
    from ml.reporting import (
        build_ml_strategy_report,
        build_benchmark_report_section,
        chunk_text,
    )
    from ml.visualization import plot_equity_curves, plot_sweet_spot_trials

    print("=" * 64)
    print("ML Sweet-Spot Smoke  (최적화 샘플 / synthetic smoke)")
    print("=" * 64)

    print("\n[1/4] Generating synthetic market data (seed={})...".format(args.seed))
    data = generate_synthetic_market_data(seed=args.seed)
    print(f"      {len(data['close'])} trading days, "
          f"{data['close'].index[0].date()} – {data['close'].index[-1].date()}")

    print("\n[2/4] Running sweet-spot optimizer...")
    result = optimize_sweet_spot(data)
    print(f"      best_params = {result.best_params}")
    print(f"      best CAGR   = {result.best_result.cagr:.1%}" if result.best_result.cagr else "      best CAGR = n/a")
    print(f"      ML CAGR     = {result.ml_result.cagr:.1%}" if result.ml_result.cagr else "      ML CAGR = n/a")
    print(f"      QQQ CAGR    = {result.qqq_result.cagr:.1%}" if result.qqq_result.cagr else "      QQQ CAGR = n/a")

    print("\n[3/4] Building benchmark comparison...")
    bench = build_benchmark_comparison(data, ml_result=result.ml_result)
    print(f"      {len(bench.results)} benchmark strategies")
    print(f"      portfolio note: {bench.current_portfolio_note}")

    print("\n[4/4] Saving graphs to {}...".format(outdir))

    # Combine ML + all benchmark equity curves for one chart
    all_equity = result.equity.copy()
    for b in bench.results:
        if "equity" in b.extra and b.name not in all_equity.columns:
            all_equity[b.name] = b.extra["equity"]

    saved: list[str] = []

    p1 = plot_equity_curves(all_equity, outdir=outdir, filename="equity_curves.png")
    if p1:
        size = Path(p1).stat().st_size
        print(f"      equity_curves.png      → {p1}  ({size:,} bytes)")
        saved.append(p1)
    else:
        print("      matplotlib not available — equity_curves.png skipped")

    p2 = plot_sweet_spot_trials(
        result.trials, result.best_params, outdir=outdir, filename="sweet_spot_trials.png"
    )
    if p2:
        size = Path(p2).stat().st_size
        print(f"      sweet_spot_trials.png  → {p2}  ({size:,} bytes)")
        saved.append(p2)
    else:
        print("      matplotlib not available — sweet_spot_trials.png skipped")

    # ── Full text report ──────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("REPORT OUTPUT")
    print("=" * 64)

    main_report = build_ml_strategy_report(
        ml_result=result.ml_result,
        qqq_result=result.qqq_result,
        spy_result=result.spy_result,
        weights=result.weights,
        wf_summary=result.wf_summary,
        as_of="샘플 (최적화 샘플 / synthetic smoke)",
    )
    bench_section = build_benchmark_report_section(bench)
    full_report = main_report + bench_section

    for chunk in chunk_text(full_report):
        print(chunk)

    # ── Walk-forward summary ──────────────────────────────────────────────
    print("\n[ Walk-Forward 요약 ]")
    wf = result.wf_summary
    print(f"  folds         = {wf.get('n_folds')}")
    ms = wf.get("mean_sharpe")
    ss = wf.get("std_sharpe")
    mc = wf.get("mean_cagr")
    print(f"  mean Sharpe   = {ms:.3f}" if ms is not None else "  mean Sharpe = n/a")
    print(f"  std  Sharpe   = {ss:.3f}" if ss is not None else "  std  Sharpe = n/a")
    print(f"  mean CAGR     = {mc:.1%}" if mc is not None else "  mean CAGR  = n/a")

    # ── Trials table ─────────────────────────────────────────────────────
    print("\n[ Optimizer Trials ]")
    if not result.trials.empty:
        print(result.trials.to_string(max_rows=30, float_format="{:.4f}".format))

    print("\n" + "=" * 64)
    print(f"Graphs written: {len(saved)}/{2}")
    for p in saved:
        print(f"  {p}")
    print("=" * 64)


if __name__ == "__main__":
    main()
