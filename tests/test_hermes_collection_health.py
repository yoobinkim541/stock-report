from datetime import datetime, timedelta, timezone

import pytest

from reports import source_collector as collector
from reports import wiki_pipeline_health


NOW = datetime(2026, 10, 10, 11, tzinfo=timezone.utc)


def old_health(**fields):
    return {"hermes_webhook": {
        "last_success": (NOW - timedelta(hours=41)).isoformat(),
        "last_count": 1, **fields,
    }}


def test_legacy_passive_webhook_is_not_a_scheduled_collection_gap(monkeypatch):
    monkeypatch.delenv("HERMES_SOURCE_COLLECTION_MODE", raising=False)
    assert collector.stale_sources(old_health(), now=NOW) == []


@pytest.mark.parametrize("config", ["env", "receipt"])
def test_scheduled_webhook_still_alerts_when_deliveries_stop(monkeypatch, config):
    monkeypatch.delenv("HERMES_SOURCE_COLLECTION_MODE", raising=False)
    health = old_health()
    if config == "env":
        monkeypatch.setenv("HERMES_SOURCE_COLLECTION_MODE", "scheduled")
    else:
        health["hermes_webhook"]["collection_mode"] = "scheduled"
    rows = collector.stale_sources(health, now=NOW)
    assert rows == [{"source": "hermes_webhook", "hours": 41.0,
                     "threshold": 24, "error": None}]


def test_passive_receiver_is_visible_but_not_reported_as_healthy_collection(monkeypatch):
    monkeypatch.delenv("HERMES_SOURCE_COLLECTION_MODE", raising=False)
    section = wiki_pipeline_health._summarize_source_health(old_health(), [], [])
    row = next(row for row in section["sources"] if row["source"] == "hermes_webhook")
    assert row["collection_mode"] == "on_demand"
    assert section["overall"]["on_demand_sources"] == 1
    assert section["overall"]["healthy_sources"] == 0
    assert row["last_success"] == old_health()["hermes_webhook"]["last_success"]


def test_collection_mode_is_recorded_without_refreshing_delivery_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_SOURCE_COLLECTION_MODE", "scheduled")
    health = collector.update_source_health([], cache_dir=tmp_path, now=NOW,
        attempted_sources=["hermes_webhook"],
        run_stats={"hermes_webhook": {"fetch_success": False, "error": "sender_unavailable"}})
    assert health["hermes_webhook"]["collection_mode"] == "scheduled"
    assert "last_success" not in health["hermes_webhook"]
    assert collector.stale_sources(health, now=NOW + timedelta(hours=25))


def test_other_scheduled_sources_keep_their_gap_alerts(monkeypatch):
    monkeypatch.delenv("HERMES_SOURCE_COLLECTION_MODE", raising=False)
    health = {**old_health(), "fred": {"last_success": (NOW - timedelta(hours=80)).isoformat()}}
    assert [row["source"] for row in collector.stale_sources(health, now=NOW)] == ["fred"]
