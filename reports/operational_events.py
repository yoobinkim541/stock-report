from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable




def _iso(value: object) -> str:
    if isinstance(value, datetime):
        current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return current.isoformat(timespec="seconds")
    return str(value or "").strip()


def fetch_economic_calendar_events(
    days: int = 14,
    *,
    fetcher: Callable[..., list[dict]] | None = None,
) -> list[dict]:
    """Normalize upcoming macro events into the common source-event contract."""
    from providers.econ_calendar import CALENDAR_URL

    if fetcher is None:
        from providers.econ_calendar import upcoming_events

        fetcher = upcoming_events

    events: list[dict] = []
    for raw in fetcher(days=max(1, int(days))) or []:
        title = str(raw.get("title") or "").strip()
        scheduled_at = _iso(raw.get("when") or raw.get("scheduled_at"))
        if not title or not scheduled_at:
            continue
        identity = hashlib.sha256(f"{title}|{scheduled_at}".encode("utf-8")).hexdigest()[:16]
        events.append({
            "source": "economic_calendar",
            "source_url": CALENDAR_URL,
            "url": f"{CALENDAR_URL}#event-{identity}",
            "type": "economic_calendar",
            "record_kind": "content",
            "title": title,
            "body": f"{scheduled_at} 예정 경제 일정: {title}" + "".join(
                f" · {label} {raw[key]}" for key, label in (("forecast", "예상"), ("previous", "이전")) if raw.get(key)),
            "published_at": scheduled_at,
            "scheduled_at": scheduled_at,
            "tags": ["경제일정", str(raw.get("importance") or "info")],
            "metrics": {
                "scheduled_at": scheduled_at,
                "importance": str(raw.get("importance") or "info"),
                "marker": str(raw.get("marker") or ""),
                "country": str(raw.get("country") or ""),
                "forecast": str(raw.get("forecast") or ""),
                "previous": str(raw.get("previous") or ""),
            },
        })

    from reports.source_collector import _classify_event

    return [_classify_event(event) for event in events]
