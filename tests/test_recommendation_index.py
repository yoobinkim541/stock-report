import json
import sqlite3
from datetime import datetime, timedelta, timezone

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
            "trust_prior": 0.74,
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
