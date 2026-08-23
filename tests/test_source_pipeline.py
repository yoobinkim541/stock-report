from __future__ import annotations

import threading
from datetime import datetime, timezone

import requests


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
