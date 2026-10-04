import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reports.recommendation_index import RecommendationIndex


UTC = timezone.utc


def _event(source, ticker, collected_at, **extra):
    return {
        "source": source,
        "title": f"{ticker} update",
        "url": f"https://example.test/{source}/{ticker}",
        "tickers": [ticker],
        "collected_at": collected_at.isoformat(),
        **extra,
    }


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_ingest_creates_schema_indexes_events_without_raw_bodies_and_filters_by_family(tmp_path):
    """Removing file references, source classification, or body redaction breaks this contract."""
    cache = tmp_path / "cache"
    event_file = cache / "events-2026-09-20.jsonl"
    at = datetime(2026, 9, 20, 9, 30, tzinfo=UTC)
    _write_rows(
        event_file,
        [
            _event("saveticker", "NVDA", at, body="never persist this raw body"),
            _event("arca", "NVDA", at + timedelta(minutes=1), body_raw="also not in sqlite"),
        ],
    )
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache)

    result = index.ingest()
    news = index.query_events(tickers=["NVDA"], source_families=["news"])
    all_events = index.query_events(tickers=["NVDA"])

    assert result["indexed"] == 2
    assert [row["source"] for row in news] == ["saveticker"]
    assert len(all_events) == 2
    assert all(row["raw_path"] == str(event_file) for row in all_events)
    assert all(isinstance(row["raw_line"], int) and row["raw_line"] > 0 for row in all_events)
    assert all(isinstance(row["raw_offset"], int) and row["raw_offset"] >= 0 for row in all_events)
    assert all("body" not in row and "body_raw" not in row for row in all_events)

    with sqlite3.connect(tmp_path / "index.sqlite") as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"ingest_checkpoint", "event_index", "ticker_day_features"} <= tables


def test_ingest_is_idempotent_tails_appends_and_materializes_ticker_day_features(tmp_path):
    """Breaking checkpoints, content identity, or daily aggregation must fail this test."""
    cache = tmp_path / "cache"
    event_file = cache / "events-2026-09-21.jsonl"
    at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    content = _event("saveticker", "AAPL", at)
    _write_rows(event_file, [content, content, _event("arca", "AAPL", at + timedelta(hours=1))])
    database = tmp_path / "index.sqlite"

    first = RecommendationIndex(db_path=database, cache_dir=cache)
    assert first.ingest()["indexed"] == 2
    assert first.ingest()["indexed"] == 0

    _write_rows(event_file, [_event("saveticker", "AAPL", at + timedelta(hours=2), url="https://example.test/second")])
    restarted = RecommendationIndex(db_path=database, cache_dir=cache)
    assert restarted.ingest()["indexed"] == 1
    features = restarted.ticker_day_features("AAPL", asof=at + timedelta(days=1))

    assert len(restarted.query_events(tickers=["AAPL"])) == 3
    assert features == [
        {
            "ticker": "AAPL",
            "day": "2026-09-21",
            "event_count": 3,
            "article_count": 2,
            "community_count": 1,
            "source_diversity": 2,
            "trust_prior": 0.68,
            "body_available_count": 0,
            "freshness_seconds": 79200,
        }
    ]


def test_ingest_indexes_each_ticker_named_by_one_event(tmp_path):
    """Collapsing a multi-ticker event to its first symbol loses recommendation evidence."""
    cache = tmp_path / "cache"
    at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    _write_rows(
        cache / "events-2026-09-21.jsonl",
        [_event("saveticker", "AAPL", at, tickers=["AAPL", "MSFT"])],
    )
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache)

    assert index.ingest()["indexed"] == 2
    assert [row["ticker"] for row in index.query_events(tickers=["MSFT"])] == ["MSFT"]
    assert index.ticker_day_features("MSFT", asof=at + timedelta(hours=1))[0]["event_count"] == 1


def test_defaults_keep_raw_cache_and_ml_index_storage_separate():
    """Placing the SQLite index in the raw cache violates the storage layout contract."""
    index = RecommendationIndex()

    assert index.cache_dir == Path.home() / "reports" / "source-cache"
    assert index.db_path == Path.home() / "reports" / "ml-cache" / "recommendation_index.sqlite3"


def test_ticker_day_features_use_specified_source_trust_priors(tmp_path, monkeypatch):
    """Changing source trust priors must change the materialized feature value."""
    from reports import source_collector

    monkeypatch.setitem(source_collector.SOURCE_CLASSIFICATION, "dsource", {
        "family": "other", "kind": "event", "trust": "D", "horizon": "1d",
    })
    at = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    expected = {"yahoo_finance": 0.90, "saveticker": 0.75, "arca": 0.55, "dsource": 0.35}

    for source, prior in expected.items():
        cache = tmp_path / source
        _write_rows(cache / "events-2026-09-21.jsonl", [_event(source, "AAPL", at)])
        index = RecommendationIndex(db_path=tmp_path / f"{source}.sqlite", cache_dir=cache)
        index.ingest()

        assert index.ticker_day_features("AAPL", asof=at + timedelta(hours=1))[0]["trust_prior"] == prior


