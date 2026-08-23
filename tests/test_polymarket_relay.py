from __future__ import annotations

import os


class _Response:
    def __init__(self, payload=None, *, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            error = requests.HTTPError(f"{self.status_code} upstream")
            error.response = self
            raise error

    def json(self):
        return self._payload


def test_fetch_events_uses_fixed_upstream_and_clamps_limit():
    from deploy.polymarket_relay.api import index

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response([{"id": "event-1", "title": "Fed decision"}])

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


def test_health_route_does_not_require_token():
    from deploy.polymarket_relay.api import index

    response = index.app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert "time" in response.get_json()
