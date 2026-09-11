"""Durable, bounded metadata queue for article crawling.

The index intentionally contains metadata only. Cleaned article bodies are
content-addressed files below ``bodies/`` so an interrupted refresh cannot
replace the last readable version before its metadata pointer is committed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import safe_io
from reports import raw_archive


ARTICLE_CACHE_ENV = "ARTICLE_CACHE_DIR"
MAX_INDEX_RECORDS = 10_000
MAX_EVENT_IDS = 50
COMPLETED_RETENTION = timedelta(days=14)
REVALIDATE_AFTER = timedelta(hours=24)
TERMINAL_STATUSES = {"ready", "failed", "unavailable", "needs_browser", "blocked", "capacity"}
TRACKING_PARAMETERS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "mc_cid", "mc_eid", "_ga", "_gl",
}


def _as_utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _as_utc(parsed)


def cache_root(root: str | Path | None = None, *, create: bool = False) -> Path:
    if root is not None:
        resolved = Path(root).expanduser()
    elif os.getenv(ARTICLE_CACHE_ENV):
        resolved = Path(os.environ[ARTICLE_CACHE_ENV]).expanduser()
    else:
        resolved = raw_archive.reports_root() / "article-cache"
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def canonicalize_url(url: object) -> str:
    """Return a stable HTTP(S) URL without fragments or tracking parameters."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError:
        return ""
    if scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        return ""
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMETERS:
            continue
        query.append((key, value))
    return urlunsplit((scheme, host, parsed.path or "/", urlencode(query, doseq=True), ""))


def _index_path(root: str | Path | None = None) -> Path:
    return cache_root(root) / "index.json"


