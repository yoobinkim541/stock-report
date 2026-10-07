"""Metadata-only batch adapters for existing recommendation surfaces."""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from numbers import Real

from ml.recommendation_contracts import finite_json_value
from ml.recommendation_engine import build_snapshot, calibrate_score, rank_snapshots
from reports.recommendation_index import RecommendationIndex

logger = logging.getLogger(__name__)
EVENT_LIMIT = 2_000
FEATURE_VERSION = "indexed-source-metadata-v2"


def _version_values(row):
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    values = {}
    for name in ("model_version", "parameter_version"):
        direct, nested = row.get(name), metadata.get(name)
        if (any(value is not None and (not isinstance(value, str) or not value.strip())
                for value in (direct, nested))
                or direct is not None and nested is not None and direct != nested):
            raise ValueError("invalid calibration version metadata")
        values[name] = direct if direct is not None else nested
    return values


def _apply_calibration(snapshot, row, as_of, cache):
    try:
        _version_values(row)
    except ValueError:
        snapshot["calibration"] = {"status": "invalid_metadata"}
        return
    try:
        cutoff = min(_timestamp(as_of), _timestamp(snapshot["decision_ts"])).isoformat()
    except (ValueError, TypeError):
        snapshot["calibration"] = {"status": "invalid_timestamp"}
        return
    scale = row.get("score_range", (0, 1) if snapshot["surface"] == "entry_signals" else None)
    if (not isinstance(scale, (list, tuple)) or len(scale) != 2
            or any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in scale)
            or scale[0] >= scale[1]):
        snapshot["calibration"] = {"status": "unknown_scale"}
        return
    score = snapshot["raw_score"]
    if score is None or not scale[0] <= score <= scale[1]:
        snapshot["calibration"] = {"status": "invalid_score"}
        return
    scope = {key: snapshot[key] for key in
             ("surface", "horizon", "market", "model_version", "parameter_version")}
    key = (*scope.values(), *scale, cutoff)
    if key not in cache:
        try:
            from ml.recommendation_calibration import load_accepted_calibrator
            cache[key] = load_accepted_calibrator(**scope, as_of=cutoff, score_range=scale)
        except Exception:
            logger.debug("Scoped calibration unavailable", exc_info=True)
            cache[key] = None
    artifact = cache[key]
    snapshot["calibration"] = {"status": "unavailable"}
    if artifact is None:
        return
    probability = calibrate_score(score, artifact["calibrator"])
    if probability is None:
        return
    snapshot["calibrated_probability"] = probability
    # Beta sample bounds are not uncertainty bounds for a held-out score calibrator.
    snapshot["uncertainty_low"] = snapshot["uncertainty_high"] = None
    snapshot["missing_fields"] = list(dict.fromkeys(
        [name for name in snapshot["missing_fields"] if name != "calibrated_probability"]
        + ["uncertainty_low", "uncertainty_high"]))
    quality = [part for part in snapshot["data_quality"].split("|")
               if part != "missing_fields:calibrated_probability"]
    quality.extend(["missing_fields:uncertainty_low", "missing_fields:uncertainty_high"])
    snapshot["data_quality"] = "|".join(dict.fromkeys(quality))
    snapshot["calibration"] = {
        "status": "accepted", "version": artifact["version"], "scope": artifact["scope"],
        "score_range": list(scale), "source_digest": artifact["source_snapshot"]["digest"],
        "validation_end_date": artifact["validation_end_date"],
        "validation_metrics": artifact["validation_metrics"],
    }


