from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import safe_io

RUNS_FILE = "source_runs.jsonl"


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_source_run(cache_dir: Path | str, run: dict) -> dict:
    path = Path(cache_dir) / RUNS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(run or {})
    started = _parse_time(row.get("started_at"))
    finished = _parse_time(row.get("finished_at"))
    if started and finished:
        measured = max(0, int(row.get("duration_ms") or 0))
        wall_clock = max(0, round((finished - started).total_seconds() * 1000))
        row["duration_ms"] = max(measured, wall_clock)
    else:
        row["duration_ms"] = max(0, int(row.get("duration_ms") or 0))
    row.setdefault("fetched", 0)
    row.setdefault("persisted", 0)
    row.setdefault("transport", "direct")
    row.setdefault("availability", "available" if not row.get("error") else "error")
    with safe_io.file_write_lock(str(path)):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def load_source_runs(
    cache_dir: Path | str,
    *,
    hours: int = 24,
    now: datetime | None = None,
) -> list[dict]:
    path = Path(cache_dir) / RUNS_FILE
    if not path.exists():
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=max(1, int(hours or 24)))
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        finished = _parse_time(row.get("finished_at") or row.get("started_at"))
        if isinstance(row, dict) and finished and finished >= cutoff:
            rows.append(row)
    rows.sort(key=lambda row: str(row.get("finished_at") or row.get("started_at") or ""))
    return rows
