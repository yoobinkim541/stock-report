from __future__ import annotations

import hashlib
from datetime import datetime, timezone

MUTABLE_EVENT_TYPES = {
    "market_snapshot",
    "macro_snapshot",
    "prediction_market",
    "economic_calendar_snapshot",
    "snapshot",
}

NATIVE_ID_KEYS = (
    "market_id",
    "market_ticker",
    "series_id",
    "ticker",
    "symbol",
    "maturity",
    "event_id",
)


def _hash(value: object) -> str:
    return hashlib.sha256(str(value).strip().lower().encode("utf-8")).hexdigest()[:16]


def legacy_content_id(event: dict) -> str:
    key = event.get("url") or f"{event.get('source', '')}:{event.get('title', '')}"
    return _hash(key)


def _parse_observed_at(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            parsed = fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _observation_entity(event: dict) -> str:
    explicit = str(event.get("entity_id") or "").strip()
    if explicit:
        return explicit
    source = str(event.get("source") or "unknown").strip().lower() or "unknown"
    metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
    for key in NATIVE_ID_KEYS:
        value = metrics.get(key)
        if value not in (None, ""):
            return f"{source}:{str(value).strip()}"
    url = str(event.get("url") or event.get("source_url") or "").strip()
    if url:
        return f"{source}:{_hash(url)}"
    return f"{source}:{_hash(event.get('title') or source)}"


def _record_kind(event: dict) -> str:
    explicit = str(event.get("record_kind") or "").strip().lower()
    if explicit in {"content", "observation"}:
        return explicit
    event_type = str(
        event.get("type")
        or (event.get("classification") or {}).get("kind")
        or ""
    ).strip().lower()
    return "observation" if event_type in MUTABLE_EVENT_TYPES else "content"


def normalize_event_identity(
    event: dict,
    observed_at: datetime,
    bucket_minutes: int = 30,
) -> dict:
    row = dict(event or {})
    observed = _parse_observed_at(row.get("observed_at"), observed_at)
    row["observed_at"] = observed.isoformat(timespec="seconds")
    kind = _record_kind(row)
    row["record_kind"] = kind

    if kind == "content":
        content_id = legacy_content_id(row)
        row["content_id"] = content_id
        row["entity_id"] = str(row.get("entity_id") or f"{row.get('source', 'unknown')}:{content_id}")
        row["observation_bucket"] = ""
        row["id"] = content_id
        return row

    bucket_minutes = max(1, min(int(bucket_minutes or 30), 1440))
    utc = observed.astimezone(timezone.utc)
    minute = utc.minute - (utc.minute % bucket_minutes)
    bucket = utc.replace(minute=minute, second=0, microsecond=0)
    entity_id = _observation_entity(row)
    bucket_text = bucket.isoformat(timespec="seconds")
    row["content_id"] = ""
    row["entity_id"] = entity_id
    row["observation_bucket"] = bucket_text
    row["id"] = _hash(f"{entity_id}|{bucket_text}")
    return row

