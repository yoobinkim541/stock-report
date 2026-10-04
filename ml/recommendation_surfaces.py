"""Metadata-only batch adapters for existing recommendation surfaces."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from ml.recommendation_contracts import finite_json_value
from ml.recommendation_engine import build_snapshot, rank_snapshots
from reports.recommendation_index import RecommendationIndex

logger = logging.getLogger(__name__)
EVENT_LIMIT = 2_000
FEATURE_VERSION = "indexed-source-metadata-v2"


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
    for rank, row in enumerate(candidates, 1):
        ticker = row.get("ticker") or row.get("code")
        source = sources.get(str(ticker).upper(), {})
        features = row.get("features") or {}
        candidate = {
            **row, "ticker": ticker,
            "id": row.get("id") or f"{stamp}:{surface}:{ticker}",
            "decision_ts": row.get("snapshot_ts") or stamp,
            "market": row.get("market") or market or ("KR" if str(ticker).endswith((".KS", ".KQ")) else "US"),
            "horizon": row.get("horizon") or "20d",
            "raw_score": row.get("raw_score", row.get("policy_score", row.get("total_score", row.get("score")))),
            "sample_size": row.get("sample_size", features.get("n_similar")),
            "expected_excess": row.get("expected_excess"),
            "rank": rank,
            "selection_status": row.get("selection_status") or ("selected" if ticker in selected_tickers else "shadow"),
            "evidence_refs": source.get("evidence_refs", []),
            "source_coverage": source.get("source_coverage"),
        }
        snapshots.append(build_snapshot(surface, candidate, source_features=source, as_of=stamp))
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
        for position, snapshot in enumerate(ranked):
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