def _timestamp(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def source_metadata(tickers, as_of, *, index=None):
    """One bounded query for the whole universe; never scan raw events or labels."""
    tickers = list(dict.fromkeys(str(t).upper() for t in tickers if t))
    if not tickers:
        return {}
    cutoff = _timestamp(as_of)
    grouped = {ticker: [] for ticker in tickers}
    quality = []
    try:
        index = index or RecommendationIndex()
        if hasattr(index, "db_path") and not index.db_path.exists():
            raise FileNotFoundError("recommendation index unavailable")
        from ml.recommendation_features import _source_health_quality
        quality.append(_source_health_quality(index))
        events = index.query_events(tickers=tickers, start=cutoff - timedelta(days=7),
                                    end=cutoff, limit=EVENT_LIMIT)
        if len(events) >= EVENT_LIMIT:
            quality.append("degraded:query_truncated")
        for event in events:
            ticker = str(event.get("ticker") or "").upper()
            if ticker not in grouped:
                continue
            try:
                # Publication alone is not proof that evidence was available then.
                if not event.get("collected_at") or not event.get("event_at"):
                    quality.append("degraded:missing_event_timestamps")
                    continue
                stamps = [_timestamp(event[key]) for key in
                          ("event_at", "published_at", "observed_at", "collected_at") if event.get(key)]
                if any(stamp > cutoff for stamp in stamps):
                    continue
            except (ValueError, TypeError):
                quality.append("degraded:invalid_event_timestamps")
                continue
            grouped[ticker].append(event)
    except Exception as exc:
        logger.debug("Recommendation metadata unavailable: %s", exc)
        quality.append("degraded:index_unavailable")
        if index is not None and hasattr(index, "record_fallback") and getattr(index, "db_path", None):
            try:
                if index.db_path.exists():
                    index.record_fallback("source_metadata")
            except Exception:
                logger.debug("Recommendation fallback metric unavailable", exc_info=True)
    output = {}
    for ticker, events in grouped.items():
        coverage = {}
        for family in ("news", "community", "macro", "market", "prediction_market"):
            records = [e for e in events if e.get("source_family") == family]
            coverage[family] = len({e.get("content_hash") or e.get("event_id") for e in records})
        refs = [{key: event.get(key) for key in
                 ("event_id", "source", "source_family", "raw_path", "raw_line", "raw_offset")}
                for event in events[:20]]
        freshness = min((max(0, (cutoff - _timestamp(e["event_at"])).total_seconds())
                         for e in events), default=None)
        output[ticker] = {"source_coverage": coverage, "evidence_refs": refs,
                          "freshness_seconds": freshness, "feature_version": FEATURE_VERSION,
                          "data_quality": "|".join(dict.fromkeys(quality + ([] if events else ["missing_sources"]))),
                          "missing_fields": ["labels", "news_axis"], "news_axis": None}
    return output


def snapshot_candidates(surface, candidates, *, as_of=None, market=None,
                        selected_tickers=(), top_n=5, allow_active=False, index=None):
    """Preserve input order unless explicitly activated with measured gate evidence."""
    candidates = list(candidates)
    stamp = as_of or datetime.now(timezone.utc).isoformat(timespec="seconds")
    sources = source_metadata([r.get("ticker") or r.get("code") for r in candidates], stamp, index=index)
    snapshots = []
    calibration_cache = {}
    for rank, row in enumerate(candidates, 1):
        ticker = row.get("ticker") or row.get("code")
        source = sources.get(str(ticker).upper(), {})
        features = row.get("features") or {}
        try:
            versions = _version_values(row)
        except ValueError:
            versions = {"model_version": None, "parameter_version": None}
        candidate = {
            **row, "ticker": ticker,
            "id": row.get("id") or f"{stamp}:{surface}:{ticker}",
            "decision_ts": row.get("decision_ts") or row.get("snapshot_ts") or stamp,
            "market": row.get("market") or market or ("KR" if str(ticker).endswith((".KS", ".KQ")) else "US"),
            "horizon": row.get("horizon") or "20d",
            "raw_score": row.get("raw_score", row.get("policy_score", row.get("total_score", row.get("score")))),
            "sample_size": row.get("sample_size", features.get("n_similar")),
            "expected_excess": row.get("expected_excess"),
            "rank": rank,
            "selection_status": row.get("selection_status") or ("selected" if ticker in selected_tickers else "shadow"),
            "evidence_refs": source.get("evidence_refs", []),
            "source_coverage": source.get("source_coverage"),
            **versions,
        }
        snapshot = build_snapshot(surface, candidate, source_features=source, as_of=stamp)
        _apply_calibration(snapshot, row, stamp, calibration_cache)
        snapshots.append(snapshot)
    gate = None
    active = allow_active and os.getenv("RECOMMENDER_V2_MODE", "shadow").strip().lower() == "active"
    if active:
        try:
            from ml.ranker import load_ranker, ranker_gate
            model = load_ranker()
            meta = getattr(model, "meta", {}) or {}
            horizons = {snapshot.get("horizon") for snapshot in snapshots}
            versions = {snapshot.get("model_version") for snapshot in snapshots}
            if (model is not None and meta.get("surface") == surface
                    and len(horizons) == 1 and meta.get("horizon") in horizons
                    and len(versions) == 1 and None not in versions
                    and meta.get("model_version") in versions):
                gate = ranker_gate(model)
        except Exception as exc:
            logger.warning("Recommendation activation held: %s", exc)
    ranked = rank_snapshots(snapshots, top_n=len(snapshots), active=active, gate=gate)
    if ranked and ranked[0]["ranking_mode"] == "active":
        ranked_ids = {snapshot["id"] for snapshot in ranked}
        for snapshot in snapshots:
            if snapshot["id"] not in ranked_ids:
                ranked.append({**snapshot, "ranking_mode": "active"})
        for position, snapshot in enumerate(ranked):
            if snapshot["selection_status"] not in {"filtered", "invalid"}:
                snapshot["selection_status"] = "selected" if position < top_n else "shadow"
    else:
        for snapshot, row in zip(ranked, candidates):
            snapshot["selection_status"] = row.get("selection_status") or (
                "selected" if row.get("ticker") in selected_tickers else "shadow"
            )
    return ranked


def decision_metadata(snapshot, *, selection_status=None):
    snapshot = dict(snapshot)
    if selection_status is not None:
        snapshot["selection_status"] = selection_status
    return finite_json_value({
        "selection_status": snapshot.get("selection_status", "shadow"),
        "selection_rank": snapshot.get("rank"),
        "data_quality": snapshot.get("data_quality", "degraded:missing_snapshot"),
        "source_coverage": snapshot.get("source_coverage"),
        "evidence_refs": snapshot.get("evidence_refs", []),
        "recommendation_v2_shadow": snapshot,
    })