def _read_index_path(path: Path) -> dict[str, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def load_index(*, root: str | Path | None = None) -> dict:
    """Load a metadata-only snapshot keyed by canonical URL."""
    return copy.deepcopy(_read_index_path(_index_path(root)))


def _completion_time(record: dict) -> datetime | None:
    return _parse_time(record.get("completed_at") or record.get("fetched_at"))


def _prune_expired(index: dict[str, dict], now: datetime) -> list[dict]:
    cutoff = now - COMPLETED_RETENTION
    removed: list[dict] = []
    for url, record in list(index.items()):
        completed = _completion_time(record)
        if record.get("status") in TERMINAL_STATUSES and completed and completed < cutoff:
            removed.append(index.pop(url))
    return removed


@contextmanager
def locked_index(
    *, root: str | Path | None = None, now: datetime | None = None
) -> Iterator[tuple[Path, dict[str, dict], list[dict]]]:
    """Run a short metadata read-modify-write transaction."""
    root_path = cache_root(root, create=True)
    path = root_path / "index.json"
    with safe_io.file_write_lock(str(path)):
        index = _read_index_path(path)
        pruned = _prune_expired(index, _as_utc(now))
        yield root_path, index, pruned
        safe_io.atomic_write_json(str(path), index)


def _merge_bounded_id(existing: list, candidate_id: object) -> list[str]:
    values = [str(value) for value in existing if str(value or "").strip()]
    candidate = str(candidate_id or "").strip()
    if candidate and candidate not in values:
        values.append(candidate)
    return values[-MAX_EVENT_IDS:]


def _evidence_id(event: dict) -> str:
    from reports.evidence_cards import _id_for

    return _id_for(event)


def _new_record(event: dict, url: str, now: datetime) -> dict:
    stamp = _iso(now)
    return {
        "url": url,
        "title": str(event.get("title") or "").strip(),
        "source": str(event.get("source") or "").strip(),
        "published_at": str(event.get("published_at") or event.get("created_at") or "").strip(),
        "status": "pending",
        "attempts": 0,
        "next_attempt_at": stamp,
        "last_error": "",
        "content_hash": None,
        "body_path": None,
        "event_ids": _merge_bounded_id([], event.get("id")),
        "evidence_ids": _merge_bounded_id([], _evidence_id(event)),
        "first_discovered_at": stamp,
        "last_discovered_at": stamp,
        "fetched_at": None,
        "completed_at": None,
        "refresh_pending": False,
        "claim_id": None,
        "claim_expires_at": None,
    }


def _merge_discovery(record: dict, event: dict, now: datetime) -> bool:
    changed = False
    for field, candidates in {
        "title": ("title",), "source": ("source",), "published_at": ("published_at", "created_at"),
    }.items():
        incoming = ""
        for candidate in candidates:
            incoming = str(event.get(candidate) or "").strip()
            if incoming:
                break
        if incoming and incoming != str(record.get(field) or ""):
            record[field] = incoming
            changed = True
    record["event_ids"] = _merge_bounded_id(record.get("event_ids") or [], event.get("id"))
    record["evidence_ids"] = _merge_bounded_id(
        record.get("evidence_ids") or [], _evidence_id(event)
    )
    record["last_discovered_at"] = _iso(now)
    return changed


def _schedule_rediscovery(record: dict, *, changed: bool, now: datetime) -> None:
    status = record.get("status")
    if status == "failed" and changed:
        record.update(
            status="pending", attempts=0, next_attempt_at=_iso(now), last_error="", completed_at=None,
            claim_id=None, claim_expires_at=None,
        )
        return
    completed = _completion_time(record)
    if status == "ready" and completed and now - completed >= REVALIDATE_AFTER:
        record.update(refresh_pending=True, attempts=0, next_attempt_at=_iso(now), last_error="")
    elif status in {"unavailable", "needs_browser", "capacity"} and completed and now - completed >= REVALIDATE_AFTER:
        record.update(
            status="pending", attempts=0, next_attempt_at=_iso(now), last_error="", completed_at=None,
            claim_id=None, claim_expires_at=None,
        )


def _counts(index: dict[str, dict], now: datetime) -> dict:
    statuses: dict[str, int] = {}
    retry_due = retry_waiting = 0
    for record in index.values():
        status = str(record.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        if status == "retry" or (status == "ready" and record.get("refresh_pending")):
            due = _parse_time(record.get("next_attempt_at"))
            if due is None or due <= now:
                retry_due += 1
            else:
                retry_waiting += 1
    return {"statuses": statuses, "retry_due": retry_due, "retry_waiting": retry_waiting}


def enqueue_events(events, *, root=None, now=None) -> dict:
    """Merge event discoveries into the bounded queue without storing bodies."""
    current = _as_utc(now)
    enqueued = updated = invalid = capacity_rejected = 0
    with locked_index(root=root, now=current) as (_root, index, pruned):
        for event in events or []:
            if not isinstance(event, dict):
                invalid += 1
                continue
            url = canonicalize_url(event.get("url"))
            if not url:
                invalid += 1
                continue
            record = index.get(url)
            if record is None:
                if len(index) >= MAX_INDEX_RECORDS:
                    capacity_rejected += 1
                    continue
                index[url] = _new_record(event, url, current)
                enqueued += 1
                continue
            changed = _merge_discovery(record, event, current)
            _schedule_rediscovery(record, changed=changed, now=current)
            updated += 1
        counts = _counts(index, current)
        used = len(index)
    return {
        "enqueued": enqueued,
        "updated": updated,
        "invalid": invalid,
        "pruned": len(pruned),
        "capacity_rejected": capacity_rejected,
        "capacity": {"limit": MAX_INDEX_RECORDS, "used": used, "available": max(0, MAX_INDEX_RECORDS - used)},
        "retry_counts": {"due": counts["retry_due"], "waiting": counts["retry_waiting"]},
    }


def body_relative_path(url: str, content_hash: str) -> str:
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return str(Path("bodies") / url_hash[:2] / url_hash / f"{content_hash}.json")


def get_article(url, *, root=None, index: dict | None = None) -> dict | None:
    """Return the current ready article body, never queue metadata or stale files."""
    canonical = canonicalize_url(url)
    if not canonical:
        return None
    record = (index if index is not None else load_index(root=root)).get(canonical)
    if not record or record.get("status") != "ready" or not record.get("content_hash"):
        return None
    relative = Path(str(record.get("body_path") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        return None
    path = cache_root(root) / relative
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("url") != canonical:
        return None
    if payload.get("content_hash") != record.get("content_hash"):
        return None
    return {
        "url": canonical,
        "title": record.get("title"),
        "source": record.get("source"),
        "published_at": record.get("published_at"),
        "fetched_at": payload.get("fetched_at"),
        "text": payload.get("text"),
        "content_hash": payload.get("content_hash"),
    }


def status_snapshot(*, root=None, now=None) -> dict:
    current = _as_utc(now)
    index = load_index(root=root)
    counts = _counts(index, current)
    used = len(index)
    return {
        "total": used,
        "statuses": counts["statuses"],
        "retry_counts": {"due": counts["retry_due"], "waiting": counts["retry_waiting"]},
        "capacity": {"limit": MAX_INDEX_RECORDS, "used": used, "available": max(0, MAX_INDEX_RECORDS - used)},
    }