def test_ingest_leaves_unterminated_jsonl_tail_pending_until_writer_finishes_it(tmp_path):
    """Advancing past an incomplete append-only record permanently loses evidence."""
    cache = tmp_path / "cache"
    event_file = cache / "events-2026-09-21.jsonl"
    event = _event("saveticker", "AAPL", datetime(2026, 9, 21, 12, 0, tzinfo=UTC))
    serialized = json.dumps(event).encode("utf-8")
    event_file.parent.mkdir(parents=True)
    event_file.write_bytes(serialized[:20])
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache)

    pending = index.ingest()
    assert {key: pending[key] for key in ("files", "indexed", "errors", "fallback_count")} == {
        "files": 1, "indexed": 0, "errors": 0, "fallback_count": 0,
    }
    with event_file.open("ab") as handle:
        handle.write(serialized[20:] + b"\n")

    assert index.ingest()["indexed"] == 1
    assert [row["ticker"] for row in index.query_events(tickers=["AAPL"])] == ["AAPL"]


def test_query_preserves_timestamp_provenance_and_uses_published_time_for_cutoffs(tmp_path):
    """Using collection time for a future-published event violates point-in-time exclusion."""
    cache = tmp_path / "cache"
    at = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    future = at + timedelta(hours=6)
    _write_rows(
        cache / "events-2026-09-21.jsonl",
        [_event("saveticker", "AAPL", at, observed_at=(at + timedelta(hours=1)).isoformat(), published_at=future.isoformat())],
    )
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache)
    index.ingest()

    row = index.query_events(tickers=["AAPL"])[0]
    assert row["collected_at"] == at.isoformat()
    assert row["observed_at"] == (at + timedelta(hours=1)).isoformat()
    assert row["published_at"] == future.isoformat()
    assert row["event_at"] == future.isoformat()
    assert index.query_events(tickers=["AAPL"], end=at + timedelta(hours=2)) == []


def test_same_content_from_two_sources_counts_once_but_keeps_source_diversity(tmp_path):
    """Counting duplicate body hashes as separate evidence overstates corroboration."""
    cache = tmp_path / "cache"
    at = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    _write_rows(
        cache / "events-2026-09-21.jsonl",
        [
            _event("saveticker", "AAPL", at, body="Same syndicated article body", url="https://first.test/article"),
            _event("arca", "AAPL", at + timedelta(minutes=5), body="Same syndicated article body", url="https://first.test/article"),
        ],
    )
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache)
    index.ingest()

    rows = index.query_events(tickers=["AAPL"])
    features = index.ticker_day_features("AAPL", asof=at + timedelta(hours=1))

    assert len(rows) == 2
    assert rows[0]["content_hash"] == rows[1]["content_hash"]
    assert features[0]["event_count"] == 1
    assert features[0]["source_diversity"] == 2


def test_query_filters_by_source_and_asof_excludes_same_day_future_events(tmp_path):
    """Source selection and intra-day as-of must not leak unrelated or future evidence."""
    cache = tmp_path / "cache"
    morning = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    evening = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    _write_rows(
        cache / "events-2026-09-21.jsonl",
        [_event("saveticker", "AAPL", morning), _event("arca", "AAPL", evening)],
    )
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache)
    index.ingest()

    assert [row["source"] for row in index.query_events(tickers=["AAPL"], sources=["saveticker"])] == ["saveticker"]
    features = index.ticker_day_features("AAPL", asof=morning + timedelta(hours=3))
    assert features[0]["event_count"] == 1
    assert features[0]["freshness_seconds"] == 10800


def test_ingest_recovers_after_rotation_and_reports_invalid_json_health(tmp_path):
    """Failure to reset a shrunken file checkpoint would miss replacement cache data."""
    cache = tmp_path / "cache"
    event_file = cache / "events-2026-09-22.jsonl"
    at = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    _write_rows(event_file, [_event("saveticker", "MSFT", at), _event("arca", "MSFT", at + timedelta(minutes=1))])
    index = RecommendationIndex(db_path=tmp_path / "index.sqlite", cache_dir=cache)
    assert index.ingest()["indexed"] == 2

    event_file.write_text("{invalid json}\n" + json.dumps(_event("kalshi", "TSLA", at + timedelta(minutes=2))) + "\n", encoding="utf-8")
    result = index.ingest()
    status = index.status()

    assert result["errors"] == 1
    assert [row["ticker"] for row in index.query_events(tickers=["TSLA"])] == ["TSLA"]
    assert status["errors"] == 1
    assert status["fallback_count"] == 0
    assert status["lag_seconds"] >= 0
