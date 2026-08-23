from __future__ import annotations

from datetime import datetime, timedelta, timezone


KST = timezone(timedelta(hours=9))


def test_economic_calendar_events_normalize_time_importance_and_source():
    from reports.operational_events import fetch_economic_calendar_events

    when = datetime(2026, 8, 22, 21, 30, tzinfo=KST)
    events = fetch_economic_calendar_events(
        days=7,
        fetcher=lambda **_kwargs: [{
            "title": "Fed Chair speech",
            "when": when,
            "date_str": "08/22 21:30",
            "importance": "high",
            "marker": "red",
            "color": "#EF4444",
        }],
    )

    assert len(events) == 1
    event = events[0]
    assert event["source"] == "economic_calendar"
    assert event["type"] == "economic_calendar"
    assert event["record_kind"] == "content"
    assert event["published_at"] == when.isoformat(timespec="seconds")
    assert event["metrics"]["importance"] == "high"
    assert event["source_url"] == "https://saveticker.com/calendar"
    assert "Fed Chair speech" in event["title"]


def test_economic_calendar_context_is_exposed_without_rule_template(monkeypatch, tmp_path):
    from agent_console import context

    row = {
        "source": "economic_calendar",
        "title": "US CPI release",
        "published_at": "2026-08-22T21:30:00+09:00",
        "url": "https://saveticker.com/calendar#cpi",
        "metrics": {"importance": "high", "scheduled_at": "2026-08-22T21:30:00+09:00"},
    }

    state = context.economic_calendar_state([row])

    assert state["count"] == 1
    assert state["items"][0]["title"] == "US CPI release"
    assert state["items"][0]["importance"] == "high"
    assert state["items"][0]["scheduled_at"] == "2026-08-22T21:30:00+09:00"


def test_default_source_registry_includes_calendar_group():
    from reports.source_pipeline import default_registry

    specs = {spec.name: spec for spec in default_registry()}

    assert specs["economic_calendar"].group == "calendar"
    assert specs["economic_calendar"].sources == ("economic_calendar",)
