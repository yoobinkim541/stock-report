from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


def _sleeping_provider_worker():
    pid_path = Path(os.environ["SOURCE_PIPELINE_TEST_PID_FILE"])
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(5)
    return [{"source": "timeout-test", "title": "unreachable", "url": "https://e/timeout"}]


def _healthy_subprocess_worker():
    return [{"source": "sibling-test", "title": "ok", "url": "https://e/sibling"}]


def _partial_failure_worker():
    from reports import source_collector as collector

    events = [{"source": "partial:ok", "title": "ok", "url": "https://e/partial-ok"}]
    if os.environ.get("SOURCE_PIPELINE_TEST_FEED_DOWN") == "1":
        collector._note_error("partial:down", "feed down")
    else:
        events.append({"source": "partial:down", "title": "back", "url": "https://e/partial-down"})
    return events


def _unavailable_after_note_worker():
    from reports import source_collector as collector

    collector._SOURCE_AVAILABILITY["noted-unavailable"] = {
        "availability": "unavailable",
        "availability_reason": "3 of 3 symbols failed",
    }
    raise collector.SourceProviderUnavailable("every symbol failed")


def _http_error(status: int) -> requests.HTTPError:
    response = type("Response", (), {"status_code": status})()
    error = requests.HTTPError(f"HTTP {status}")
    error.response = response
    return error


def test_run_providers_selects_group_and_updates_only_attempted_sources(tmp_path):
    from reports.source_pipeline import ProviderSpec, run_providers

    registry = [
        ProviderSpec("news-a", ("saveticker",), "news", lambda: [{"source": "saveticker", "title": "n", "url": "https://e/n"}]),
        ProviderSpec("macro-a", ("fred",), "macro", lambda: [{"source": "fred", "title": "m", "url": "https://e/m", "type": "macro_snapshot", "metrics": {"series_id": "DGS10"}}]),
    ]

    result = run_providers(registry=registry, group="news", cache_dir=tmp_path / "cache")

    assert result["selected"] == ["news-a"]
    assert result["providers"]["news-a"]["fetched"] == 1
    assert result["providers"]["news-a"]["persisted"] == 1
    assert "macro-a" not in result["providers"]
    assert set(result["health"]) == {"saveticker"}


def test_run_providers_starts_independent_fetchers_concurrently(tmp_path):
    from reports.source_pipeline import ProviderSpec, run_providers

    barrier = threading.Barrier(2)

    def fetch(source):
        barrier.wait(timeout=1)
        return [{"source": source, "title": source, "url": f"https://e/{source}"}]

    registry = [
        ProviderSpec("one", ("saveticker",), "news", lambda: fetch("saveticker")),
        ProviderSpec("two", ("arca",), "news", lambda: fetch("arca")),
    ]

    result = run_providers(registry=registry, group="news", cache_dir=tmp_path / "cache", max_workers=2)

    assert result["providers"]["one"]["availability"] == "available"
    assert result["providers"]["two"]["availability"] == "available"


def test_run_providers_isolates_failure_and_retries_transient_error(tmp_path):
    from reports.source_pipeline import ProviderSpec, run_providers

    attempts = {"flaky": 0, "healthy": 0}

    def flaky():
        attempts["flaky"] += 1
        if attempts["flaky"] == 1:
            raise _http_error(503)
        return [{"source": "fred", "title": "macro", "url": "https://e/f", "type": "macro_snapshot", "metrics": {"series_id": "DGS10"}}]

    def healthy():
        attempts["healthy"] += 1
        return [{"source": "saveticker", "title": "news", "url": "https://e/n"}]

    registry = [
        ProviderSpec("flaky", ("fred",), "mixed", flaky, retries=1),
        ProviderSpec("healthy", ("saveticker",), "mixed", healthy),
    ]

    result = run_providers(registry=registry, group="mixed", cache_dir=tmp_path / "cache")

    assert attempts == {"flaky": 2, "healthy": 1}
    assert result["providers"]["flaky"]["fetched"] == 1
    assert result["providers"]["healthy"]["fetched"] == 1


def test_run_providers_does_not_retry_nonretryable_451(tmp_path):
    from reports.source_pipeline import ProviderSpec, run_providers

    attempts = 0

    def blocked():
        nonlocal attempts
        attempts += 1
        raise _http_error(451)

    registry = [ProviderSpec("blocked", ("polymarket",), "prediction", blocked, retries=3)]

    result = run_providers(registry=registry, group="prediction", cache_dir=tmp_path / "cache")

    assert attempts == 1
    assert result["providers"]["blocked"]["availability"] == "blocked"
    assert result["providers"]["blocked"]["status_code"] == 451
    assert result["health"]["polymarket"]["availability"] == "blocked"


def test_prediction_group_is_degraded_but_successful_when_alternative_provider_works(tmp_path):
    from reports import source_collector
    from reports.source_pipeline import ProviderSpec, run_providers

    def swallowed_block():
        source_collector._SOURCE_AVAILABILITY["polymarket"] = {
            "availability": "blocked",
            "availability_reason": "HTTP 451",
        }
        return []

    registry = [
        ProviderSpec("polymarket", ("polymarket",), "prediction", swallowed_block),
        ProviderSpec("kalshi", ("kalshi",), "prediction", lambda: [{
            "source": "kalshi",
            "title": "Fed rate cut: Yes 55%",
            "url": "https://kalshi.com/markets/fed",
            "type": "prediction_market",
            "record_kind": "observation",
            "entity_id": "kalshi:FED",
            "metrics": {"yes_probability": 0.55},
        }]),
    ]

    result = run_providers(registry=registry, group="prediction", cache_dir=tmp_path / "cache")

    assert result["ok"] is True
    assert result["availability"] == "degraded"
    assert result["providers"]["polymarket"]["availability"] == "blocked"
    assert result["providers"]["polymarket"]["error"] == "HTTP 451"
    assert result["providers"]["kalshi"]["availability"] == "available"


