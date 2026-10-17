"""Atomic daily reservations for the limited public-wiki model budget."""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from safe_io import atomic_write_json, file_write_lock

SCHEMA = 1
DEFAULT_LIMIT = 20
MAX_LIMIT = 1000
KST = timezone(timedelta(hours=9))
_REVISION_RE = re.compile(r"^[0-9a-f]{64}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HASH_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def _now(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise ValueError("limit must be an integer from 1 through 1000")
    return value


def configured_limit() -> int:
    return _limit(int(os.getenv("WIKI_SOURCE_DAILY_LIMIT", str(DEFAULT_LIMIT))))


def _read(path: str | Path) -> dict:
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {"schema": SCHEMA, "days": {}}
    if (not isinstance(data, dict) or set(data) != {"schema", "days"} or
            type(data["schema"]) is not int or data["schema"] != SCHEMA):
        raise ValueError("wiki public budget schema is invalid")
    days = data["days"]
    if not isinstance(days, dict):
        raise ValueError("wiki public budget days must be an object")
    seen_ids = set()
    for day, records in days.items():
        if not isinstance(day, str) or not _DAY_RE.fullmatch(day):
            raise ValueError("wiki public budget day key is invalid")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("wiki public budget day key is invalid") from exc
        if not isinstance(records, list) or len(records) > MAX_LIMIT:
            raise ValueError("wiki public budget reservations are invalid")
        for record in records:
            if not isinstance(record, dict) or set(record) != {"id", "revision_id", "at"}:
                raise ValueError("wiki public budget reservation is invalid")
            identifier = record["id"]
            if (not isinstance(identifier, str) or
                    not (str(_valid_uuid(identifier)) == identifier or _HASH_ID_RE.fullmatch(identifier)) or
                    identifier in seen_ids):
                raise ValueError("wiki public budget reservation id is invalid")
            seen_ids.add(identifier)
            if not isinstance(record["revision_id"], str) or not _REVISION_RE.fullmatch(record["revision_id"]):
                raise ValueError("wiki public budget revision id is invalid")
            try:
                timestamp = datetime.fromisoformat(record["at"].replace("Z", "+00:00"))
                if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
                    raise ValueError()
                if timestamp.astimezone(KST).date().isoformat() != day:
                    raise ValueError()
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError("wiki public budget timestamp is invalid") from exc
    return data


def _valid_uuid(value: str) -> uuid.UUID | None:
    try:
        parsed = uuid.UUID(value)
        return parsed if str(parsed) == value else None
    except (ValueError, AttributeError, TypeError):
        return None


def reserve(path: str | Path, revision_id: str, now: datetime | None = None,
            limit: int | None = None) -> dict | None:
    """Persist one attempt before external side effects, or return None at capacity."""
    instant = _now(now) if now is not None else None
    limit = configured_limit() if limit is None else _limit(limit)
    if not isinstance(revision_id, str) or not _REVISION_RE.fullmatch(revision_id):
        raise ValueError("revision_id must be 64 lowercase hexadecimal characters")
    with file_write_lock(str(path)):
        instant = instant or _now(None)
        day = instant.astimezone(KST).date().isoformat()
        data = _read(path)
        records = data["days"].setdefault(day, [])
        if len(records) >= limit:
            return None
        reservation = {"id": str(uuid.uuid4()), "revision_id": revision_id,
                       "at": instant.isoformat()}
        records.append(reservation)
        atomic_write_json(str(path), data)
        return reservation


def status(path: str | Path, now: datetime | None = None) -> dict:
    """Read the current KST-day count without creating or modifying the state file."""
    day = _now(now).astimezone(KST).date().isoformat()
    data = _read(path)
    return {"day": day, "count": len(data["days"].get(day, [])), "limit": configured_limit()}
