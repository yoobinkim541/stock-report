"""Bounded incremental refresh for the recommendation metadata index."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reports.recommendation_index import RecommendationIndex


def _pending_bytes(index: RecommendationIndex) -> int:
    with index._connect(readonly=True) as connection:
        offsets = {
            row[0]: row[1] for row in connection.execute(
                "SELECT raw_path, offset FROM ingest_checkpoint"
            )
        }
    total = 0
    for path in index._event_files(None):
        try:
            total += max(0, path.stat().st_size - offsets.get(str(path.resolve()), 0))
        except OSError:
            continue
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--max-rows", type=int, default=10_000)
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    args = parser.parse_args(argv)
    index = RecommendationIndex(db_path=args.db_path, cache_dir=args.cache_dir)
    try:
        result = index.ingest(max_rows=args.max_rows, max_bytes=args.max_bytes)
        result["pending_bytes"] = _pending_bytes(index)
        result["status"] = "ok"
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BlockingIOError:
        print(json.dumps({"status": "locked", "indexed": 0}))
        return 0
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
