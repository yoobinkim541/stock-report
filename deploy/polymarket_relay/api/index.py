from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone
from typing import Callable

import requests
from flask import Flask, jsonify, request

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
MAX_EVENTS = 200
UPSTREAM_TIMEOUT_SECONDS = 15

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = MAX_EVENTS
    return max(1, min(parsed, MAX_EVENTS))


def fetch_events(
    *,
    limit: int = MAX_EVENTS,
    get: Callable = requests.get,
) -> tuple[dict, int]:
    """Fetch the one allowlisted, public Polymarket discovery endpoint."""
    params = {
        "active": True,
        "closed": False,
        "archived": False,
        "limit": _safe_limit(limit),
        "order": "volume",
        "ascending": False,
    }
    retrieved_at = _utc_now()
    try:
        response = get(
            GAMMA_EVENTS_URL,
            params=params,
            headers={"User-Agent": "stock-report-readonly-relay/1.0"},
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Polymarket events response must be a list")
    except requests.HTTPError as exc:
        status = int(getattr(getattr(exc, "response", None), "status_code", 502) or 502)
        blocked = status == 451
        return {
            "ok": False,
            "source_url": GAMMA_EVENTS_URL,
            "retrieved_at": retrieved_at,
            "transport": "polymarket-relay",
            "availability": "blocked" if blocked else "error",
            "upstream_status": status,
            "error": "upstream unavailable for legal reasons" if blocked else "upstream HTTP error",
            "events": [],
        }, 451 if blocked else 502
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {
            "ok": False,
            "source_url": GAMMA_EVENTS_URL,
            "retrieved_at": retrieved_at,
            "transport": "polymarket-relay",
            "availability": "error",
            "upstream_status": None,
            "error": str(exc)[:240],
            "events": [],
        }, 502

    return {
        "ok": True,
        "source_url": GAMMA_EVENTS_URL,
        "retrieved_at": retrieved_at,
        "transport": "polymarket-relay",
        "events": payload[:MAX_EVENTS],
    }, 200


def _authorized() -> bool:
    token = (os.getenv("POLYMARKET_RELAY_TOKEN") or "").strip()
    supplied = request.headers.get("Authorization", "")
    return bool(token) and hmac.compare_digest(supplied, f"Bearer {token}")


@app.get("/api/events")
@app.get("/api/index")
def events_route():
    if not _authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    body, status = fetch_events(limit=_safe_limit(request.args.get("limit")))
    return jsonify(body), status


@app.get("/health")
def health_route():
    return jsonify({"ok": True, "time": _utc_now(), "upstream": GAMMA_EVENTS_URL})

