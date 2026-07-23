#!/usr/bin/env python3
"""Filesystem helpers for raw source artifacts and derived text.

Raw sources are stored under ``~/reports/raw`` (or ``STOCK_REPORT_REPORTS_DIR/raw``)
and derived text under ``~/reports/text``.  Raw originals expire by TTL, but
derived text remains available for wiki and memory layers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
REPORTS_DIR_ENV = "STOCK_REPORT_REPORTS_DIR"
DEFAULT_REPORTS_DIR = Path.home() / "reports"
DEFAULT_RAW_TTL_DAYS = 30
RAW_TTL_DAYS_BY_SOURCE = {
    "saveticker_report_pdf": 180,
    "saveticker_article": 60,
    "saveticker": 60,
    "telegram": 14,
    "arca": 7,
    "yahoo_finance": 30,
    "fred": 30,
    "worldgovernmentbonds": 30,
}


def reports_root() -> Path:
    return Path(os.getenv(REPORTS_DIR_ENV, str(DEFAULT_REPORTS_DIR))).expanduser()


def raw_root() -> Path:
    root = reports_root() / "raw"
    root.mkdir(parents=True, exist_ok=True)
    return root


def text_root() -> Path:
    root = reports_root() / "text"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt


def _date_parts(dt: datetime) -> tuple[str, str, str]:
    kst = _ensure_tz(dt).astimezone(KST)
    return f"{kst:%Y}", f"{kst:%m}", f"{kst:%d}"


def _stamp(dt: datetime) -> str:
    return _ensure_tz(dt).astimezone(KST).strftime("%Y%m%d-%H%M%S")


def _slugify(text: str, limit: int = 80) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", (text or "").strip()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:limit].strip("-") or "artifact"


def _artifact_dir(source: str, fetched_at: datetime, *, base: Path | None = None) -> Path:
    y, m, d = _date_parts(fetched_at)
    root = (base or raw_root()) / source / y / m / d
    root.mkdir(parents=True, exist_ok=True)
    return root


def _text_dir(source: str, fetched_at: datetime, *, base: Path | None = None) -> Path:
    y, m, d = _date_parts(fetched_at)
    root = (base or text_root()) / source / y / m / d
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported value for json serialization: {type(value)!r}")


def _normalize_source(source: str) -> str:
    return str(source or "").strip().lower().split(":", 1)[0]


def resolve_raw_ttl_days(source: str, *, kind: str | None = None, ttl_days: int | None = None) -> int:
    if ttl_days is not None:
        return max(1, int(ttl_days))
    root = _normalize_source(source)
    if root.startswith("telegram"):
        return RAW_TTL_DAYS_BY_SOURCE.get("telegram", DEFAULT_RAW_TTL_DAYS)
    if root in RAW_TTL_DAYS_BY_SOURCE:
        return RAW_TTL_DAYS_BY_SOURCE[root]
    if kind and str(kind).lower() == "pdf" and root.startswith("saveticker"):
        return RAW_TTL_DAYS_BY_SOURCE["saveticker_report_pdf"]
    return DEFAULT_RAW_TTL_DAYS


def save_raw_artifact(
    source: str,
    kind: str,
    fetched_at: datetime,
    title: str,
    url: str,
    payload: bytes | str,
    suffix: str,
    ttl_days: int | None = None,
) -> dict:
    fetched_at = _ensure_tz(fetched_at)
    data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    digest = _sha256_bytes(data)
    stamp = _stamp(fetched_at)
    slug = _slugify(title)
    base_name = f"{stamp}-{slug}-{digest[:12]}"
    resolved_ttl_days = resolve_raw_ttl_days(source, kind=kind, ttl_days=ttl_days)

    raw_dir = _artifact_dir(source, fetched_at)
    text_dir_ = _text_dir(source, fetched_at)
    raw_path = raw_dir / f"{base_name}{suffix}"
    manifest_path = raw_dir / f"{base_name}.manifest.json"
    text_path = text_dir_ / f"{base_name}.txt"
    expires_at = (fetched_at + timedelta(days=resolved_ttl_days)).astimezone(KST)

    raw_path.write_bytes(data)
    record = {
        "source": source,
        "kind": kind,
        "title": title,
        "url": url,
        "source_url": url,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "ttl_days": resolved_ttl_days,
        "raw_path": str(raw_path),
        "text_path": str(text_path),
        "manifest_path": str(manifest_path),
        "content_type": kind,
        "suffix": suffix,
        "sha256": digest,
    }
    manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return record


def save_extracted_text(raw_record: dict, text: str) -> dict:
    text_path = Path(raw_record["text_path"])
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text or "", encoding="utf-8")
    record = dict(raw_record)
    record["text_path"] = str(text_path)
    manifest_path = Path(raw_record["manifest_path"])
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = dict(raw_record)
        manifest["text_path"] = str(text_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return record


def _load_manifest(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cleanup_expired_raw_artifacts(now: datetime | None = None, ttl_days: int = 30) -> dict:
    now = _ensure_tz(now or datetime.now(KST))
    deleted_raw = 0
    deleted_manifests = 0
    scanned = 0
    root = raw_root()
    for manifest_path in root.rglob("*.json"):
        scanned += 1
        manifest = _load_manifest(manifest_path)
        if not manifest:
            continue
        expires_at = manifest.get("expires_at")
        try:
            expires_dt = datetime.fromisoformat(expires_at)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=KST)
        except Exception:
            continue
        if expires_dt > now:
            continue
        raw_path = Path(manifest.get("raw_path") or "")
        if raw_path.exists():
            raw_path.unlink()
            deleted_raw += 1
        if manifest_path.exists():
            manifest_path.unlink()
            deleted_manifests += 1
    return {
        "scanned": scanned,
        "deleted_raw": deleted_raw,
        "deleted_manifests": deleted_manifests,
        "ttl_days": ttl_days,
    }
