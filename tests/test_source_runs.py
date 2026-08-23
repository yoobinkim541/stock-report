from __future__ import annotations

from datetime import datetime, timedelta, timezone


UTC = timezone.utc


def test_source_run_manifest_roundtrip_and_recent_filter(tmp_path):
    from reports.source_runs import load_source_runs, record_source_run

    cache = tmp_path / "cache"
    old = {
        "provider": "fred",
        "started_at": "2026-08-19T10:00:00+00:00",
        "finished_at": "2026-08-19T10:00:10+00:00",
        "fetched": 12,
        "persisted": 12,
    }
    recent = {
        "provider": "kalshi",
        "started_at": "2026-08-21T09:00:00+00:00",
        "finished_at": "2026-08-21T09:00:01+00:00",
        "fetched": 50,
        "persisted": 50,
        "transport": "direct",
    }
    record_source_run(cache, old)
    saved = record_source_run(cache, recent)

    rows = load_source_runs(cache, hours=24, now=datetime(2026, 8, 21, 10, 0, tzinfo=UTC))

    assert [row["provider"] for row in rows] == ["kalshi"]
    assert saved["duration_ms"] == 1000
    assert saved["availability"] == "available"


def test_selected_source_health_leaves_unattempted_sources_unchanged(tmp_path):
    from reports import source_collector as sc

    cache = tmp_path / "cache"
    first = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
    initial = sc.update_source_health(
        [
            {"source": "saveticker", "title": "news"},
            {"source": "kalshi", "title": "market"},
        ],
        cache_dir=cache,
        now=first,
    )
    second = first + timedelta(hours=1)
    health = sc.update_source_health(
        [],
        cache_dir=cache,
        now=second,
        attempted_sources=["kalshi"],
        run_stats={"kalshi": {"fetched": 20, "persisted": 0, "duration_ms": 850}},
    )

    assert health["saveticker"]["last_run"] == initial["saveticker"]["last_run"]
    assert health["kalshi"]["last_run"] == second.astimezone(timezone(timedelta(hours=9))).isoformat()
    assert health["kalshi"]["last_fetched_count"] == 20
    assert health["kalshi"]["last_persisted_count"] == 0
    assert health["kalshi"]["zero_persist_streak"] == 1
    assert health["kalshi"]["last_duration_ms"] == 850


def test_successful_persistence_resets_zero_persist_streak(tmp_path):
    from reports import source_collector as sc

    cache = tmp_path / "cache"
    now = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
    sc.update_source_health(
        [],
        cache_dir=cache,
        now=now,
        attempted_sources=["kalshi"],
        run_stats={"kalshi": {"fetched": 10, "persisted": 0}},
    )
    health = sc.update_source_health(
        [{"source": "kalshi", "title": "market"}],
        cache_dir=cache,
        now=now + timedelta(minutes=30),
        attempted_sources=["kalshi"],
        run_stats={"kalshi": {"fetched": 10, "persisted": 10}},
    )

    assert health["kalshi"]["zero_persist_streak"] == 0
    assert health["kalshi"]["last_persisted_count"] == 10
