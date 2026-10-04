"""Compact quality and readiness state for recommendation shadow operation."""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone

from ml.recommendation_contracts import canonical_outcome_key
from reports.recommendation_index import RecommendationIndex
from reports.source_collector import load_source_health


def _duplicate_count(rows: list[dict]) -> int:
    seen = set()
    duplicates = 0
    for row in rows:
        decision_id = str(row.get("base_decision_id") or row.get("decision_id") or "")
        base = decision_id.rsplit(":h", 1)[0] if ":h" in decision_id else decision_id
        horizon = row.get("horizon")
        if horizon is None and ":h" in decision_id:
            horizon = decision_id.rsplit(":h", 1)[1]
        key = canonical_outcome_key(base, horizon or "")
        duplicates += key in seen
        seen.add(key)
    return duplicates


def _invalid_count(rows: list[dict]) -> int:
    return sum(any(isinstance(value, float) and not math.isfinite(value)
                   for value in row.values()) for row in rows)


def _model_health(model, now: datetime) -> dict:
    if model is None:
        return {"stale": True, "reasons": ["model_unavailable"]}
    if isinstance(model, Mapping):
        date = model.get("train_end_date")
        trained = model.get("trained_at")
        try:
            age = (now - datetime.fromisoformat(str(date)).replace(tzinfo=timezone.utc)).days
            trained_age = (now - datetime.fromisoformat(str(trained)).replace(tzinfo=timezone.utc)).days
        except (TypeError, ValueError):
            age = trained_age = None
        stale = age is None or trained_age is None or min(age, trained_age) < 0 or max(age, trained_age) > 90
        return {"train_end_date": date, "model_age_days": age, "stale": stale,
                "reasons": ["training_stale_or_unknown"] if stale else []}
    from ml.ranker import ranker_gate
    result = ranker_gate(model)
    return {**result["freshness"], "gate_passed": result["passed"],
            "gate_reasons": result["reasons"]}


def collect(*, index: RecommendationIndex | None = None, outcomes: list[dict] | None = None,
            model=None, source_health: Mapping | None = None, now: datetime | None = None) -> dict:
    index = index or RecommendationIndex()
    now = now or datetime.now(timezone.utc)
    try:
        status = {"status": "ok", **index.status()}
    except Exception as exc:
        status = {"status": "unavailable", "error": str(exc), "lag_seconds": None,
                  "errors": None, "fallback_count": None}
    health = source_health if source_health is not None else load_source_health(index.cache_dir)
    blocked = sorted(name for name, row in (health or {}).items()
                     if isinstance(row, Mapping) and row.get("availability") in {"blocked", "disabled", "unavailable"})
    rows = list(outcomes or [])
    return {
        "index": status,
        "fallback_count": status.get("fallback_count"),
        "blocked_sources": blocked,
        "canonical_duplicate_count": _duplicate_count(rows) if outcomes is not None else None,
        "invalid_metric_count": _invalid_count(rows) if outcomes is not None else None,
        "model": _model_health(model, now),
    }


def format_report(status: Mapping) -> str:
    index = status.get("index") or {}
    blocked = status.get("blocked_sources") or []
    model = status.get("model") or {}
    return (f"Recommendation index: {index.get('status', 'unavailable')} "
            f"lag={index.get('lag_seconds')}s errors={index.get('errors')} "
            f"fallbacks={status.get('fallback_count')}\n"
            f"Sources blocked: {', '.join(blocked) if blocked else 'none'}\n"
            f"Model stale={model.get('stale')} gate={model.get('gate_passed', False)} "
            f"duplicates={status.get('canonical_duplicate_count')} "
            f"invalid={status.get('invalid_metric_count')}")
