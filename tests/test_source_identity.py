from __future__ import annotations

from datetime import datetime, timedelta, timezone


UTC = timezone.utc


def test_immutable_content_keeps_same_id_across_collection_times():
    from reports.source_identity import normalize_event_identity

    event = {
        "source": "saveticker",
        "type": "news",
        "title": "Fed holds rates",
        "url": "https://example.com/news/1",
    }
    first = normalize_event_identity(event, datetime(2026, 8, 21, 10, 5, tzinfo=UTC))
    second = normalize_event_identity(event, datetime(2026, 8, 21, 11, 5, tzinfo=UTC))

    assert first["record_kind"] == "content"
    assert first["id"] == second["id"] == first["content_id"]
    assert first["entity_id"] == second["entity_id"]


def test_mutable_observation_keeps_entity_but_changes_id_between_buckets():
    from reports.source_identity import normalize_event_identity

    event = {
        "source": "polymarket",
        "type": "prediction_market",
        "entity_id": "polymarket:market-200",
        "url": "https://polymarket.com/event/fed",
        "metrics": {"market_id": "market-200", "yes_probability": 0.63},
    }
    first = normalize_event_identity(event, datetime(2026, 8, 21, 10, 5, tzinfo=UTC))
    same_bucket = normalize_event_identity(event, datetime(2026, 8, 21, 10, 29, tzinfo=UTC))
    next_bucket = normalize_event_identity(event, datetime(2026, 8, 21, 10, 35, tzinfo=UTC))

    assert first["record_kind"] == "observation"
    assert first["entity_id"] == same_bucket["entity_id"] == next_bucket["entity_id"]
    assert first["id"] == same_bucket["id"]
    assert first["id"] != next_bucket["id"]
    assert first["observation_bucket"] == "2026-08-21T10:00:00+00:00"
    assert next_bucket["observation_bucket"] == "2026-08-21T10:30:00+00:00"


def test_native_metric_id_builds_stable_observation_entity():
    from reports.source_identity import normalize_event_identity

    event = {
        "source": "fred",
        "type": "macro_snapshot",
        "url": "https://fred.stlouisfed.org/series/DGS10",
        "metrics": {"series_id": "DGS10", "current": 4.1},
    }
    row = normalize_event_identity(event, datetime(2026, 8, 21, 10, 5, tzinfo=UTC))

    assert row["entity_id"] == "fred:DGS10"
    assert row["content_id"] == ""


def test_explicit_observed_at_controls_bucket_instead_of_append_time():
    from reports.source_identity import normalize_event_identity

    event = {
        "source": "kalshi",
        "type": "prediction_market",
        "entity_id": "kalshi:KXFED-CUT",
        "observed_at": "2026-08-21T09:31:15+00:00",
    }
    row = normalize_event_identity(event, datetime(2026, 8, 21, 11, 0, tzinfo=UTC))

    assert row["observation_bucket"] == "2026-08-21T09:30:00+00:00"
    assert row["observed_at"] == "2026-08-21T09:31:15+00:00"


def test_worldgovernmentbonds_entity_id_includes_country_and_maturity():
    from reports.source_identity import normalize_event_identity

    observed_at = datetime(2026, 8, 21, 10, 5, tzinfo=UTC)
    united_states = normalize_event_identity({
        "source": "worldgovernmentbonds",
        "type": "macro_snapshot",
        "metrics": {"country": "united-states", "maturity": "10Y", "yield_pct": 4.4},
    }, observed_at)
    japan = normalize_event_identity({
        "source": "worldgovernmentbonds",
        "type": "macro_snapshot",
        "metrics": {"country": "japan", "maturity": "10Y", "yield_pct": 1.2},
    }, observed_at)

    assert united_states["entity_id"] == "worldgovernmentbonds:united-states:10y"
    assert japan["entity_id"] == "worldgovernmentbonds:japan:10y"
    assert united_states["id"] != japan["id"]
