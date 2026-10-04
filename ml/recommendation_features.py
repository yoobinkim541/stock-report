"""Point-in-time recommendation features derived from the event index."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from providers import news_labels
from reports.recommendation_index import RecommendationIndex
from reports.source_collector import load_source_health


FEATURE_VERSION = "recommendation-news-v2"
LEGACY_COLUMNS = ["date", "ticker", "count", "sentiment", "theme", "event"]
FEATURE_COLUMNS = [
    "article_count", "community_count", "source_diversity", "trust_score",
    "body_ratio", "freshness_seconds", "news_axis", "data_quality",
]
ALL_COLUMNS = LEGACY_COLUMNS + FEATURE_COLUMNS
DIRECT_SOURCE_FAMILIES = ("news", "community")
TRUST_SCORES = {"A": 0.90, "B": 0.75, "C": 0.55, "D": 0.35}


def build_news_feature_frame(
    ticker: str,
    start: str,
    as_of: str | None = None,
    index: RecommendationIndex | None = None,
    labels: list[dict] | None = None,
) -> pd.DataFrame:
    """Return direct news/community features available at ``as_of``.

    The index supplies metadata only; raw event bodies are never opened here.
    """
    cutoff = _parse_time(as_of) if as_of else datetime.now(timezone.utc)
    index = index or RecommendationIndex()
    quality = [_source_health_quality(index)]
    available_labels, label_quality = _available_labels(ticker, labels, cutoff)
    quality.extend(label_quality)
    events = index.query_events(
        tickers=[ticker],
        start=_start_of_day(start),
        end=cutoff,
        source_families=DIRECT_SOURCE_FAMILIES,
        limit=2_000,
    )
    rows = _aggregate_events(ticker, events, cutoff, available_labels, quality)
    frame = pd.DataFrame(rows, columns=ALL_COLUMNS)
    frame.attrs["feature_version"] = FEATURE_VERSION
    frame.attrs["data_quality"] = _quality_text(quality)
    return frame


def _aggregate_events(
    ticker: str,
    events: Iterable[dict],
    cutoff: datetime,
    labels: list[dict],
    quality: list[str],
) -> list[dict]:
    by_day: dict[str, list[dict]] = {}
    for event in events:
        event_at = _try_parse_time(event.get("event_at"))
        provenance = [
            _try_parse_time(event.get(key))
            for key in ("published_at", "observed_at", "collected_at")
        ]
        available_times = [stamp for stamp in provenance if stamp is not None]
        if event_at is None or not available_times:
            quality.append("missing_fields:event_timestamp")
            continue
        row_cutoff = min(cutoff, event_at.replace(hour=23, minute=59, second=59, microsecond=999999))
        if event_at > row_cutoff or max(available_times) > row_cutoff:
            continue
        by_day.setdefault(event_at.date().isoformat(), []).append(event)

    rows = []
    for day, day_events in sorted(by_day.items()):
        row_cutoff = min(cutoff, _start_of_day(day).replace(hour=23, minute=59, second=59, microsecond=999999))
        row_labels, row_label_quality = _available_labels(ticker, labels, row_cutoff)
        axis = news_labels.news_axis(str(ticker), labels=row_labels, asof=row_cutoff) if row_labels else None
        row_quality = [value for value in quality if value != "missing_labels"] + row_label_quality
        evidence = _group_evidence(day_events)
        count = len(evidence)
        latest = max(_parse_time(event["event_at"]) for event in day_events)
        rows.append({
            "date": day,
            "ticker": str(ticker).upper(),
            "count": count,
            "sentiment": pd.NA,
            "theme": pd.NA,
            "event": pd.NA,
            "article_count": sum(any(event.get("source_family") == "news" for event in records)
                                 for records in evidence.values()),
            "community_count": sum(any(event.get("source_family") == "community" for event in records)
                                   for records in evidence.values()),
            "source_diversity": len({str(event.get("source") or "") for event in day_events}),
            "trust_score": round(sum(max(TRUST_SCORES.get(str(event.get("trust") or "").upper(), 0.55)
                                           for event in records) for records in evidence.values()) / count, 2),
            "body_ratio": round(sum(any(bool(event.get("body_available")) for event in records)
                                    for records in evidence.values()) / count, 4),
            "freshness_seconds": max(0, int((row_cutoff - latest).total_seconds())),
            "news_axis": axis,
            "data_quality": _quality_text(row_quality),
        })
    return rows


def _group_evidence(events: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        identity = next((str(event.get(key) or "") for key in ("content_hash", "observation_id", "event_id")
                         if event.get(key)), "")
        grouped.setdefault(identity, []).append(event)
    return grouped


def _available_labels(ticker: str, labels: list[dict] | None, cutoff: datetime) -> tuple[list[dict], list[str]]:
    supplied = labels if labels is not None else news_labels.load_labels()
    available, missing_timestamps = [], False
    for label in supplied:
        if not _label_matches_ticker(label, ticker):
            continue
        published_at = _try_parse_time(label.get("published_at"))
        labeled_at = _try_parse_time(label.get("labeled_at"))
        if published_at is None or labeled_at is None:
            missing_timestamps = True
            continue
        if published_at <= cutoff and labeled_at <= cutoff:
            available.append(label)
    quality = ["missing_labels"] if not available else []
    if missing_timestamps:
        quality.append("missing_fields:labels")
    return available, quality


def _label_matches_ticker(label: dict, ticker: str) -> bool:
    target = str(ticker).split(".")[0].upper()
    return target in {str(value).split(".")[0].upper() for value in label.get("tickers") or []}


def _source_health_quality(index: RecommendationIndex) -> str:
    health = getattr(index, "source_health", None)
    if callable(health):
        try:
            health = health()
        except TypeError:
            health = None
    if not isinstance(health, dict):
        health = load_source_health(index.cache_dir)
    if not health:
        return "no_cache_health"
    availability = {str((record or {}).get("availability") or "").lower() for record in health.values()}
    if availability & {"blocked", "disabled"}:
        return "degraded:blocked_sources"
    if availability & {"unavailable", "error"}:
        return "degraded:unavailable_sources"
    if any((record or {}).get("fetch_success") is False for record in health.values()):
        return "degraded:source_health"
    return "ok"


def _quality_text(values: Iterable[str]) -> str:
    return ";".join(dict.fromkeys(value for value in values if value)) or "ok"


def _start_of_day(value: str) -> datetime:
    parsed = _parse_time(value)
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_time(value: object) -> datetime:
    parsed = _try_parse_time(value)
    if parsed is None:
        raise ValueError(f"invalid timestamp: {value!r}")
    return parsed


def _try_parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