def test_group_fails_when_no_provider_is_available(tmp_path):
    from reports.source_pipeline import ProviderSpec, run_providers

    registry = [
        ProviderSpec("blocked", ("polymarket",), "prediction", lambda: (_ for _ in ()).throw(_http_error(451))),
        ProviderSpec("failed", ("kalshi",), "prediction", lambda: (_ for _ in ()).throw(_http_error(503))),
    ]

    result = run_providers(registry=registry, group="prediction", cache_dir=tmp_path / "cache")

    assert result["ok"] is False
    assert result["availability"] == "unavailable"


def test_run_providers_can_select_one_provider_by_name(tmp_path):
    from reports.source_pipeline import ProviderSpec, run_providers

    registry = [
        ProviderSpec("polymarket", ("polymarket",), "prediction", lambda: []),
        ProviderSpec("kalshi", ("kalshi",), "prediction", lambda: []),
    ]

    result = run_providers(registry=registry, sources=["kalshi"], cache_dir=tmp_path / "cache")

    assert result["selected"] == ["kalshi"]


def test_provider_timeout_terminates_child_and_keeps_sibling_result(tmp_path, monkeypatch):
    from reports.source_pipeline import ProviderSpec, run_providers
    from reports.source_runs import load_source_runs

    pid_path = tmp_path / "timed-out-worker.pid"
    monkeypatch.setenv("SOURCE_PIPELINE_TEST_PID_FILE", str(pid_path))
    started = time.monotonic()
    registry = [
        ProviderSpec(
            "slow-timeout",
            ("timeout-test",),
            "timeout-test",
            _sleeping_provider_worker,
            timeout_seconds=2,
        ),
        ProviderSpec(
            "healthy-sibling",
            ("sibling-test",),
            "timeout-test",
            _healthy_subprocess_worker,
            timeout_seconds=3,
        ),
    ]

    result = run_providers(
        registry=registry,
        group="timeout-test",
        cache_dir=tmp_path / "cache",
        max_workers=2,
    )

    assert time.monotonic() - started < 3.5
    timed_out = result["providers"]["slow-timeout"]
    assert timed_out["availability"] == "timeout", timed_out
    assert timed_out["timed_out"] is True
    assert timed_out["terminated"] is True
    assert "deadline" in timed_out["error"]
    assert result["providers"]["healthy-sibling"]["availability"] == "available"
    assert result["providers"]["healthy-sibling"]["fetched"] == 1

    worker_pid = int(pid_path.read_text(encoding="utf-8"))
    try:
        os.kill(worker_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("timed-out provider child is still alive")

    manifests = load_source_runs(tmp_path / "cache", hours=1)
    manifest_by_provider = {row["provider"]: row for row in manifests}
    assert manifest_by_provider["slow-timeout"]["availability"] == "timeout"
    assert manifest_by_provider["slow-timeout"]["timed_out"] is True
    assert manifest_by_provider["healthy-sibling"]["availability"] == "available"



def test_subprocess_partial_failure_marks_only_the_failed_source(tmp_path, monkeypatch):
    from reports import source_collector
    from reports.source_pipeline import ProviderSpec, run_providers

    monkeypatch.setattr(source_collector, "_LAST_ERRORS", {"partial:ok": "stale parent note"})
    monkeypatch.setattr(source_collector, "_SOURCE_AVAILABILITY", {})
    registry = [
        ProviderSpec(
            "partial",
            ("partial:ok", "partial:down"),
            "partial-test",
            _partial_failure_worker,
            timeout_seconds=20,
        )
    ]

    monkeypatch.setenv("SOURCE_PIPELINE_TEST_FEED_DOWN", "1")
    degraded = run_providers(registry=registry, group="partial-test", cache_dir=tmp_path / "cache")

    assert degraded["providers"]["partial"]["transport"] == "subprocess"
    assert degraded["providers"]["partial"]["fetched"] == 1
    down = degraded["health"]["partial:down"]
    assert down["fetch_success"] is False
    assert down["last_error"] == "feed down"
    healthy = degraded["health"]["partial:ok"]
    assert healthy["fetch_success"] is True
    assert "last_error" not in healthy

    monkeypatch.setenv("SOURCE_PIPELINE_TEST_FEED_DOWN", "0")
    recovered = run_providers(registry=registry, group="partial-test", cache_dir=tmp_path / "cache")

    assert recovered["providers"]["partial"]["fetched"] == 2
    assert recovered["health"]["partial:down"]["fetch_success"] is True
    assert "last_error" not in recovered["health"]["partial:down"]


def test_subprocess_failure_keeps_availability_noted_before_raise(tmp_path, monkeypatch):
    from reports import source_collector
    from reports.source_pipeline import ProviderSpec, run_providers

    monkeypatch.setattr(source_collector, "_LAST_ERRORS", {})
    monkeypatch.setattr(source_collector, "_SOURCE_AVAILABILITY", {})
    registry = [
        ProviderSpec(
            "noted",
            ("noted-unavailable",),
            "noted-test",
            _unavailable_after_note_worker,
            timeout_seconds=20,
        )
    ]

    result = run_providers(registry=registry, group="noted-test", cache_dir=tmp_path / "cache")

    assert result["providers"]["noted"]["transport"] == "subprocess"
    record = result["health"]["noted-unavailable"]
    assert record["availability"] == "unavailable"
    assert record["availability_reason"] == "3 of 3 symbols failed"
    assert record["fetch_success"] is False
