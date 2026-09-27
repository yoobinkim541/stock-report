import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from ml.data_sources import fetch_news_features
from reports.recommendation_index import RecommendationIndex


UTC = timezone.utc


def _write_events(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _event(source, at, **extra):
    return {
        "source": source,
        "id": f"{source}-{at.isoformat()}",
        "title": "NVDA update",
        "url": f"https://example.test/{source}/{at.isoformat()}",
        "tickers": ["NVDA"],
        "published_at": at.isoformat(),
        "collected_at": at.isoformat(),
        **extra,
    }


def _index(tmp_path, rows):
    cache_dir = tmp_path / "cache"
    _write_events(cache_dir / "events-2026-09-20.jsonl", rows)
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache_dir)
    index.ingest()
    return index


def _build(*args, **kwargs):
    from ml.recommendation_features import build_news_feature_frame
    return build_news_feature_frame(*args, **kwargs)


def test_fetch_news_features_replaces_the_placeholder_with_indexed_features(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [_event("saveticker", at)])

    frame = fetch_news_features("NVDA", "2026-09-20", at.isoformat(), index=index, labels=[])

    assert frame.loc[0, "count"] == 1


def test_build_news_feature_frame_uses_only_direct_news_and_community_evidence(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [
        _event("saveticker", at, body="article body"),
        _event("arca", at + timedelta(minutes=1)),
        _event("fred", at + timedelta(minutes=2)),
        _event("yahoo_finance", at + timedelta(minutes=3)),
        _event("polymarket", at + timedelta(minutes=4)),
    ])

    frame = _build("NVDA", "2026-09-20", "2026-09-20T23:59:59+00:00", index=index)

    assert frame[["date", "ticker", "count", "article_count", "community_count"]].to_dict("records") == [{
        "date": "2026-09-20", "ticker": "NVDA", "count": 2, "article_count": 1, "community_count": 1,
    }]
    assert frame.loc[0, "source_diversity"] == 2
    assert frame.loc[0, "body_ratio"] == 0.5
    assert frame.loc[0, "trust_score"] == 0.65


def test_build_news_feature_frame_excludes_future_events_and_future_or_incomplete_labels(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    cutoff = at + timedelta(hours=1)
    index = _index(tmp_path, [
        _event("saveticker", at),
        _event("saveticker", cutoff + timedelta(minutes=1)),
    ])
    labels = [
        {"id": "available", "tickers": ["NVDA"], "direction": 1, "strength": 5,
         "published_at": at.isoformat(), "labeled_at": cutoff.isoformat()},
        {"id": "future", "tickers": ["NVDA"], "direction": -1, "strength": 5,
         "published_at": at.isoformat(), "labeled_at": (cutoff + timedelta(minutes=1)).isoformat()},
        {"id": "missing-time", "tickers": ["NVDA"], "direction": -1, "strength": 5,
         "published_at": at.isoformat()},
    ]

    frame = _build("NVDA", "2026-09-20", cutoff.isoformat(), index=index, labels=labels)

    assert frame.loc[0, "count"] == 1
    assert frame.loc[0, "news_axis"] > 0.5
    assert "missing_fields:labels" in frame.loc[0, "data_quality"]


def test_labels_for_other_tickers_do_not_claim_label_coverage(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [_event("saveticker", at)])
    labels = [{"id": "other", "tickers": ["AAPL"], "direction": 1, "strength": 5,
               "published_at": at.isoformat(), "labeled_at": at.isoformat()}]

    frame = _build("NVDA", "2026-09-20", at.isoformat(), index=index, labels=labels)

    assert pd.isna(frame.loc[0, "news_axis"])
    assert "missing_labels" in frame.loc[0, "data_quality"]


def test_source_diversity_counts_distinct_sources_not_duplicate_events(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [
        _event("saveticker", at, url="https://example.test/one"),
        _event("saveticker", at + timedelta(minutes=1), url="https://example.test/two"),
        _event("arca", at + timedelta(minutes=2)),
    ])

    frame = _build("NVDA", "2026-09-20", "2026-09-20T23:59:59+00:00", index=index)

    assert frame.loc[0, "count"] == 3
    assert frame.loc[0, "source_diversity"] == 2


def test_duplicate_content_counts_once_but_preserves_contributing_source_diversity(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [
        _event("saveticker", at, body="same syndicated article"),
        _event("arca", at + timedelta(minutes=1), body="same syndicated article"),
    ])

    frame = _build("NVDA", "2026-09-20", "2026-09-20T23:59:59+00:00", index=index)

    assert frame.loc[0, "count"] == 1
    assert frame.loc[0, "article_count"] == 1
    assert frame.loc[0, "community_count"] == 1
    assert frame.loc[0, "source_diversity"] == 2
    assert frame.loc[0, "trust_score"] == 0.75
    assert frame.loc[0, "body_ratio"] == 1.0


def test_blocked_cache_source_health_is_degraded_not_zero_news_claim(tmp_path):
    index = _index(tmp_path, [])
    (index.cache_dir / "source_health.json").write_text(
        json.dumps({"saveticker": {"availability": "blocked", "availability_reason": "rate limit"}}),
        encoding="utf-8",
    )

    frame = _build("NVDA", "2026-09-20", "2026-09-20T23:59:59+00:00", index=index)

    assert frame.empty
    assert "degraded:blocked_sources" in frame.attrs["data_quality"]


def test_fetch_news_features_delegates_and_attaches_feature_metadata(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [_event("saveticker", at)])

    frame = fetch_news_features("NVDA", "2026-09-20", at.isoformat(), index=index, labels=[])

    assert isinstance(frame, pd.DataFrame)
    assert frame.loc[0, "count"] == 1
    assert frame.attrs["feature_version"] == "recommendation-news-v2"
    assert "no_cache_health" in frame.attrs["data_quality"]
