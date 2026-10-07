"""Refresh bounded calibration diagnostics from immutable ledgers only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ml.adaptive.ledger import Ledger
from ml.recommendation_calibration import train_artifacts


def _read_snapshot(paths, max_rows, max_bytes):
    snapshots = []
    remaining = max_bytes
    for path in paths:
        rows = []
        if path.exists():
            with path.open("rb") as stream:
                while True:
                    line = stream.readline(remaining + 1)
                    if not line:
                        break
                    remaining -= len(line)
                    if remaining < 0:
                        raise ValueError("max_bytes exceeded; refusing a partial source snapshot")
                    if not line.strip():
                        continue
                    if len(rows) >= max_rows:
                        raise ValueError("max_rows exceeded; refusing a partial source snapshot")
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("ledger rows must be JSON objects")
                    rows.append(row)
        snapshots.append(rows)
    return snapshots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", default="entry_signals")
    parser.add_argument("--ledger-dir", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--as-of")
    parser.add_argument("--score-range", type=float, nargs=2)
    parser.add_argument("--min-samples", type=int, default=100)
    parser.add_argument("--min-bin-size", type=int, default=20)
    parser.add_argument("--min-holdout-samples", type=int, default=20)
    parser.add_argument("--holdout-fraction", type=float, default=.2)
    parser.add_argument("--max-rows", type=int, default=100_000)
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-scopes", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        if args.max_rows < 1 or args.max_bytes < 1:
            raise ValueError("max_rows and max_bytes must be positive")
        if not args.surface or Path(args.surface).name != args.surface:
            raise ValueError("surface must be a ledger name, not a path")
        ledger = Ledger(args.surface, base_dir=args.ledger_dir)
        decisions, outcomes = _read_snapshot((ledger.decisions_path, ledger.outcomes_path), args.max_rows, args.max_bytes)
        artifacts = train_artifacts(decisions, outcomes, surface=args.surface, as_of=args.as_of,
            score_range=args.score_range, base_dir=args.artifact_dir, min_samples=args.min_samples,
            min_bin_size=args.min_bin_size, min_holdout_samples=args.min_holdout_samples,
            holdout_fraction=args.holdout_fraction, max_rows=args.max_rows, max_scopes=args.max_scopes)
        print(json.dumps({"status": "ok", "n_decisions": len(decisions), "n_outcomes": len(outcomes),
            "reasons": [] if artifacts else ["no_joinable_scopes"],
            "scopes": [{k: artifact[k] for k in ("scope", "counts", "purge_counts", "rejections", "source_rejections", "acceptance")}
                       for artifact in artifacts]}, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
