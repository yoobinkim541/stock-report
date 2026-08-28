from __future__ import annotations

import json
import logging

import pytest


class _Response:
    def __init__(self, payload=None, *, status_code=200, text="", headers=None, chunks=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        encoded = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.headers = {"Content-Length": str(len(encoded))} if headers is None else headers
        self._chunks = [encoded] if chunks is None else chunks
        self.iterated = False
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            error = requests.HTTPError(f"{self.status_code} upstream")
            error.response = self
            raise error

    def json(self):
        return self._payload

    def iter_content(self, *, chunk_size):
        self.iterated = True
        yield from self._chunks

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    from deploy.polymarket_relay.api import index

    if hasattr(index, "_RATE_BUCKETS"):
        index._RATE_BUCKETS.clear()
    yield
    if hasattr(index, "_RATE_BUCKETS"):
        index._RATE_BUCKETS.clear()


def test_fetch_events_uses_fixed_upstream_and_clamps_limit():
    from deploy.polymarket_relay.api import index

    calls = []
    upstream = _Response([{"id": "event-1", "title": "Fed decision"}])

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return upstream

    body, status = index.fetch_events(limit=9999, get=fake_get)

    assert status == 200
    assert body["ok"] is True
    assert body["transport"] == "polymarket-relay"
    assert body["events"] == [{"id": "event-1", "title": "Fed decision"}]
    assert calls[0][0] == "https://gamma-api.polymarket.com/events"
    assert calls[0][1]["params"] == {
        "active": True,
        "closed": False,
        "archived": False,
        "limit": 200,
        "order": "volume",
        "ascending": False,
    }
    assert calls[0][1]["stream"] is True
    assert upstream.iterated is True
    assert upstream.closed is True


def test_fetch_events_rejects_oversized_declared_content_length():
    from deploy.polymarket_relay.api import index

    upstream = _Response(
        [{"id": "event-1"}],
        headers={"Content-Length": str(index.MAX_UPSTREAM_BYTES + 1)},
    )

    body, status = index.fetch_events(get=lambda _url, **_kwargs: upstream)

    assert status == 502
    assert body["error"] == {
        "code": "upstream_response_too_large",
        "message": "upstream response exceeds relay size limit",
    }
    assert body["events"] == []
    assert upstream.iterated is False
    assert upstream.closed is True


def test_fetch_events_rejects_oversized_stream_without_content_length():
    from deploy.polymarket_relay.api import index

    secret_body = b"raw-secret-body:" + (b"x" * index.MAX_UPSTREAM_BYTES)
    upstream = _Response(
        [{"id": "event-1"}],
        headers={},
        chunks=[secret_body],
    )

    body, status = index.fetch_events(get=lambda _url, **_kwargs: upstream)

    assert status == 502
    assert body["error"]["code"] == "upstream_response_too_large"
    assert "raw-secret-body" not in json.dumps(body)
    assert upstream.iterated is True
    assert upstream.closed is True


def test_fetch_events_reports_upstream_451_as_blocked():
    from deploy.polymarket_relay.api import index

    def blocked_get(_url, **_kwargs):
        return _Response(status_code=451, text="Unavailable For Legal Reasons")

    body, status = index.fetch_events(limit=20, get=blocked_get)

    assert status == 451
    assert body["ok"] is False
    assert body["availability"] == "blocked"
    assert body["upstream_status"] == 451
    assert body["events"] == []


def test_events_route_requires_dedicated_bearer_token(monkeypatch):
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-secret")
    from deploy.polymarket_relay.api import index

    client = index.app.test_client()
    unauthorized = client.get("/api/events")

    assert unauthorized.status_code == 401
    assert unauthorized.get_json() == {"error": "unauthorized", "ok": False}


def test_events_route_returns_authenticated_envelope(monkeypatch):
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-secret")
    from deploy.polymarket_relay.api import index

    monkeypatch.setattr(
        index,
        "fetch_events",
        lambda **_kwargs: (
            {
                "ok": True,
                "source_url": index.GAMMA_EVENTS_URL,
                "retrieved_at": "2026-08-21T14:00:00+00:00",
                "transport": "polymarket-relay",
                "events": [{"id": "event-2"}],
            },
            200,
        ),
    )
    client = index.app.test_client()
    response = client.get(
        "/api/events?limit=40",
        headers={"Authorization": "Bearer relay-secret"},
    )

    assert response.status_code == 200
    assert response.get_json()["events"] == [{"id": "event-2"}]


def test_events_route_rejects_user_supplied_upstream_params(monkeypatch):
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-secret")
    from deploy.polymarket_relay.api import index

    calls = []
    monkeypatch.setattr(index, "fetch_events", lambda **kwargs: calls.append(kwargs) or ({"ok": True}, 200))

    response = index.app.test_client().get(
        "/api/events?url=https://attacker.example/events&order=createdAt&limit=10",
        headers={"Authorization": "Bearer relay-secret"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unsupported_query"
    assert calls == []


def test_events_route_applies_best_effort_rate_limit_per_token(monkeypatch):
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-secret")
    from deploy.polymarket_relay.api import index

    monkeypatch.setattr(index, "RATE_LIMIT_MAX_REQUESTS", 1, raising=False)
    monkeypatch.setattr(index, "fetch_events", lambda **_kwargs: ({"ok": True, "events": []}, 200))
    client = index.app.test_client()
    headers = {"Authorization": "Bearer relay-secret"}

    first = client.get("/api/events", headers=headers)
    second = client.get("/api/events", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json()["error"]["code"] == "rate_limited"
    assert second.headers["Retry-After"] == "60"


def test_events_route_audit_log_excludes_secret_ip_query_and_body(monkeypatch, caplog):
    monkeypatch.setenv("POLYMARKET_RELAY_TOKEN", "relay-secret")
    from deploy.polymarket_relay.api import index

    monkeypatch.setattr(index, "fetch_events", lambda **_kwargs: ({"ok": True, "events": []}, 200))
    caplog.set_level(logging.INFO, logger="polymarket_relay.audit")

    response = index.app.test_client().get(
        "/api/events?limit=3",
        headers={"Authorization": "Bearer relay-secret"},
        environ_base={"REMOTE_ADDR": "198.51.100.42"},
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert response.status_code == 200
    assert "relay_request outcome=success status=200" in messages
    assert "relay-secret" not in messages
    assert "198.51.100.42" not in messages
    assert "limit=3" not in messages
    assert "events" not in messages


def test_health_route_does_not_require_token():
    from deploy.polymarket_relay.api import index

    response = index.app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert "time" in response.get_json()


@pytest.mark.parametrize("path", ["/health", "/api/events"])
def test_relay_responses_disable_storage_and_add_security_headers(path):
    from deploy.polymarket_relay.api import index

    response = index.app.test_client().get(path)

    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
