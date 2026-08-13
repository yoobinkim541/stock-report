"""Bounded best-effort telemetry for chart renderer decisions."""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import safe_io


VERSION = 1
RECENT_LIMIT = 200
RATE_LIMIT_SECONDS = 30.0
_LAST_RECORDED: dict[tuple[str, str, tuple[str, ...], str], float] = {}


def _default_path() -> Path:
    reports = Path(os.getenv("STOCK_REPORT_REPORTS_DIR", "~/reports")).expanduser()
    return reports / "telemetry" / "chart-renderer.json"


def _empty_metrics() -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": None,
        "totals": {"canvas": 0, "plotly": 0},
        "reasons": {},
        "errors": {},
        "prepare_ms_recent": [],
    }


def load_renderer_metrics(path: str | Path | None = None) -> dict[str, Any]:
    """Load a valid metrics snapshot, returning an empty state on corruption."""
    target = Path(path or _default_path()).expanduser()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != VERSION:
            raise ValueError("unsupported metrics payload")
        totals = data.get("totals")
        if not isinstance(totals, dict):
            raise ValueError("missing totals")
        return data
    except Exception:
        return _empty_metrics()


def _increment(mapping: dict[str, int], key: str) -> None:
    mapping[key] = int(mapping.get(key) or 0) + 1


def record_renderer_event(
    *,
    backend: str,
    reasons: tuple[str, ...] | list[str],
    prepare_ms: float,
    error: str | None = None,
    path: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Aggregate one renderer event without allowing telemetry to break charts."""
    target = Path(path or _default_path()).expanduser()
    backend = str(backend or "").lower()
    reason_keys = tuple(str(reason) for reason in reasons if reason)
    error_key = str(error or "")
    event_key = (str(target), backend, reason_keys, error_key)
    now = time.monotonic()
    if not force and now - _LAST_RECORDED.get(event_key, float("-inf")) < RATE_LIMIT_SECONDS:
        return load_renderer_metrics(target)
    _LAST_RECORDED[event_key] = now
    try:
        latency = float(prepare_ms)
        if not math.isfinite(latency) or latency < 0:
            latency = 0.0
        with safe_io.file_write_lock(str(target), timeout=2.0):
            metrics = load_renderer_metrics(target)
            totals = metrics.setdefault("totals", {"canvas": 0, "plotly": 0})
            _increment(totals, backend if backend in {"canvas", "plotly"} else "plotly")
            reason_counts = metrics.setdefault("reasons", {})
            for reason in reason_keys:
                _increment(reason_counts, reason)
            if error_key:
                _increment(metrics.setdefault("errors", {}), error_key)
            recent = list(metrics.get("prepare_ms_recent") or [])
            recent.append(round(latency, 4))
            metrics["prepare_ms_recent"] = recent[-RECENT_LIMIT:]
            metrics["version"] = VERSION
            metrics["updated_at"] = datetime.now(timezone.utc).isoformat()
            safe_io.atomic_write_json(str(target), metrics)
            return metrics
    except Exception:
        return load_renderer_metrics(target)


def renderer_summary(path: str | Path | None = None) -> dict[str, Any]:
    metrics = load_renderer_metrics(path)
    totals = metrics.get("totals") or {}
    canvas = int(totals.get("canvas") or 0)
    plotly = int(totals.get("plotly") or 0)
    total = canvas + plotly
    values = sorted(
        float(value) for value in metrics.get("prepare_ms_recent") or []
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    )
    p95 = values[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None
    return {
        "total": total,
        "canvas_share": canvas / total if total else None,
        "prepare_p95_ms": p95,
        "totals": dict(totals),
        "reasons": dict(metrics.get("reasons") or {}),
        "errors": dict(metrics.get("errors") or {}),
        "updated_at": metrics.get("updated_at"),
    }
