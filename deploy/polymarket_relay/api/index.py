from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable

import requests
from flask import Flask, jsonify, request

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
MAX_EVENTS = 200
UPSTREAM_TIMEOUT_SECONDS = 15
MAX_UPSTREAM_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = 60
_RATE_BUCKETS: dict[str, list[float]] = {}
_AUDIT_LOG = logging.getLogger("polymarket_relay.audit")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


class _UpstreamResponseTooLarge(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_limit(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = MAX_EVENTS
    return max(1, min(parsed, MAX_EVENTS))


def _error_body(code: str, message: str, **extra: object) -> dict:
    body = {"ok": False, "error": {"code": code, "message": message}, "events": []}
    body.update(extra)
    return body


def _read_json_bounded(response) -> object:
    declared = response.headers.get("Content-Length")
    try:
        declared_size = int(declared) if declared is not None else None
    except (TypeError, ValueError):
        declared_size = None
    if declared_size is not None and declared_size > MAX_UPSTREAM_BYTES:
        raise _UpstreamResponseTooLarge
    size = 0
    chunks = []
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > MAX_UPSTREAM_BYTES:
            raise _UpstreamResponseTooLarge
        chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


_EVENT_FIELDS = (
    "id", "title", "slug", "question", "description", "category", "active", "closed",
    "archived", "volume", "liquidity", "openInterest", "endDate", "startDate",
    "publishedAt", "createdAt",
)
_MARKET_FIELDS = (
    "id", "conditionId", "slug", "question", "description", "active", "closed",
    "volume", "liquidity", "openInterest", "endDate", "startDate", "outcomePrices",
    "outcomes", "lastTradePrice", "bestBid", "bestAsk",
)


def _compact_event(event: object) -> dict:
    if not isinstance(event, dict):
        return {}
    result = {key: event[key] for key in _EVENT_FIELDS if key in event}
    tags = event.get("tags")
    if isinstance(tags, list):
        result["tags"] = [
            ({key: item[key] for key in ("id", "label", "name", "slug") if key in item}
             if isinstance(item, dict) else item)
            for item in tags
            if isinstance(item, (dict, str))
        ]
    markets = event.get("markets")
    if isinstance(markets, list):
        result["markets"] = [
            {key: market[key] for key in _MARKET_FIELDS if key in market}
            for market in markets if isinstance(market, dict)
        ]
    return result


def _compact_events(events: list[object]) -> list[dict]:
    """Keep fields consumed by the collector and cap the public response body."""
    compacted: list[dict] = []
    for event in events[:MAX_EVENTS]:
        row = _compact_event(event)
        if not row:
            continue
        candidate = compacted + [row]
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > MAX_RESPONSE_BYTES:
            break
        compacted.append(row)
    return compacted


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
            stream=True,
        )
        response.raise_for_status()
        payload = _read_json_bounded(response)
        if not isinstance(payload, list):
            raise ValueError("Polymarket events response must be a list")
    except _UpstreamResponseTooLarge:
        return _error_body(
            "upstream_response_too_large",
            "upstream response exceeds relay size limit",
            source_url=GAMMA_EVENTS_URL,
            retrieved_at=retrieved_at,
            transport="polymarket-relay",
            availability="error",
        ), 502
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
    finally:
        try:
            response.close()
        except (AttributeError, UnboundLocalError):
            pass

    return {
        "ok": True,
        "source_url": GAMMA_EVENTS_URL,
        "retrieved_at": retrieved_at,
        "transport": "polymarket-relay",
        "events": _compact_events(payload),
    }, 200


def _authorized() -> bool:
    token = (os.getenv("POLYMARKET_RELAY_TOKEN") or "").strip()
    supplied = request.headers.get("Authorization", "")
    return bool(token) and hmac.compare_digest(supplied, f"Bearer {token}")


def _rate_key() -> str:
    supplied = request.headers.get("Authorization", "")
    return hashlib.sha256(supplied.encode("utf-8")).hexdigest()


def _rate_limited() -> bool:
    now = time.monotonic()
    key = _rate_key()
    bucket = [ts for ts in _RATE_BUCKETS.get(key, [])
              if now - ts < RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= max(1, int(RATE_LIMIT_MAX_REQUESTS)):
        _RATE_BUCKETS[key] = bucket
        return True
    bucket.append(now)
    _RATE_BUCKETS[key] = bucket
    return False


def _audit(outcome: str, status: int) -> None:
    _AUDIT_LOG.info("relay_request outcome=%s status=%d", outcome, status)


@app.get("/api/events")
@app.get("/api/index")
def events_route():
    if not _authorized():
        _audit("unauthorized", 401)
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    unsupported = sorted(set(request.args) - {"limit"})
    if unsupported:
        _audit("unsupported_query", 400)
        return jsonify(_error_body("unsupported_query", "only limit is supported")), 400
    if _rate_limited():
        _audit("rate_limited", 429)
        response = jsonify(_error_body("rate_limited", "relay request rate limit exceeded"))
        response.headers["Retry-After"] = str(int(RATE_LIMIT_WINDOW_SECONDS))
        return response, 429
    body, status = fetch_events(limit=_safe_limit(request.args.get("limit")))
    _audit("success" if status < 400 else "upstream_error", status)
    return jsonify(body), status


@app.get("/health")
def health_route():
    return jsonify({"ok": True, "time": _utc_now(), "upstream": GAMMA_EVENTS_URL})


@app.after_request
def add_security_headers(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response
