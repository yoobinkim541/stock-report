import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

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


def test_historical_rows_use_their_own_label_cutoff_and_freshness(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [_event("saveticker", at + timedelta(days=day)) for day in range(3)])
    labels = [
        {"id": "delayed", "tickers": ["NVDA"], "direction": 1, "strength": 5,
         "published_at": at.isoformat(), "labeled_at": "2026-09-21T23:59:59.999999Z"},
        {"id": "later-publication", "tickers": ["NVDA"], "direction": -1, "strength": 5,
         "published_at": "2026-09-22T00:00:00Z", "labeled_at": at.isoformat()},
        {"id": "after-decision", "tickers": ["NVDA"], "direction": -1, "strength": 5,
         "published_at": at.isoformat(), "labeled_at": "2026-09-22T10:00:00.000001Z"},
        {"id": "incomplete", "tickers": ["NVDA"], "published_at": at.isoformat()},
    ]

    frame = _build("NVDA", "2026-09-20", "2026-09-22T10:00:00Z", index=index, labels=labels)

    assert pd.isna(frame.loc[0, "news_axis"])
    assert "missing_labels" in frame.loc[0, "data_quality"]
    assert frame.loc[1, "news_axis"] > 0.5
    assert frame.loc[2, "news_axis"] < 0.5
    assert all("missing_labels" not in value for value in frame.loc[1:, "data_quality"])
    assert all("missing_fields:labels" in value for value in frame["data_quality"])
    assert all("no_cache_health" in value for value in frame["data_quality"])
    assert frame["freshness_seconds"].tolist() == [53999, 53999, 3600]
    for day, cutoff in [(1, "2026-09-21T23:59:59.999999Z"), (2, "2026-09-22T10:00:00Z")]:
        single = _build("NVDA", frame.loc[day, "date"], cutoff, index=index, labels=labels)
        assert frame.loc[day, "news_axis"] == single.loc[0, "news_axis"]


@pytest.mark.parametrize("timestamp", ["observed_at", "collected_at"])
def test_late_indexed_availability_does_not_rewrite_historical_evidence(tmp_path, timestamp):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [
        _event("arca", at),
        _event("saveticker", at + timedelta(hours=1), body="late article",
               **{timestamp: "2026-09-21T10:00:00Z"}),
        _event("saveticker", at + timedelta(days=1)),
    ])
    # Feature reads must continue to work using only the index metadata.
    (index.cache_dir / "events-2026-09-20.jsonl").unlink()

    for cutoff in ["2026-09-20T12:00:00Z", "2026-09-21T12:00:00Z"]:
        frame = _build("NVDA", "2026-09-20", cutoff, index=index, labels=[])
        assert frame.loc[0, "count"] == 1
        assert frame.loc[0, "article_count"] == 0
        assert frame.loc[0, "source_diversity"] == 1
        assert frame.loc[0, "trust_score"] == 0.55
        assert frame.loc[0, "body_ratio"] == 0.0


def test_same_day_collection_is_visible_only_at_its_available_timestamp(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [_event("saveticker", at, collected_at="2026-09-20T10:00:00Z")])

    before = _build("NVDA", "2026-09-20", "2026-09-20T09:59:59Z", index=index, labels=[])
    available = _build("NVDA", "2026-09-20", "2026-09-20T10:00:00Z", index=index, labels=[])

    assert before.empty
    assert available.loc[0, "count"] == 1
    assert available.loc[0, "freshness_seconds"] == 3600


def test_indexed_events_without_timestamp_provenance_are_not_fabricated(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [_event("saveticker", at, published_at=None, collected_at=None)])

    frame = _build("NVDA", "2026-09-20", "2099-01-01T00:00:00Z", index=index, labels=[])

    assert frame.empty


def test_published_only_events_preserve_existing_availability(tmp_path):
    at = datetime(2026, 9, 20, 9, tzinfo=UTC)
    index = _index(tmp_path, [_event("saveticker", at, collected_at=None)])

    frame = _build("NVDA", "2026-09-20", at.isoformat(), index=index, labels=[])

    assert frame.loc[0, "count"] == 1
    assert frame.loc[0, "freshness_seconds"] == 0
